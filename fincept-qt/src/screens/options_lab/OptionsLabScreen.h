#pragma once
// [equity-custom] Options Lab — embeds the Options Lab dashboard (served from
// goldie-oci over Tailscale) as a Fincept tab. UI only: the screen loads a URL
// read from AppConfig and makes no HTTP calls of its own.
#include <QElapsedTimer>
#include <QUrl>
#include <QWidget>

class QLabel;
class QStackedWidget;
class QTimer;
#ifdef HAS_QT_WEBENGINE
class QWebEngineView;
#endif

namespace fincept::screens {

/// Options Lab — a QWebEngineView pointed at `options_lab/base_url`.
///
/// Three states, so the panel is never blank:
///   connecting  — first load / retry in flight (a dead host can take ~20 s to fail)
///   page        — the dashboard itself
///   unreachable — load failed or timed out; Retry + Open in browser
/// Without WebEngine (compile-time or missing runtime helper) a label explains
/// why and offers Open in browser.
class OptionsLabScreen : public QWidget {
    Q_OBJECT
  public:
    explicit OptionsLabScreen(QWidget* parent = nullptr);

  protected:
    /// The web view is created and the first load started on first show, not in
    /// the constructor — a restored-but-hidden tab costs nothing.
    void showEvent(QShowEvent* event) override;

  private:
    QWidget* build_top_bar();
    QWidget* build_message_page(const QString& object_name, QLabel** message_out, bool with_retry);
    void open_in_browser();
    void reload();
    /// Shows `message` on the unreachable page for a state Retry cannot fix
    /// (bad URL, no WebEngine): hides Retry, disables Reload, never builds a view.
    void show_unavailable(const QString& message);

#ifdef HAS_QT_WEBENGINE
    void ensure_web_view();
    void on_load_started();
    void on_load_finished(bool ok);
    void on_load_timeout();
#endif

    QUrl url_;
    QStackedWidget* stack_ = nullptr;
    QWidget* connecting_page_ = nullptr;
    QWidget* unreachable_page_ = nullptr;
    QLabel* connecting_label_ = nullptr;
    QLabel* unreachable_label_ = nullptr;
    QWidget* retry_btn_ = nullptr;
    QWidget* reload_btn_ = nullptr;
    bool web_available_ = true;
#ifdef HAS_QT_WEBENGINE
    QWebEngineView* web_view_ = nullptr;
    QTimer* load_timeout_ = nullptr;
    QElapsedTimer load_clock_;
    bool timed_out_ = false;
#endif
};

} // namespace fincept::screens
