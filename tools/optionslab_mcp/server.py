"""Options Lab MCP server — read-only access to the Options Lab API for Fincept AI Chat / Claude Code.

stdio transport. Every tool wraps exactly one GET endpoint of the Options Lab API
(http://100.89.79.67:8095 over Tailscale by default; override with OPTIONSLAB_URL).

Read-only by construction:
  * all HTTP goes through OptionsLab._get(), which only ever calls httpx.AsyncClient.get();
  * _get() refuses any path that is not one of the read routes in READ_ROUTES;
  * a request hook re-checks method, host and path of the request actually about to be sent;
  * OPTIONSLAB_URL must be a bare http(s)://host[:port] (no path, query or credentials);
  * redirects are not followed and proxy env vars (HTTP_PROXY etc.) are ignored;
  * path parameters are a ticker that must be on the Options Lab watchlist, or an int.
Options Lab's write endpoints (POST /api/refresh, /api/paper/pause|resume, /api/tracker,
/api/tracker/{pid}/stop, /api/position/*, DELETE /api/tracker/{pid}) are never reachable.

stdout carries the MCP protocol, so logs go to stderr as one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations

DEFAULT_URL = "http://100.89.79.67:8095"
TIMEOUT_S = 10.0
WATCHLIST_TTL_S = 60.0  # ticker validation re-reads /api/watchlist at most once a minute

# Size budget. Fincept AI Chat shrinks any tool result over 8000 chars (setting mcp/tool_result_max_chars)
# and parks the rest behind result_fetch, so every tool aims to stay under ~7000 chars. Measured on live
# data 2026-10-20: the first cut (21 strikes, full health details, untrimmed paper) gave 9-15 K chars for
# health/chain/smile/paper/equity/tracker(pid); these caps bring all of them under budget.
CHAIN_STRIKES = 11  # 130 strikes (~334 KB) -> the 11 strikes nearest ATM
SMILE_POINTS = 25  # 130 quoted points -> 25 evenly spaced (ATM always kept)
SMILE_FIT = 15  # 241 fitted knots -> 15
SURFACE_K = 11  # 41 moneyness columns -> 11 (all expiries kept)
QUALITY_ROWS = 10  # 60 history rows -> the latest 10
EQUITY_ROWS = 24  # ~100 equity points (one per ~10 min) -> the latest 24
TRACKER_MARKS = 10  # per-position mark series -> the latest 10
PAPER_ORDERS = 5  # recent orders shown by optionslab_paper

CHAIN_LEG_FIELDS = ("bid", "ask", "mid", "volume", "quote_status", "iv_mid", "delta", "gamma", "vega", "theta")
HEALTH_CHECK_FIELDS = ("key", "name", "verdict", "judged_verdict", "untested_reason", "value", "unit", "tolerance",
                       "needs_live")  # fmt: skip
EQUITY_FIELDS = ("ts_utc", "equity_aud", "equity_usd", "realised_usd", "open_usd")
POSITION_FIELDS = ("id", "book", "underlying", "strategy", "expiry", "qty", "status", "opened_ts", "closed_ts",
                   "entry_credit", "exit_debit", "exit_reason", "pnl", "legs")  # fmt: skip
ORDER_FIELDS = ("id", "position_id", "purpose", "status", "limit_price", "fill_price", "created_ts", "reason")
MARK_FIELDS = ("ts_utc", "feed", "spot", "value_mid", "pnl_mid", "pnl_liquidation", "all_quoted", "greeks")

# The only paths _get() will request. Anything else is refused before any network I/O.
READ_ROUTES = tuple(
    re.compile(p)
    for p in (
        r"/api/status",
        r"/api/session-summary",
        r"/api/watchlist",
        r"/api/health",
        r"/api/chain/[A-Z0-9^][A-Z0-9.^-]{0,11}",
        r"/api/smile/[A-Z0-9^][A-Z0-9.^-]{0,11}",
        r"/api/term/[A-Z0-9^][A-Z0-9.^-]{0,11}",
        r"/api/surface/[A-Z0-9^][A-Z0-9.^-]{0,11}",
        r"/api/quality/[A-Z0-9^][A-Z0-9.^-]{0,11}",
        r"/api/tracker",
        r"/api/tracker/[0-9]{1,9}",
        r"/api/paper",
        r"/api/paper/equity",
    )
)

UNREACHABLE = "Options Lab unreachable at {url} — check Tailscale / service."
TIMED_OUT = "Options Lab did not answer within {timeout:g} s at {url} — check Tailscale / service."

log = logging.getLogger("optionslab_mcp")


def _log(event: str, **fields: Any) -> None:
    log.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}, default=str))


class OptionsLabError(Exception):
    """A failure with a message that is safe and useful to show the user / LLM as-is."""


class OptionsLab:
    """GET-only client for the Options Lab API."""

    def __init__(self, base_url: str, timeout: float = TIMEOUT_S, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.strip().rstrip("/")
        self.timeout = timeout
        self._transport = transport  # tests inject httpx.MockTransport
        self._tickers: tuple[float, list[str]] | None = None
        self.config_error = _base_url_problem(self.base_url)
        self._host = None if self.config_error else httpx.URL(self.base_url).host

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self._transport,
            follow_redirects=False,  # a 30x must never move us to another host or path
            trust_env=False,  # ignore HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / .netrc
            event_hooks={"request": [self._guard]},
        )

    async def _guard(self, request: httpx.Request) -> None:
        """Last check on the request actually about to go on the wire (after URL joining)."""
        if (
            request.method != "GET"
            or request.url.host != self._host
            or request.url.query
            or not any(r.fullmatch(request.url.path) for r in READ_ROUTES)
        ):
            raise OptionsLabError(f"Refused: {request.method} {request.url} is not an Options Lab read request.")

    async def _get(self, path: str) -> Any:
        if self.config_error:
            raise OptionsLabError(self.config_error)
        if not any(r.fullmatch(path) for r in READ_ROUTES):
            raise OptionsLabError(f"Refused: {path} is not an Options Lab read endpoint.")
        t0 = time.perf_counter()
        status: int | str = "-"
        try:
            async with self.client() as client:
                resp = await client.get(path)
            status = resp.status_code
            if 300 <= resp.status_code < 400:
                raise OptionsLabError(
                    f"Options Lab answered {path} with a redirect (HTTP {resp.status_code}); not followed."
                )
            if resp.status_code >= 400:
                raise OptionsLabError(f"Options Lab returned HTTP {resp.status_code} for {path}: {_api_message(resp)}")
            try:
                return resp.json()
            except ValueError:
                raise OptionsLabError(f"Options Lab sent a non-JSON reply for {path}.") from None
        except httpx.TimeoutException:
            status = "timeout"
            raise OptionsLabError(TIMED_OUT.format(timeout=self.timeout, url=self.base_url)) from None
        except httpx.HTTPError as ex:  # connect refused / DNS / no route / protocol errors
            status = type(ex).__name__
            raise OptionsLabError(UNREACHABLE.format(url=self.base_url)) from None
        finally:
            _log("http_get", path=path, status=status, ms=round((time.perf_counter() - t0) * 1000))

    async def watchlist_tickers(self) -> list[str]:
        now = time.monotonic()
        if self._tickers and now - self._tickers[0] < WATCHLIST_TTL_S:
            return self._tickers[1]
        data = await self._get("/api/watchlist")
        tickers = [str(r["ticker"]).upper() for r in data.get("rows", []) if r.get("ticker")]
        self._tickers = (now, tickers)
        return tickers

    async def checked_ticker(self, ticker: str) -> str:
        t = (ticker or "").strip().upper()
        known = await self.watchlist_tickers()
        if t not in known:
            raise OptionsLabError(f"Unknown ticker {ticker!r}. Options Lab covers: {', '.join(known) or '(none)'}.")
        return t


def _base_url_problem(url: str) -> str | None:
    """None if `url` is a bare http(s)://host[:port]; otherwise a plain message saying what is wrong."""
    try:
        u = httpx.URL(url)
    except Exception:
        return f"OPTIONSLAB_URL {url!r} is not a valid URL."
    if u.scheme not in ("http", "https") or not u.host:
        return f"OPTIONSLAB_URL {url!r} must be http:// or https:// with a host."
    if u.path not in ("", "/") or u.query or u.fragment or u.userinfo:
        return f"OPTIONSLAB_URL {url!r} must be just scheme://host[:port] (no path, query or credentials)."
    return None


