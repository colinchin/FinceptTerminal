# Options Lab MCP server (read-only)

A small stdio MCP server that lets Fincept's AI Chat (and Claude Code) answer questions from
**Options Lab**, the options-pricing dashboard running on goldie-oci. Example question:
"Are Options Lab's health checks passing, and what's SPY ATM 30-day IV?"

It is **read-only**. Every tool wraps exactly one `GET` endpoint of the Options Lab API. Options Lab's
write endpoints (`POST /api/refresh`, `/api/paper/pause|resume`, `/api/tracker`, `/api/tracker/{pid}/stop`,
`/api/position/*`, `DELETE /api/tracker/{pid}`) cannot be reached:

- all HTTP goes through one function, `OptionsLab._get()`, which only calls `httpx.AsyncClient.get()`;
- `_get()` refuses any path that is not in the `READ_ROUTES` allowlist, before any network I/O;
- a request hook (`OptionsLab._guard`) re-checks the request actually about to be sent: method `GET`, the
  configured host, no query string, path in `READ_ROUTES`;
- `OPTIONSLAB_URL` must be a bare `http(s)://host[:port]`; redirects are not followed (a 3xx is an error);
  proxy environment variables are ignored (`trust_env=False`);
- the only path parameters are a ticker that must be on the Options Lab watchlist, and an integer id.

The tests enforce all of these, including hostile tool arguments, a compromised watchlist, redirects and
bad URLs (see `tests/test_server.py`, section "read-only guarantee").

**Caveat (Options Lab side, not fixable here):** two of Options Lab's *GET* endpoints have side effects.
`GET /api/health` runs and saves a health check when none is cached (only after a failed startup run), and
`GET /api/paper` / `/api/session-summary` refresh the paper trader's in-memory price/FX cache from an API
thread. Neither places, changes or cancels anything, but they are not strictly side-effect free.

## Tools

| Tool | Endpoint | Notes |
|---|---|---|
| `optionslab_status` | `/api/status` | market open/closed, data source, per-ticker refresh state |
| `optionslab_health` | `/api/health` | check counts + one line per check; per-check detail and history dropped |
| `optionslab_watchlist` | `/api/watchlist` | spot and ATM 30-day IV per ticker (adds `atm30_iv_pct`) |
| `optionslab_chain(ticker)` | `/api/chain/{ticker}` | 11 strikes nearest ATM, 10 fields per leg |
| `optionslab_smile(ticker)` | `/api/smile/{ticker}` | 25 quoted points (ATM kept) + 15 fitted knots |
| `optionslab_term(ticker)` | `/api/term/{ticker}` | ATM IV per expiry |
| `optionslab_surface(ticker)` | `/api/surface/{ticker}` | IV grid, all expiries × 11 moneyness columns |
| `optionslab_quality(ticker)` | `/api/quality/{ticker}` | latest 10 data-quality snapshots |
| `optionslab_paper` | `/api/paper` | paper accounts, positions, last 5 orders |
| `optionslab_paper_equity` | `/api/paper/equity` | latest 24 equity points + summary |
| `optionslab_tracker(pid=0)` | `/api/tracker`, `/api/tracker/{pid}` | 0 = list all; else one position, latest 10 marks |
| `optionslab_session_summary` | `/api/session-summary` | end-of-session report |

`ticker` is checked against `/api/watchlist` (cached 60 s) before the call; an unknown ticker is rejected
with the list of valid ones. When a result is cut down, it says so in a `truncated` field.

Results are kept under ~7,000 characters because Fincept AI Chat shrinks any tool result over 8,000
(setting `mcp/tool_result_max_chars`). Live sizes on 2026-10-20 ranged from 1.8 K to 6.4 K characters.

Errors come back as one plain line (MCP `isError: true`), never a stack trace, e.g.
`Options Lab unreachable at http://100.89.79.67:8095 — check Tailscale / service.`
The HTTP timeout is 10 s.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `OPTIONSLAB_URL` | `http://100.89.79.67:8095` | Options Lab base URL (Tailscale address of goldie-oci) |

