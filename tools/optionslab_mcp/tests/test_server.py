from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from conftest import TICKERS, call, text

import server

HERE = Path(__file__).resolve().parents[1]

EXPECTED_TOOLS = {
    "optionslab_status", "optionslab_health", "optionslab_watchlist", "optionslab_chain", "optionslab_smile",
    "optionslab_term", "optionslab_surface", "optionslab_quality", "optionslab_paper", "optionslab_paper_equity",
    "optionslab_tracker", "optionslab_session_summary",
}  # fmt: skip

# Every tool with arguments that succeed against the fake lab.
ALL_CALLS = [
    ("optionslab_status", {}), ("optionslab_health", {}), ("optionslab_watchlist", {}),
    ("optionslab_chain", {"ticker": "SPY"}), ("optionslab_smile", {"ticker": "SPY"}),
    ("optionslab_term", {"ticker": "SPY"}), ("optionslab_surface", {"ticker": "SPY"}),
    ("optionslab_quality", {"ticker": "SPY"}), ("optionslab_paper", {}), ("optionslab_paper_equity", {}),
    ("optionslab_tracker", {}), ("optionslab_tracker", {"pid": 10}), ("optionslab_session_summary", {}),
]  # fmt: skip

# Options Lab's write endpoints (optlab/api/app.py: every route not declared with @app.get), as method + path.
WRITE_ENDPOINTS = [
    ("POST", r"/api/refresh"), ("POST", r"/api/paper/pause"), ("POST", r"/api/paper/resume"),
    ("POST", r"/api/tracker"), ("POST", r"/api/tracker/\d+/stop"), ("DELETE", r"/api/tracker/\d+"),
    ("POST", r"/api/position/template"), ("POST", r"/api/position/evaluate"),
]  # fmt: skip
# The write paths that are not also read paths (POST/DELETE /api/tracker[/{pid}] share a path with a GET).
WRITE_PATHS = ["/api/refresh", "/api/paper/pause", "/api/paper/resume", "/api/tracker/10/stop", "/api/tracker/1/stop",
               "/api/position/template", "/api/position/evaluate"]  # fmt: skip


def assert_no_write_requested(fake) -> None:
    for method, path in fake.requests:
        assert method == "GET", (method, path)
        for wm, wp in WRITE_ENDPOINTS:
            assert not (method == wm and re.fullmatch(wp, path)), (method, path)
    for url in fake.urls:  # full URL, not just the path: same host, no query string
        assert url.host == "100.89.79.67" and url.port == 8095 and not url.query, str(url)


def no_traceback(msg: str) -> None:
    assert "Traceback" not in msg and 'File "' not in msg, msg


# ------------------------------------------------------------------ tool surface
def test_exactly_the_twelve_read_tools_are_listed():
    async def go():
        return await server.mcp.list_tools()

    tools = asyncio.run(go())
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True and t.annotations.destructive_hint is False, t.name
        assert "anyOf" not in json.dumps(t.input_schema), t.name  # keep schemas simple for every LLM provider


@pytest.mark.parametrize(("name", "args"), ALL_CALLS)
def test_every_tool_succeeds_and_returns_json(fake, name, args):
    r = call(name, args)
    assert not r.is_error, text(r)
    json.loads(text(r))


# ------------------------------------------------------------------ read-only guarantee
def test_no_tool_ever_issues_a_non_get_request(fake):
    for name, args in ALL_CALLS:
        call(name, args)
    assert {m for m, _ in fake.requests} == {"GET"}, fake.requests
    for _, path in fake.requests:
        assert any(r.fullmatch(path) for r in server.READ_ROUTES), path
        assert path not in WRITE_PATHS
    assert_no_write_requested(fake)


HOSTILE_CALLS = [
    ("optionslab_chain", {"ticker": "../refresh"}), ("optionslab_smile", {"ticker": "SPY/../../paper/pause"}),
    ("optionslab_term", {"ticker": ".."}), ("optionslab_surface", {"ticker": "%2e%2e"}),
    ("optionslab_quality", {"ticker": "SPY?x=1"}), ("optionslab_tracker", {"pid": -5}),
    ("optionslab_tracker", {"pid": 10**12}), ("optionslab_status", {"path": "/api/refresh", "method": "POST"}),
]  # fmt: skip