def _api_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            return str(body["error"])[:300]
    except ValueError:
        pass
    return resp.reason_phrase or "error"


# --------------------------------------------------------------------------- trimming helpers
def _round(o: Any) -> Any:
    """Floats to 6 significant figures (keeps tiny error values meaningful), recursively."""
    if isinstance(o, float):
        return float(f"{o:.6g}")
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def _pick(d: dict, fields: tuple[str, ...]) -> dict:
    return {k: d[k] for k in fields if k in d}


def _pct(x: Any) -> float | None:
    return round(x * 100, 2) if isinstance(x, (int, float)) else None


def _spread(items: list, n: int, keep: int | None = None) -> list:
    """n items evenly spaced across `items` (first and last included), always including index `keep`."""
    if len(items) <= n:
        return list(items)
    idx = {round(i * (len(items) - 1) / (n - 1)) for i in range(n)}
    if keep is not None:
        idx.add(keep)
    return [items[i] for i in sorted(idx)]


def _note(out: dict, what: str, shown: int, total: int, how: str) -> None:
    if shown < total:
        out.setdefault("truncated", {})[what] = f"showing {shown} of {total} {how}"


def _omitted(out: dict, what: str, how: str) -> None:
    out.setdefault("truncated", {})[what] = how


IV_NOTE = "IV values are annualised decimals (0.1355 = 13.55%); *_pct fields are the same value in percent."


