# Step 2 — "Options Lab" screen inside Fincept

**Goal:** open the Options Lab dashboard (running on goldie-oci) as a tab inside Fincept.
**Scope:** one new screen. No changes to Options Lab. No trading code touched.

## Facts
- Options Lab URL (over Tailscale): `http://100.89.79.67:8095` — web UI at `/`, JSON API under `/api/...`.
- Fincept builds with Qt WebEngine on this machine (`HAS_WEBENGINE` in CMakeLists.txt →
  compile definition `HAS_QT_WEBENGINE`). `src/screens/equity_trading/EquityChartPanel.cpp` is a
  working example of a `QWebEngineView` with a non-WebEngine fallback.

## Build
1. New folder `src/screens/options_lab/` with `OptionsLabScreen.h/.cpp` (namespace `fincept::screens`).
   - `#ifdef HAS_QT_WEBENGINE`: a `QWebEngineView` loading the URL; a thin top bar with the URL,
     a **Reload** button and an **Open in browser** button (`QDesktopServices::openUrl`).
   - `#else`: a label explaining WebEngine is unavailable + the **Open in browser** button.
   - If the page fails to load (`loadFinished(false)`), show a clear message:
     "Options Lab not reachable at <url> — is Tailscale connected and the service running on goldie-oci?"
     with a Retry button. Never a blank panel.
2. URL is configurable, not hard-coded in the screen: read it from `AppConfig`
   (new key `options_lab/base_url`, default `http://100.89.79.67:8095`).
3. Add the .cpp to `SCREEN_SOURCES` in CMakeLists.txt.
4. Register lazily in `src/app/WindowFrame_Setup.cpp`:
   `dock_router_->register_factory("options_lab", ...)`.
5. Add navigation: a menu item in `src/ui/navigation/ToolBar.cpp` and a command in
   `src/ui/navigation/CommandBar.cpp` (so "Options Lab" works from the command bar).
6. Mark any edits to existing upstream files with `// [equity-custom]`.

## Done when
- `cmake --build --preset win-dev` succeeds with no new warnings.
- Fincept launches; "Options Lab" opens from the menu and from the command bar.
- The Options Lab dashboard renders and is usable (ticker tabs, position cards, charts).
- With Tailscale disconnected, the screen shows the "not reachable" message, and Retry works
  once Tailscale is back.
- Report exactly what was checked, with a screenshot of the working tab.