def test_hostile_arguments_never_reach_a_write_endpoint(fake):
    for name, args in HOSTILE_CALLS:
        call(name, args)
    assert_no_write_requested(fake)


@pytest.mark.parametrize("evil", ["../REFRESH", "SPY/../../PAPER/PAUSE", "..", ".", "%2E%2E", "A?B=1", "A#B", "A\\B"])
def test_compromised_watchlist_cannot_steer_a_request(fake, evil):
    # Even if Options Lab itself listed a hostile "ticker", the read-route check is the second barrier.
    fake.data["/api/watchlist"]["rows"].append({"ticker": evil})
    r = call("optionslab_chain", {"ticker": evil})
    assert r.is_error and "Refused" in text(r), text(r)
    assert fake.requests == [("GET", "/api/watchlist")]


def test_redirect_is_not_followed(fake):
    fake.override["/api/status"] = httpx.Response(307, headers={"location": "http://evil.example/api/refresh"})
    r = call("optionslab_status")
    assert r.is_error and text(r) == "Options Lab answered /api/status with a redirect (HTTP 307); not followed."
    assert fake.requests == [("GET", "/api/status")]


def test_client_ignores_proxy_env_and_redirects():
    c = server.OptionsLab(server.DEFAULT_URL).client()
    assert c.follow_redirects is False and c.trust_env is False


@pytest.mark.parametrize(
    ("method", "url"),
    [("POST", "http://100.89.79.67:8095/api/status"), ("DELETE", "http://100.89.79.67:8095/api/tracker/1"),
     ("GET", "http://evil.example:8095/api/status"), ("GET", "http://100.89.79.67:8095/api/refresh"),
     ("GET", "http://100.89.79.67:8095/api/status?x=1")],
)  # fmt: skip
def test_request_guard_refuses_anything_but_a_read_get(method, url):
    lab = server.OptionsLab(server.DEFAULT_URL)
    with pytest.raises(server.OptionsLabError, match="not an Options Lab read request"):
        asyncio.run(lab._guard(httpx.Request(method, url)))


@pytest.mark.parametrize(
    "url",
    ["http://h:8095/api/refresh?", "http://h:8095/api/paper/pause/..", "http://user:pw@h:8095", "ftp://h:8095",
     "h:8095", "http://h:8095/x", "http://h:8095?a=1", "not a url"],
)  # fmt: skip
def test_bad_optionslab_url_is_refused_before_any_request(monkeypatch, url):
    from conftest import FakeLab

    handler = FakeLab()
    monkeypatch.setattr(server, "lab", server.OptionsLab(url, transport=httpx.MockTransport(handler)))
    r = call("optionslab_status")
    assert r.is_error and text(r).startswith("OPTIONSLAB_URL "), text(r)
    assert handler.requests == []


@pytest.mark.parametrize("url", ["http://100.89.79.67:8095", "http://100.89.79.67:8095/", "https://lab.example"])
def test_good_optionslab_urls_accepted(url):
    assert server.OptionsLab(url).config_error is None


@pytest.mark.parametrize(
    "path",
    [*WRITE_PATHS, "/api/tracker/10/../stop", "/api/chain/SPY/../../refresh", "/api/chain/..", "/api/term/.", "/"],
)
def test_get_refuses_anything_but_read_routes(fake, path):
    with pytest.raises(server.OptionsLabError, match="not an Options Lab read endpoint"):
        asyncio.run(server.lab._get(path))
    assert fake.requests == []  # refused before any network I/O


def test_read_routes_match_no_write_endpoint():
    for p in WRITE_PATHS:
        assert not any(r.fullmatch(p) for r in server.READ_ROUTES), p