# --------------------------------------------------------------------------- server + tools
lab = OptionsLab(os.environ.get("OPTIONSLAB_URL", DEFAULT_URL))

mcp = MCPServer(
    "optionslab",
    instructions=(
        "Read-only access to Options Lab, a personal options-pricing dashboard (SPY, QQQ, IWM, AAPL, MSFT, NVDA): "
        "IV smiles, term structures, vol surfaces, option chains with greeks, data-quality health checks and the "
        "simulated (paper) iron-condor accounts. Data is IBKR delayed; when the market is closed it is the last "
        "snapshot. Nothing here can place, change or cancel anything."
    ),
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def _result(text: str, is_error: bool = False) -> CallToolResult:
    # Returned as-is by the SDK: one text item (what Fincept reads), no "Error executing tool ..." prefix.
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=is_error)


async def _run(tool: str, args: dict[str, Any], fn) -> CallToolResult:
    """Run one tool body: log entry/exit, turn every failure into a plain message (never a stack trace)."""
    t0 = time.perf_counter()
    _log("tool_start", tool=tool, args=args)
    try:
        result = await fn()
    except OptionsLabError as ex:
        _log("tool_error", tool=tool, error=str(ex), ms=round((time.perf_counter() - t0) * 1000))
        return _result(str(ex), is_error=True)
    except Exception as ex:  # a payload shape we did not expect — say so, keep the trace in the log only
        log.exception("unexpected failure in %s", tool)
        _log("tool_error", tool=tool, error=type(ex).__name__, ms=round((time.perf_counter() - t0) * 1000))
        return _result(
            f"{tool} failed: Options Lab sent data this server could not read ({type(ex).__name__}).", is_error=True
        )
    text = json.dumps(_round(result), separators=(",", ":"), ensure_ascii=False)
    _log("tool_ok", tool=tool, bytes=len(text), ms=round((time.perf_counter() - t0) * 1000))
    return _result(text)


