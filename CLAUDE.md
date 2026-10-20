# CLAUDE.md — FinceptTerminal (personal fork)

## What this is
Personal fork of the open-source Fincept Terminal (AGPL-3.0-or-later).
Native C++20 / Qt 6.8 desktop app with embedded Python analytics. Windows x64, MSVC.
Goal of this fork: customise it for **equity trading** — screens, watchlists, analytics,
backtests and broker workflow. Personal use only; nothing is distributed or hosted.

## Repo & git setup
- Local path: `D:\Source\FinceptTerminal` (C++ project lives in `fincept-qt\`)
- `origin`   = `colinchin/FinceptTerminal` (my fork — the only place to push)
- `upstream` = `Fincept-Corporation/FinceptTerminal` (read-only; fetch and merge from it)
- Working branch: `equity-custom`. `main` mirrors upstream and is not edited directly.
- **Never push to, open PRs against, or file issues on `upstream`.** Any `gh` command that
  targets a repo must use `--repo colinchin/FinceptTerminal`.
- Syncing upstream:
  ```
  git checkout main && git pull upstream main && git push origin main
  git checkout equity-custom && git merge main
  ```
  then rebuild and launch before continuing. Resolve conflicts in favour of keeping my changes
  working; never silently drop upstream changes.

## Build & run (Windows)
The shell MUST have the **x64** MSVC environment from **VS 2022 Build Tools** (MSVC 19.4x).
This machine also has VS Build Tools 2026 (MSVC 19.5x) — do not use it for this project.
The default "Developer PowerShell for VS 2022" shortcut is **x86** and breaks the build
("Qt6 not found", compiler path contains `Hostx86/x86`). Enter the x64 shell with:
```
& "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\Launch-VsDevShell.ps1" -Arch amd64 -HostArch amd64
```
Check: `cl` must say "Version 19.4x ... for x64". A plain PowerShell fails with
"Cannot open include file: 'stddef.h'". If the environment is wrong, stop and tell me —
do not try to work around it.

```
cd D:\Source\FinceptTerminal\fincept-qt
cmake --preset win-dev                 # configure once (fast incremental dev build)
cmake --build --preset win-dev         # rebuild after every change
.\build\win-dev\FinceptTerminal.exe    # run
```
- `win-release` is for a final optimised build only.
- OpenSSL comes from vcpkg at `D:\Source\vcpkg` (`openssl:x64-windows`), found via the
  `VCPKG_ROOT` env var. zlib on Windows is Qt's bundled copy. "Could NOT find Vulkan/GDAL"
  and the md4c `cmake_minimum_required` deprecation warning during configure are harmless.
- Qt is found via `QT_DIR` = `C:\Qt\6.8.3\msvc2022_64`. Do not change the Qt pin settings
  (`FINCEPT_QT_PIN_MODE`) or the toolchain version checks in CMakeLists.txt.
- Requires MSVC 19.40+ (VS 2022 17.10+). Keep >= 5 GB RAM free while building.
  If builds crawl, cap jobs: `-DFINCEPT_MAX_COMPILE_JOBS=4`.
- `Launch-VsDevShell.ps1` also resets `VCPKG_ROOT` to VS's bundled vcpkg. Set
  `$env:VCPKG_ROOT = 'D:\Source\vcpkg'` after entering the shell — CMakeLists.txt looks for
  OpenSSL under `$ENV{VCPKG_ROOT}` whenever a CMakeLists edit triggers a re-configure.
- Close any running `FinceptTerminal.exe` from `build\win-dev` before building, or the link fails
  with LNK1168 (exe locked). The app opens on a PIN lock screen; only I can unlock it.
- A re-configure recompiles QXlsx, which prints ~200 `D9025` command-line overrides, and
  windeployqt warns about missing `Qt6SerialPort.dll`. Both are pre-existing; only `warning C####`
  lines from our sources count as new warnings.
- A one-line change should rebuild in seconds. If it takes minutes, check free RAM first.
- Build output (`fincept-qt/build*/`) is git-ignored. Never commit build artefacts.
- There is no guest mode in this version: the app needs a (free) Fincept account and must reach
  `https://api.fincept.in` (auth, macro, news, LLM proxy). Bitdefender flags that domain as an
  "infected web resource" — it is allow-listed by domain in Bitdefender on this machine.
  A "Network error" at sign-in means that host is being blocked, not that the build is broken.
- First launch: the app sets up its own Python venvs
  (`venv-numpy1`, `venv-numpy2`) via uv — let that finish before testing Python-backed screens.
- Python scripts live in `fincept-qt/scripts/`. Test one standalone, e.g.
  `python scripts/yfinance_data.py quote AAPL` (from `fincept-qt`).

## Where things live (`fincept-qt/`)
- `src/app/` — main window; screens registered in `WindowFrame_Setup.cpp`
- `src/screens/<feature>/` — UI only
- `src/services/` — data fetching, caching, processing (incl. `backtesting/`)
- `src/trading/` — order engine, paper trading, `brokers/` (ibkr, saxo, alpaca, tradier, …)
- `src/python/` — Python bridge (PythonRunner, PythonSetupManager)
- `src/ui/` — shared widgets, theme, navigation (ToolBar, CommandBar)
- `scripts/` — Python data fetchers and analytics, called by C++ as (command, JSON)
- `docs/CPP_CONTRIBUTOR_GUIDE.md`, `docs/PYTHON_CONTRIBUTOR_GUIDE.md`, `docs/ARCHITECTURE.md`
  (repo root `docs/`)
- `docs/custom/` — my step specs. Upstream's `.gitignore` has a blanket `*.md` rule, so these are
  untracked unless added with `git add -f`.
- `src/screens/options_lab/` — Options Lab tab (embeds the goldie-oci dashboard). URL comes from
  AppConfig key `options_lab/base_url` (default `http://100.89.79.67:8095`).
- `tools/optionslab_mcp/` (repo root) — read-only Python MCP server over the Options Lab API, registered
  in Fincept's MCP Servers screen as "Options Lab". Own venv (`.venv`, git-ignored); tests:
  `.venv\Scripts\python.exe -m pytest -q`. Its README has the exact MCP Servers values and how AI Chat
  calls external tools. Keep every tool GET-only; never expose Options Lab's POST/DELETE endpoints.

## Code rules (from the upstream contributor guide)
- Screens render UI only. No HTTP and no business logic in `*Screen.cpp`; that goes in a `*Service`.
- Use `Result<T>`, `LOG_INFO/LOG_ERROR`, `EventBus`, `AppConfig`. No magic strings.
- Reuse `ui::Card`, `ui::DataTable`, `ui::SearchBar`, `ui::StatusBadge`.
- Pointer-to-member `connect(...)` only; `Q_OBJECT` in every QObject subclass.
- UI on the main thread; background work via QThread/QtConcurrent, results back via signals.
- New screen: folder under `src/screens/`, add .cpp to `SCREEN_SOURCES` in CMakeLists.txt **and**
  to the `SKIP_UNITY_BUILD_INCLUSION` list below it (every screen is in both), register lazily in
  `WindowFrame_Setup.cpp`, add the tab title to the map in `DockScreenRouter.cpp`, add ToolBar +
  CommandBar entries.
- Never construct a `QWebEngineView` without first checking `QtWebEngineProcess` exists at runtime
  (see `KLineChartWidget.cpp` / `OptionsLabScreen.cpp`) — a missing helper aborts the whole app.
- Style: 4 spaces, 120 cols, snake_case functions/vars, PascalCase types, trailing `_` on members.
- Prefer adding new files over editing upstream files, so upstream merges stay easy.
  When an upstream file must change, keep the edit small and mark it `// [equity-custom]`.

## Broker setup
- STATUS: IBKR is NOT connected. Test all trading changes with Fincept's built-in paper-trading
  engine (`src/trading/PaperTrading.*`) until a working IBKR path exists.
- The IBKR paper username is already used by a headless **IB Gateway (TWS API) on goldie-oci**
  (Docker, `EXISTING_SESSION_DETECTED_ACTION: primary`, `READ_ONLY_API: yes`, port 4002 bound to
  127.0.0.1) for the separate options-pricing project. IBKR allows one session per username, and
  that server session always reclaims it — so a local Client Portal Gateway login with the paper
  user will keep getting dropped. Do not use the Client Portal Gateway with the paper login, and
  never change/reset the paper password (it would break goldie-oci's automated login).
- Planned path: reach the goldie-oci Gateway via SSH tunnel (`ssh -L 4002:127.0.0.1:4002 goldie-oci`)
  with a distinct TWS API client ID (options-pricing uses 17/19/21). Fincept's existing
  `IBKRBroker` speaks the Client Portal REST API, not the TWS socket API, so this needs a new
  adapter or a local bridge — design it before building it.
- Paper account: an IBKR paper account (DU…; number deliberately not recorded here — this repo is public). Background on the existing Client Portal adapter
  (`src/trading/brokers/ibkr/IBKRBroker.cpp`; ACCOUNT ID = DU…, GATEWAY URL = local Client Portal
  Gateway): it does no custom SSL handling, so it only works with the gateway set to
  `listenSsl: false` at `http://localhost:5000`; it never calls `ssodh/init` or `/tickle`, so
  sessions are not established or kept alive. Any TLS relaxation must be limited to localhost.

## Trading safety (non-negotiable)
- Every trading change is proven in **paper trading** before it can touch a live broker account.
- Never weaken order validation, risk checks or confirmation gates to make something work.
- Broker keys stay in SecureStorage / the OS keychain. Never hard-code, log or commit credentials,
  tokens or account numbers.
- Never place, modify or cancel a live order as part of testing.

## How to work in this repo
- One goal per session. State it at the start.
- Read the relevant code before changing it; for anything beyond a small fix, plan first.
- Never claim a change is done without proof: it builds with no new warnings, the app launches,
  and the changed screen or behaviour has been exercised. Say exactly what was checked and what wasn't.
- No placeholders, stubs or TODOs presented as working features.
- Commit small, on `equity-custom`, with conventional messages (`feat:`, `fix:`, `refactor:`, `docs:`).
- Push only to `origin`.
