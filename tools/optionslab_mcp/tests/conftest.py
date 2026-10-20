"""Fixtures: a fake Options Lab served through httpx.MockTransport, recording every request.

Payloads are synthetic but shaped like the real API (sampled 2026-10-20), and sized like it
where trimming matters (130-strike chain, 13x41 surface, 200-run health history).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA"]


def leg(kind: str, strike: int) -> dict:
    # A real leg has ~45 fields; a few of the dropped ones are included to prove trimming.
    return {
        "contract": f"SPY {kind}{strike}",
        "type": kind,
        "bid": 9.2,
        "ask": 9.3,
        "mid": 9.25,
        "last": 9.28,
        "volume": 100,
        "quote_status": "OK",
        "iv_mid": 0.1367,
        "iv_status": "OK",
        "delta": 0.5,
        "gamma": 0.017,
        "vega": 0.68,
        "theta": -0.3,
        "model_px": 9.245,
        "ibkr_iv": 0.1371,
        "vanna": 0.0006,
        "zomma": -0.001,
        "amer_rho": 0.18,
        "eep": 0.0,
    }


def payloads() -> dict[str, object]:
    strikes = list(range(705, 835))  # 130 strikes, ATM 770
    k = [-0.5 + i * 0.025 for i in range(41)]
    return {
        "/api/watchlist": {
            "rows": [
                {
                    "ticker": t,
                    "spot": 768.41,
                    "prev_close": 763.25,
                    "pct_change": 0.0068,
                    "atm30_iv": 0.135464149389113,
                    "atm30_iv_prev_close": 0.1389,
                    "atm30_iv_change": -0.0034,
                    "snapshot_ts": "2026-10-19T19:57:35+00:00",
                    "age_s": 100.0,
                    "n_contracts": 2936,
                }
                for t in TICKERS
            ]
        },
        "/api/status": {"market_open": False, "source": "ibkr", "tickers": {t: {"error": None} for t in TICKERS}},
        "/api/session-summary": {"day": "2026-10-19", "text": "ok", "report": {"ok": True}},
        "/api/health": {
            "counts": {"PASS": 6, "FAIL": 0, "UNTESTED": 8, "FLAG": 0},
            "run_ts": "2026-10-19T20:09:04+00:00",
            "scope": "ALL",
            "checks": [
                {
                    "key": f"c{i}",
                    "name": f"Check {i}",
                    "description": "d" * 50,
                    "verdict": "PASS",
                    "judged_verdict": None,
                    "untested_reason": None,
                    "value": 1e-8,
                    "unit": "$",
                    "tolerance": "≤ 0.01",
                    "needs_live": False,
                    "market_open": False,
                    "run_ts": "x",
                    "detail": {"rows": ["x" * 100] * (500 if i == 0 else 1)},
                    "history": [{"run_ts": "x", "value": 0.0, "verdict": "PASS"}] * 200,
                }
                for i in range(14)
            ],
        },
        "/api/chain/SPY": {
            "ticker": "SPY",
            "expiry": "2026-11-06",
            "spot": 768.41,
            "snapshot_ts": "x",
            "source": "ibkr",
            "expiry_meta": {"days": 18.0, "atm_iv": 0.1368, "parity": {"strikes": list(range(500))}},
            "expiries": [{"expiry": "2026-11-06", "days": 18.0, "short_dated": False}],
            "rows": [{"strike": s, "call": leg("C", s), "put": leg("P", s), "atm": s == 770} for s in strikes],
            "quote_counts": {"OK": 250},
            "iv_counts": {"OK": 130},
            "units": {"delta": "per $1"},
        },
        "/api/smile/SPY": {
            "ticker": "SPY",
            "expiry": "2026-11-06",
            "days": 18.0,
            "spot": 768.41,
            "forward": 769.0,
            "atm_iv": 0.1368,
            "fit_status": "OK",
            "fit_rmse_vol_pts": 0.2,
            "n_fit_points": 120,
            "points": [{"strike": s, "k": 0.0, "type": "P" if s < 769 else "C", "iv_mid": 0.14} for s in strikes],
            "fit": [{"k": i / 100, "strike": 700 + i, "iv": 0.14} for i in range(241)],
            "expiries": [],
        },
        "/api/term/SPY": {
            "ticker": "SPY",
            "atm30_iv": 0.1355,
            "rows": [{"expiry": "2026-11-06", "days": 18.0, "atm_iv": 0.1368}],
        },
        "/api/surface/SPY": {
            "ticker": "SPY",
            "k": k,
            "T": [0.05] * 13,
            "days": [18.0] * 13,
            "expiries": ["e"] * 13,
            "iv": [[0.14] * 41 for _ in range(13)],
            "moneyness": [1.0] * 41,
        },
        "/api/quality/SPY": {
            "ticker": "SPY",
            "rows": [{"ts_utc": f"2026-10-{i:02d}", "atm30_iv": 0.13} for i in range(1, 61)],
        },
        "/api/tracker": {"positions": [{"id": 10, "ticker": "SPY", "summary": {}}]},
        "/api/tracker/10": {"position": {"id": 10}, "summary": {}, "marks": [{"ts_utc": str(i)} for i in range(90)]},
        "/api/paper": {"running": True, "positions": [], "orders": []},
        "/api/paper/equity": {
            "book": "main",
            "rows": [{"ts_utc": str(i), "equity_aud": 5000.0 + i} for i in range(104)],
        },
    }


class FakeLab:
    """MockTransport handler: serves payloads(), records (method, path) and the full URL of every request."""

    def __init__(self):
        self.data = payloads()
        self.requests: list[tuple[str, str]] = []
        self.urls: list[httpx.URL] = []
        self.fail: Exception | None = None  # raise this for every request
        self.override: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        self.urls.append(request.url)
        if self.fail:
            raise self.fail
        if request.url.path in self.override:
            return self.override[request.url.path]
        if request.method != "GET":
            return httpx.Response(405, json={"error": "fake lab only serves GET"})
        body = self.data.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"error": f"{request.url.path} not found", "ticker": None})
        return httpx.Response(200, content=json.dumps(body).encode(), headers={"content-type": "application/json"})


@pytest.fixture
def fake(monkeypatch) -> FakeLab:
    handler = FakeLab()
    monkeypatch.setattr(server, "lab", server.OptionsLab(server.DEFAULT_URL, transport=httpx.MockTransport(handler)))
    return handler


def call(name: str, args: dict | None = None):
    """Call a tool through a real MCP client session (in-process), as Fincept would see the result."""
    from mcp import Client

    async def go():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, args or {})

    return asyncio.run(go())


def text(result) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)
