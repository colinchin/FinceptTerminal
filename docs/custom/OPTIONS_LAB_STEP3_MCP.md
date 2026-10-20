# Step 3 — Options Lab MCP server (read-only) for Fincept AI Chat

**Goal:** Fincept's AI Chat (and Claude Code) can answer questions using Options Lab's data,
e.g. "What's SPY's IV smile, and are today's health checks passing?"
**Scope:** a small standalone Python MCP server on this PC. No changes to Options Lab or to
Fincept's C++ code — it is registered through Fincept's existing **MCP Servers** screen
(external stdio servers are supported: `src/mcp/McpClient.cpp`, `McpManager.cpp`).

## Facts
- Options Lab API base: `http://100.89.79.67:8095` (Tailscale). No auth on the API.
- Read endpoints (from `optlab/api/app.py` in D:\Source\options-pricing):
  `GET /api/status`, `/api/session-summary`, `/api/watchlist`, `/api/health`,
  `/api/chain/{ticker}`, `/api/smile/{ticker}`, `/api/term/{ticker}`, `/api/surface/{ticker}`,
  `/api/quality/{ticker}`, `/api/templates`, `/api/tracker`, `/api/tracker/{pid}`,
  `/api/paper`, `/api/paper/equity`.
- Write endpoints exist (`POST /api/refresh`, `/api/paper/pause|resume`, `/api/tracker`,
  `DELETE /api/tracker/{pid}`, `POST /api/position/*`). **Do not expose any of them.**

## Build
1. New folder in this repo: `tools/optionslab_mcp/` (Python 3.11, own venv, `requirements.txt`
   pinning the official `mcp` SDK and `httpx`).
2. One stdio MCP server, `server.py`, exposing **read-only** tools that each wrap one GET endpoint:
   `optionslab_status`, `optionslab_health`, `optionslab_watchlist`, `optionslab_chain(ticker)`,
   `optionslab_smile(ticker)`, `optionslab_term(ticker)`, `optionslab_surface(ticker)`,
   `optionslab_quality(ticker)`, `optionslab_paper`, `optionslab_paper_equity`,
   `optionslab_tracker(pid?)`, `optionslab_session_summary`.
   - Validate `ticker` against `/api/watchlist` before calling.
   - Base URL from env var `OPTIONSLAB_URL` (default above); 10 s timeout.
   - Trim large payloads (chains/surfaces) to what an LLM can use — keep the top-level summary and
     cap row counts, and say in the result when rows were truncated.
   - Errors return a plain message ("Options Lab unreachable — check Tailscale / service"), never a
     stack trace.
3. Tests with mocked HTTP (`pytest`), including: unknown ticker rejected, timeout handled, no tool
   ever issues a non-GET request.
4. `README.md` in that folder: how to run it standalone, and the exact values to enter in Fincept's
   MCP Servers screen (command = the venv's `python.exe`, args = path to `server.py`, env).

## Done when
- `pytest` passes.
- Registered in Fincept's MCP Servers screen and shown as connected with its tools listed.
- In Fincept AI Chat, "Are Options Lab's health checks passing, and what's SPY ATM 30-day IV?"
  is answered with numbers that match the Options Lab dashboard.
- Code review confirms no write endpoint is reachable through any tool.