def _tool(description: str):
    return mcp.tool(description=description, annotations=READ_ONLY)


@_tool(
    "Options Lab service status: market open/closed and next open, data source (IBKR delayed / fallback), "
    "scheduler and watchdog state, and per-ticker last refresh time, age and errors."
)
async def optionslab_status() -> CallToolResult:
    return await _run("optionslab_status", {}, lambda: lab._get("/api/status"))


@_tool(
    "Options Lab health checks: are the Options Lab checks passing? PASS/FAIL/UNTESTED/FLAG counts and each "
    "pricing-engine and data-quality check with its verdict. UNTESTED means the check needs live quotes and the "
    "market is closed; judged_verdict is what it gives on the latest snapshot anyway."
)
async def optionslab_health() -> CallToolResult:
    async def body():
        data = await lab._get("/api/health")
        checks = data.get("checks", [])
        out = {k: data.get(k) for k in ("counts", "run_ts", "scope")}
        out["market_open"] = next((c.get("market_open") for c in checks), None)
        out["checks"] = []
        for c in checks:
            row = _pick(c, HEALTH_CHECK_FIELDS)
            row["description"] = (c.get("description") or "")[:160]
            out["checks"].append(row)
        _omitted(out, "checks", "per-check detail and run history omitted (up to ~50 KB per check)")
        return out

    return await _run("optionslab_health", {}, body)


@_tool(
    "Options Lab watchlist: SPY, QQQ, IWM, AAPL, MSFT, NVDA with spot, % change and ATM 30-day implied "
    "volatility (IV) — atm30_iv, atm30_iv_pct in percent, change since previous close — and snapshot age. "
    "Use for 'what is SPY's 30-day IV?'."
)
async def optionslab_watchlist() -> CallToolResult:
    async def body():
        data = await lab._get("/api/watchlist")
        for r in data.get("rows", []):
            r["atm30_iv_pct"] = _pct(r.get("atm30_iv"))
            r["atm30_iv_change_pct_pts"] = _pct(r.get("atm30_iv_change"))
        data["note"] = IV_NOTE
        return data

    return await _run("optionslab_watchlist", {}, body)