Logs go to **stderr** as one JSON object per line (`server_start`, `tool_start`, `http_get`, `tool_ok`,
`tool_error` with timings). stdout is the MCP channel. In Fincept, stderr shows under the server card's
**LOGS** button.

## Setup (Windows, Python 3.11)

```powershell
cd D:\Source\FinceptTerminal\tools\optionslab_mcp
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # runtime deps + pytest + ruff
```

`requirements.txt` holds the runtime pins only (`mcp`, `httpx`). The `.venv` folder is git-ignored.

## Run standalone

```powershell
.venv\Scripts\python.exe server.py
```

It waits for MCP JSON-RPC on stdin, so on its own it just sits there; that is expected. To poke it
interactively, use the MCP Inspector (`npx @modelcontextprotocol/inspector .venv\Scripts\python.exe server.py`)
or Claude Code:

```powershell
claude mcp add optionslab -e OPTIONSLAB_URL=http://100.89.79.67:8095 -- D:\Source\FinceptTerminal\tools\optionslab_mcp\.venv\Scripts\python.exe D:\Source\FinceptTerminal\tools\optionslab_mcp\server.py
```

## Tests and lint

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check . ; .venv\Scripts\python.exe -m ruff format --check .
```

The tests use `httpx.MockTransport` (no network), except one that launches `server.py` over real stdio
pointed at a closed local port to check the tool list and the "unreachable" message.

## Register in Fincept (MCP Servers screen)

Fincept → **MCP Servers** → **Add server**. Enter exactly:

| Field | Value |
|---|---|
| Name | `Options Lab` (becomes server id `options_lab`; tools appear as `options_lab__optionslab_*`) |
| Description | `Read-only Options Lab data (goldie-oci)` |
| Command | `D:\Source\FinceptTerminal\tools\optionslab_mcp\.venv\Scripts\python.exe` |
| Arguments | `D:\Source\FinceptTerminal\tools\optionslab_mcp\server.py` |
| Env Vars | `OPTIONSLAB_URL=http://100.89.79.67:8095` |
| Category | `utilities` |
| Auto-start on launch | ticked |

Notes on that dialog (from `src/screens/mcp_servers/McpServersScreen_Actions.cpp`):

- Command and Arguments are split shell-style: backslashes are literal, and a path containing spaces
  must be wrapped in `"..."`. No shell is involved, so `%VAR%` or `&&` do nothing.
- Env Vars is one line of space-separated `KEY=value` pairs. It is stored encrypted and cannot be edited
  later; to change it, add the server again with the same name and confirm the overwrite.
- Do not type `uvx` as the command: Fincept rewrites `uvx` to its bundled `uv tool run`, which is for
  PyPI packages, not this local script.
- Fincept lists tools once, when the server starts. After changing `server.py`, stop and start the server
  on its card.

## How Fincept AI Chat uses it (observed 2026-10-20, Fincept v4.5.1, "Fincept LLM" provider)

- Fincept does not send all 900+ tools to the model. The model searches with `tool_list` (BM25 over tool
  names and descriptions, top 5), which is why every description starts with "Options Lab" and names the
  data (health checks, ATM 30-day IV, smile, ...).
- External tools are called as `tool_invoke(server_id="options_lab", tool="optionslab_health")`. The model
  often first tries the bare name (`optionslab_health`), gets "No tool named ... exists", reads the schema
  with `tool_describe`, then calls `tool_invoke`. The chat shows those first attempts as "failed"; the answer
  is still correct. This is Fincept behaviour, not this server.
- Fincept marks every external tool as destructive for retrieval scoring and caps tool results at 8,000
  characters (see above). The floating chat bubble cuts the tool list to 50 by name and will usually not
  see these tools; use the AI Chat screen.
- Fincept pings the server every 60 s (answered in ~2 ms); stopping the server on its card ends both the
  venv launcher and the Python process it starts.