def test_source_contains_no_non_get_http_call():
    # A tripwire, not a proof: the proof is the recorded requests above plus _guard. This catches the obvious
    # ways a future edit could add a second HTTP call site or a non-GET method.
    src = (HERE / "server.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)  # docstrings may *name* the write endpoints
    for pat in (
        r"\.post\(",
        r"\.put\(",
        r"\.patch\(",
        r"\.delete\(",
        r"\.request\(",
        r"\.stream\(",
        r"\.send\(",
        r"['\"](POST|PUT|PATCH|DELETE)['\"]",
        r"urllib",
        r"requests\b",
        r"build_request",
        r"httpx\.Request\(",
        r"http\.client",
        r"\bsocket\b",
        r"subprocess",
        r"getattr\(",
    ):
        assert not re.search(pat, code), pat
    assert len(re.findall(r"client\.get\(", code)) == 1  # the single HTTP call site, inside OptionsLab._get


# ------------------------------------------------------------------ ticker validation
def test_unknown_ticker_is_rejected_without_calling_its_endpoint(fake):
    r = call("optionslab_chain", {"ticker": "ZZZ"})
    assert r.is_error
    msg = text(r)
    assert "Unknown ticker 'ZZZ'" in msg and all(t in msg for t in TICKERS)
    no_traceback(msg)
    assert fake.requests == [("GET", "/api/watchlist")]


@pytest.mark.parametrize(
    "tool", ["optionslab_chain", "optionslab_smile", "optionslab_term", "optionslab_surface", "optionslab_quality"]
)
@pytest.mark.parametrize("bad", ["SPY/../refresh", "../paper/pause", "", "SPY?x=1"])
def test_path_tricks_in_ticker_are_rejected(fake, tool, bad):
    r = call(tool, {"ticker": bad})
    assert r.is_error and "Unknown ticker" in text(r)
    assert all(p == "/api/watchlist" for _, p in fake.requests)


def test_ticker_is_case_and_space_insensitive(fake):
    r = call("optionslab_term", {"ticker": " spy "})
    assert not r.is_error
    assert ("GET", "/api/term/SPY") in fake.requests


def test_watchlist_is_cached_between_validations(fake):
    call("optionslab_term", {"ticker": "SPY"})
    call("optionslab_smile", {"ticker": "SPY"})
    assert [p for _, p in fake.requests].count("/api/watchlist") == 1


def test_negative_pid_rejected(fake):
    r = call("optionslab_tracker", {"pid": -1})
    assert r.is_error and "pid must be" in text(r)
    assert fake.requests == []


# ------------------------------------------------------------------ errors are plain messages
def test_timeout_gives_plain_message(fake):
    fake.fail = httpx.ReadTimeout("timed out")
    r = call("optionslab_status")
    assert r.is_error
    assert text(r) == server.TIMED_OUT.format(timeout=10, url=server.DEFAULT_URL)
    no_traceback(text(r))


def test_unreachable_gives_plain_message(fake):
    fake.fail = httpx.ConnectError("[WinError 10060] connection attempt failed")
    r = call("optionslab_health")
    assert r.is_error
    assert text(r) == "Options Lab unreachable at http://100.89.79.67:8095 — check Tailscale / service."
    no_traceback(text(r))


def test_timeout_during_ticker_validation_is_plain(fake):
    fake.fail = httpx.ConnectTimeout("timed out")
    r = call("optionslab_chain", {"ticker": "SPY"})
    assert r.is_error and text(r).startswith("Options Lab did not answer within 10 s")


def test_http_error_passes_api_message(fake):
    fake.override["/api/surface/SPY"] = httpx.Response(409, json={"error": "SPY: no expiry has enough clean quotes"})
    r = call("optionslab_surface", {"ticker": "SPY"})
    assert r.is_error
    assert text(r) == "Options Lab returned HTTP 409 for /api/surface/SPY: SPY: no expiry has enough clean quotes"


def test_non_json_reply_is_plain(fake):
    fake.override["/api/status"] = httpx.Response(200, text="<html>proxy error</html>")
    r = call("optionslab_status")
    assert r.is_error and text(r) == "Options Lab sent a non-JSON reply for /api/status."


def test_unexpected_payload_shape_is_plain(fake):
    fake.data["/api/chain/SPY"]["rows"] = "not a list"
    r = call("optionslab_chain", {"ticker": "SPY"})
    assert r.is_error
    assert text(r).startswith("optionslab_chain failed: Options Lab sent data this server could not read")
    no_traceback(text(r))


def test_timeout_is_ten_seconds_and_url_from_env():
    env = {**os.environ, "OPTIONSLAB_URL": "http://example.invalid:1/"}
    out = subprocess.run(
        [sys.executable, "-c", "import server; print(server.lab.base_url, server.lab.timeout)"],
        cwd=HERE,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split() == ["http://example.invalid:1", "10.0"]
    assert server.TIMEOUT_S == 10.0


# ------------------------------------------------------------------ trimming
def test_chain_keeps_strikes_nearest_atm_and_says_so(fake):
    out = json.loads(text(call("optionslab_chain", {"ticker": "SPY"})))
    strikes = [r["strike"] for r in out["rows"]]
    assert len(strikes) == server.CHAIN_STRIKES and 770 in strikes
    assert strikes == list(range(765, 776))
    assert set(out["rows"][0]["call"]) <= set(server.CHAIN_LEG_FIELDS)
    assert "parity" not in out["expiry_meta"]
    assert out["truncated"]["rows"].startswith("showing 11 of 130 strikes")


def test_smile_downsampled_with_atm_kept(fake):
    out = json.loads(text(call("optionslab_smile", {"ticker": "SPY"})))
    assert len(out["points"]) <= server.SMILE_POINTS + 1 and len(out["fit"]) == server.SMILE_FIT
    assert any(p["strike"] == 769 for p in out["points"])
    assert out["atm_iv_pct"] == 13.68
    assert set(out["truncated"]) == {"points", "fit"}


def test_surface_grid_downsampled(fake):
    out = json.loads(text(call("optionslab_surface", {"ticker": "SPY"})))
    assert len(out["k"]) == server.SURFACE_K and len(out["iv"]) == 13 and all(len(r) == 11 for r in out["iv"])
    assert out["k"][0] == -0.5 and out["k"][-1] == 0.5
    assert "truncated" in out


def test_health_drops_history_and_large_detail(fake):
    out = json.loads(text(call("optionslab_health")))
    assert out["counts"] == {"PASS": 6, "FAIL": 0, "UNTESTED": 8, "FLAG": 0}
    assert len(out["checks"]) == 14 and all("history" not in c for c in out["checks"])
    assert all("detail" not in c for c in out["checks"]) and "checks" in out["truncated"]
    assert out["checks"][0]["value"] == 1e-8  # tiny values survive rounding
    assert len(text(call("optionslab_health"))) < 20_000


def test_watchlist_adds_percent_iv(fake):
    out = json.loads(text(call("optionslab_watchlist")))
    spy = next(r for r in out["rows"] if r["ticker"] == "SPY")
    assert spy["atm30_iv_pct"] == 13.55 and spy["atm30_iv"] == 0.135464


def test_quality_equity_and_tracker_marks_capped(fake):
    q = json.loads(text(call("optionslab_quality", {"ticker": "SPY"})))
    assert len(q["rows"]) == server.QUALITY_ROWS and q["rows"][0]["ts_utc"] == "2026-10-60"
    e = json.loads(text(call("optionslab_paper_equity")))
    assert len(e["rows"]) == server.EQUITY_ROWS and e["summary"]["points"] == 104 and "truncated" in e
    t = json.loads(text(call("optionslab_tracker", {"pid": 10})))
    assert len(t["marks"]) == server.TRACKER_MARKS and t["marks"][-1]["ts_utc"] == "89"


def test_small_payloads_are_not_marked_truncated(fake):
    for name, args in [("optionslab_term", {"ticker": "SPY"}), ("optionslab_watchlist", {}), ("optionslab_status", {})]:
        assert "truncated" not in json.loads(text(call(name, args)))


# ------------------------------------------------------------------ real stdio launch (what Fincept does)
def test_stdio_server_lists_tools_and_reports_unreachable():
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "server.py")],
        env={**os.environ, "OPTIONSLAB_URL": "http://127.0.0.1:9"},
    )  # nothing listens

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            return tools, await client.call_tool("optionslab_status", {})

    tools, r = asyncio.run(go())
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS
    assert r.is_error
    assert text(r) == "Options Lab unreachable at http://127.0.0.1:9 — check Tailscale / service."


def test_request_hook_fires_before_the_transport():
    # If a future edit bypassed _get and called the client directly, the hook still stops writes on the wire.
    from conftest import FakeLab

    handler = FakeLab()
    lab = server.OptionsLab(server.DEFAULT_URL, transport=httpx.MockTransport(handler))

    async def go():
        async with lab.client() as c:
            await c.post("/api/refresh")

    with pytest.raises(server.OptionsLabError, match="not an Options Lab read request"):
        asyncio.run(go())
    assert handler.requests == []