@_tool(
    "Options Lab option chain for one watchlist ticker at the default expiry (~30 days): bid/ask/mid, volume, "
    "IV and greeks per strike for calls and puts, limited to the strikes nearest the money. "
    "ticker must be on the Options Lab watchlist (see optionslab_watchlist)."
)
async def optionslab_chain(ticker: str) -> CallToolResult:
    async def body():
        t = await lab.checked_ticker(ticker)
        data = await lab._get(f"/api/chain/{t}")
        rows = data.get("rows", [])
        atm = next((i for i, r in enumerate(rows) if r.get("atm")), len(rows) // 2)
        lo = max(0, min(atm - CHAIN_STRIKES // 2, len(rows) - CHAIN_STRIKES))
        kept = rows[lo : lo + CHAIN_STRIKES]
        for r in kept:
            for leg in ("call", "put"):
                if isinstance(r.get(leg), dict):
                    r[leg] = {k: r[leg].get(k) for k in CHAIN_LEG_FIELDS if k in r[leg]}
        meta = _pick(data.get("expiry_meta") or {}, ("days", "F", "r", "q", "atm_iv", "n_contracts"))
        out = {k: data.get(k) for k in ("ticker", "expiry", "spot", "snapshot_ts", "source", "quote_counts")}
        out.update(
            expiry_meta=meta,
            expiries=[e.get("expiry") for e in data.get("expiries") or []],
            rows=kept,
            units=_pick(data.get("units") or {}, ("delta", "gamma", "vega", "theta")),
            note=IV_NOTE,
        )
        _note(out, "rows", len(kept), len(rows), "strikes (the ones nearest ATM); per-contract fields trimmed")
        return out

    return await _run("optionslab_chain", {"ticker": ticker}, body)


@_tool(
    "Options Lab IV smile (implied-volatility smile/skew) for one watchlist ticker at the default expiry "
    "(~30 days): ATM IV, fit status and RMSE, quoted OTM points (strike, IV bid/mid/ask) and the fitted curve."
)
async def optionslab_smile(ticker: str) -> CallToolResult:
    async def body():
        t = await lab.checked_ticker(ticker)
        data = await lab._get(f"/api/smile/{t}")
        points, fit = data.get("points", []), data.get("fit", [])
        spot = data.get("forward") or data.get("spot") or 0
        atm = min(range(len(points)), key=lambda i: abs(points[i]["strike"] - spot)) if points else None
        out = {
            k: data.get(k)
            for k in (
                "ticker",
                "expiry",
                "days",
                "spot",
                "forward",
                "atm_iv",
                "fit_status",
                "fit_rmse_vol_pts",
                "n_fit_points",
            )
        }
        out["atm_iv_pct"] = _pct(data.get("atm_iv"))
        out["points"] = [{k: v for k, v in p.items() if k != "k"} for p in _spread(points, SMILE_POINTS, atm)]
        out["fit"] = _spread(fit, SMILE_FIT)
        out["expiries"] = [e.get("expiry") for e in data.get("expiries") or []]
        out["note"] = IV_NOTE
        _note(out, "points", len(out["points"]), len(points), "quoted points, evenly spaced, ATM kept")
        _note(out, "fit", len(out["fit"]), len(fit), "fitted knots, evenly spaced")
        return out

    return await _run("optionslab_smile", {"ticker": ticker}, body)


@_tool(
    "Options Lab ATM implied-volatility term structure for one watchlist ticker: ATM IV per expiry with days "
    "to expiry, forward, dividend yield q and rate r, plus the interpolated ATM 30-day IV."
)
async def optionslab_term(ticker: str) -> CallToolResult:
    async def body():
        t = await lab.checked_ticker(ticker)
        data = await lab._get(f"/api/term/{t}")
        for r in data.get("rows", []):
            r["atm_iv_pct"] = _pct(r.get("atm_iv"))
        data["atm30_iv_pct"] = _pct(data.get("atm30_iv"))
        data["note"] = IV_NOTE
        return data

    return await _run("optionslab_term", {"ticker": ticker}, body)


@_tool(
    "Options Lab implied-volatility surface for one watchlist ticker: IV grid by expiry (rows, days) and "
    "log-moneyness k (columns, moneyness = K/F = exp(k)), downsampled to a small grid."
)
async def optionslab_surface(ticker: str) -> CallToolResult:
    async def body():
        t = await lab.checked_ticker(ticker)
        data = await lab._get(f"/api/surface/{t}")
        k = data.get("k", [])
        cols = _spread(list(range(len(k))), SURFACE_K)
        out = {
            "ticker": data.get("ticker"),
            "expiries": data.get("expiries"),
            "days": data.get("days"),
            "k": [k[i] for i in cols],
            "moneyness": [data["moneyness"][i] for i in cols] if data.get("moneyness") else None,
            "iv": [[row[i] for i in cols] for row in data.get("iv", [])],
            "layout": "iv[expiry_index][k_index]",
            "note": IV_NOTE,
        }
        _note(out, "k", len(cols), len(k), "moneyness columns, evenly spaced")
        return out

    return await _run("optionslab_surface", {"ticker": ticker}, body)


@_tool(
    "Options Lab data-quality history for one watchlist ticker, newest first: contracts priced, IV solve rate, "
    "put-call parity error, butterfly/calendar arbitrage counts, model-inside-market share, ATM 30-day IV."
)
async def optionslab_quality(ticker: str) -> CallToolResult:
    async def body():
        t = await lab.checked_ticker(ticker)
        data = await lab._get(f"/api/quality/{t}")
        rows = data.get("rows", [])
        newest = sorted(rows, key=lambda r: r.get("ts_utc") or "", reverse=True)[:QUALITY_ROWS]
        out = {"ticker": data.get("ticker"), "rows": newest}
        _note(out, "rows", len(newest), len(rows), "snapshots (the latest)")
        return out

    return await _run("optionslab_quality", {"ticker": ticker}, body)


@_tool(
    "Options Lab simulated (paper) iron-condor accounts: account equity and return, open/closed positions "
    "with P&L, recent orders, entry rules and the trader's last plan/action/error. Simulated money only."
)
async def optionslab_paper() -> CallToolResult:
    async def body():
        data = await lab._get("/api/paper")
        positions, orders = data.get("positions") or [], data.get("orders") or []
        data["positions"] = [_pick(p, POSITION_FIELDS) for p in positions]
        data["orders"] = [_pick(o, ORDER_FIELDS) for o in orders[:PAPER_ORDERS]]
        _omitted(data, "positions", "per-position entry plan and filters omitted")
        _note(data, "orders", len(data["orders"]), len(orders), "recent orders, without their event logs")
        return data

    return await _run("optionslab_paper", {}, body)


@_tool(
    "Options Lab paper-account equity curve (value over time, USD and AUD) of the main simulated account, "
    "latest points only."
)
async def optionslab_paper_equity() -> CallToolResult:
    async def body():
        data = await lab._get("/api/paper/equity")
        rows = data.get("rows", [])
        out = {"book": data.get("book"), "rows": [_pick(r, EQUITY_FIELDS) for r in rows[-EQUITY_ROWS:]]}
        if rows:
            eq = [r.get("equity_aud") for r in rows if r.get("equity_aud") is not None]
            out["summary"] = {
                "points": len(rows),
                "first_ts": rows[0].get("ts_utc"),
                "last_ts": rows[-1].get("ts_utc"),
                "equity_aud_first": eq[0] if eq else None,
                "equity_aud_last": eq[-1] if eq else None,
                "equity_aud_min": min(eq) if eq else None,
                "equity_aud_max": max(eq) if eq else None,
            }
        _note(out, "rows", len(out["rows"]), len(rows), "equity points (the latest)")
        return out

    return await _run("optionslab_paper_equity", {}, body)


@_tool(
    "Options Lab tracked positions with P&L summaries. pid = 0 (default) lists all tracked positions; "
    "a positive pid returns that one position with its recent mark history."
)
async def optionslab_tracker(pid: int = 0) -> CallToolResult:
    async def body():
        if pid < 0:
            raise OptionsLabError("pid must be 0 (all positions) or a positive position id.")
        if pid == 0:
            return await lab._get("/api/tracker")
        data = await lab._get(f"/api/tracker/{int(pid)}")
        marks = data.get("marks", [])
        data["marks"] = [_pick(m, MARK_FIELDS) for m in marks[-TRACKER_MARKS:]]
        _note(data, "marks", len(data["marks"]), len(marks), "marks (the latest), without per-leg detail")
        return data

    return await _run("optionslab_tracker", {"pid": pid}, body)


@_tool(
    "Options Lab's end-of-session summary for the current or most recent New York session: per-ticker "
    "refresh coverage and gaps, health-check failures during the session, and the summary text."
)
async def optionslab_session_summary() -> CallToolResult:
    return await _run("optionslab_session_summary", {}, lambda: lab._get("/api/session-summary"))


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO "HTTP Request:" lines duplicate http_get
    _log("server_start", url=lab.base_url, timeout_s=lab.timeout, tools=len(mcp._tool_manager.list_tools()))
    mcp.run("stdio")


if __name__ == "__main__":
    main()
