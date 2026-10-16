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
- A one-line change should rebuild in seconds. If it takes minutes, check free RAM first.
- Build output (`fincept-qt/build*/`) is git-ignored. Never commit build artefacts.
- First launch: choose "Continue as Guest". The app sets up its own Python venvs
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

## Code rules (from the upstream contributor guide)
- Screens render UI only. No HTTP and no business logic in `*Screen.cpp`; that goes in a `*Service`.
- Use `Result<T>`, `LOG_INFO/LOG_ERROR`, `EventBus`, `AppConfig`. No magic strings.
- Reuse `ui::Card`, `ui::DataTable`, `ui::SearchBar`, `ui::StatusBadge`.
- Pointer-to-member `connect(...)` only; `Q_OBJECT` in every QObject subclass.
- UI on the main thread; background work via QThread/QtConcurrent, results back via signals.
- New screen: folder under `src/screens/`, add .cpp to `SCREEN_SOURCES` in CMakeLists.txt,
  register lazily in `WindowFrame_Setup.cpp`, add ToolBar + CommandBar entries.
- Style: 4 spaces, 120 cols, snake_case functions/vars, PascalCase types, trailing `_` on members.
- Prefer adding new files over editing upstream files, so upstream merges stay easy.
  When an upstream file must change, keep the edit small and mark it `// [equity-custom]`.

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
