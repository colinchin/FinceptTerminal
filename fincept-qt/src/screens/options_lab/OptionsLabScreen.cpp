// OptionsLabScreen.cpp — see header. [equity-custom]
#include "screens/options_lab/OptionsLabScreen.h"

#include "core/config/AppConfig.h"
#include "core/logging/Logger.h"
#include "ui/theme/Theme.h"

#include <QDesktopServices>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QShowEvent>
#include <QStackedWidget>
#include <QTimer>
#include <QVBoxLayout>

#ifdef HAS_QT_WEBENGINE
#    include <QCoreApplication>
#    include <QFileInfo>
#    include <QLibraryInfo>
#    include <QWebEngineLoadingInfo>
#    include <QWebEnginePage>
#    include <QWebEngineView>
#endif

namespace fincept::screens {

using namespace fincept::ui;

namespace {

constexpr const char* kTag = "OptionsLab";
constexpr const char* kCfgBaseUrl = "options_lab/base_url";
constexpr const char* kDefaultBaseUrl = "http://100.89.79.67:8095";

// Upper bound for one page load before we give up and show the "not reachable"
// page. With Tailscale down the TCP connect to the 100.x address is silently
// dropped and Chromium only fails when the OS connect timeout (~21 s on Windows)
// expires; the full dashboard (incl. plotly.min.js) loads in well under this
// over the Tailscale relay, so 30 s only ever trips on a dead host.
constexpr int kLoadTimeoutMs = 30000;

const char* const kMono = "font-family:'Consolas','Courier New',monospace;";

QString button_ss() {
    return QString("QPushButton{background:transparent;color:%1;border:1px solid %2;border-radius:2px;"
                   "padding:3px 12px;font-size:11px;font-weight:700;%5}"
                   "QPushButton:hover{border-color:%3;color:%4;}")
        .arg(colors::TEXT_SECONDARY(), colors::BORDER_DIM(), colors::AMBER(), colors::TEXT_PRIMARY(), kMono);
}

QPushButton* make_button(const QString& text, QWidget* parent) {
    auto* b = new QPushButton(text, parent);
    b->setCursor(Qt::PointingHandCursor);
    b->setStyleSheet(button_ss());
    return b;
}

#ifdef HAS_QT_WEBENGINE
// Same probe as KLineChartWidget: HAS_QT_WEBENGINE only says WebEngine was
// present at build time. Constructing a QWebEngineView without the
// QtWebEngineProcess helper aborts the whole process (uncatchable), so check the
// locations Qt searches before creating one.
bool webengine_runtime_available() {
#    ifdef Q_OS_WIN
    const QString proc = QStringLiteral("QtWebEngineProcess.exe");
#    else
    const QString proc = QStringLiteral("QtWebEngineProcess");
#    endif
    const QString app = QCoreApplication::applicationDirPath();
    const QStringList candidates = {
        QLibraryInfo::path(QLibraryInfo::LibraryExecutablesPath) + QStringLiteral("/") + proc,
        app + QStringLiteral("/") + proc,
        app + QStringLiteral("/bin/") + proc,
    };
    for (const QString& c : candidates) {
        if (QFileInfo::exists(c))
            return true;
    }
    return false;
}
#endif

} // namespace

OptionsLabScreen::OptionsLabScreen(QWidget* parent) : QWidget(parent) {
    setObjectName("optionsLabScreen");
    setStyleSheet(QString("#optionsLabScreen{background:%1;}").arg(colors::BG_BASE()));

    const QString configured =
        AppConfig::instance().get(QString::fromLatin1(kCfgBaseUrl), QString::fromLatin1(kDefaultBaseUrl)).toString();
    url_ = QUrl(configured.trimmed(), QUrl::StrictMode);
    const bool url_ok = url_.isValid() && (url_.scheme() == "http" || url_.scheme() == "https");
    LOG_INFO(kTag, QString("Options Lab URL: %1 (%2)").arg(configured, url_ok ? "valid" : "INVALID"));

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);
    root->addWidget(build_top_bar());

    stack_ = new QStackedWidget(this);
    connecting_page_ = build_message_page("optionsLabConnecting", &connecting_label_, false);
    unreachable_page_ = build_message_page("optionsLabUnreachable", &unreachable_label_, true);
    stack_->addWidget(connecting_page_);
    stack_->addWidget(unreachable_page_);
    root->addWidget(stack_, 1);

    connecting_label_->setText(tr("Connecting to Options Lab at %1 …").arg(url_.toString()));
    unreachable_label_->setText(tr("Options Lab not reachable at %1 — is Tailscale connected and the service "
                                   "running on goldie-oci?")
                                    .arg(url_.toString()));

    if (!url_ok) {
        show_unavailable(tr("Invalid Options Lab URL \"%1\" in setting %2. "
                            "Expected an http:// or https:// address.")
                             .arg(configured, QString::fromLatin1(kCfgBaseUrl)));
        return;
    }

#ifdef HAS_QT_WEBENGINE
    if (!webengine_runtime_available()) {
        LOG_WARN(kTag, "Qt WebEngine runtime not found (QtWebEngineProcess missing) — showing browser fallback");
        show_unavailable(tr("Embedded browser unavailable: the Qt WebEngine runtime "
                            "(QtWebEngineProcess) is missing from this install.\n"
                            "Open Options Lab in your browser instead."));
    }
#else
    show_unavailable(tr("Embedded browser unavailable: this build has no Qt WebEngine.\n"
                        "Open Options Lab in your browser instead."));
#endif
}

void OptionsLabScreen::show_unavailable(const QString& message) {
    web_available_ = false;
    unreachable_label_->setText(message);
    retry_btn_->hide();
    reload_btn_->setEnabled(false);
    stack_->setCurrentWidget(unreachable_page_);
}

QWidget* OptionsLabScreen::build_top_bar() {
    auto* bar = new QWidget(this);
    bar->setObjectName("optionsLabTopBar");
    bar->setStyleSheet(QString("#optionsLabTopBar{background:%1;border-bottom:1px solid %2;}")
                           .arg(colors::BG_SURFACE(), colors::BORDER_DIM()));
    auto* h = new QHBoxLayout(bar);
    h->setContentsMargins(8, 4, 8, 4);
    h->setSpacing(6);

    auto* title = new QLabel(tr("OPTIONS LAB"), bar);
    title->setStyleSheet(
        QString("color:%1;font-size:12px;font-weight:700;background:transparent;%2").arg(colors::AMBER(), kMono));
    h->addWidget(title);

    auto* url_label = new QLabel(url_.toString(), bar);
    url_label->setObjectName("optionsLabUrl");
    url_label->setTextInteractionFlags(Qt::TextSelectableByMouse);
    url_label->setStyleSheet(
        QString("color:%1;font-size:11px;background:transparent;%2").arg(colors::TEXT_SECONDARY(), kMono));
    h->addWidget(url_label);
    h->addStretch(1);

    auto* reload_btn = make_button(tr("Reload"), bar);
    reload_btn->setObjectName("optionsLabReload");
    connect(reload_btn, &QPushButton::clicked, this, &OptionsLabScreen::reload);
    h->addWidget(reload_btn);
    reload_btn_ = reload_btn;

    auto* open_btn = make_button(tr("Open in browser"), bar);
    open_btn->setObjectName("optionsLabOpenBrowser");
    connect(open_btn, &QPushButton::clicked, this, &OptionsLabScreen::open_in_browser);
    h->addWidget(open_btn);

    return bar;
}

QWidget* OptionsLabScreen::build_message_page(const QString& object_name, QLabel** message_out, bool with_retry) {
    auto* page = new QWidget(this);
    page->setObjectName(object_name);
    page->setStyleSheet(QString("#%1{background:%2;}").arg(object_name, colors::BG_BASE()));

    auto* v = new QVBoxLayout(page);
    v->setContentsMargins(24, 24, 24, 24);
    v->setSpacing(12);
    v->addStretch(1);

    auto* msg = new QLabel(page);
    msg->setWordWrap(true);
    msg->setAlignment(Qt::AlignCenter);
    msg->setTextInteractionFlags(Qt::TextSelectableByMouse);
    msg->setStyleSheet(QString("color:%1;font-size:13px;background:transparent;%2")
                           .arg(with_retry ? colors::AMBER() : colors::TEXT_SECONDARY(), kMono));
    v->addWidget(msg);
    *message_out = msg;

    if (with_retry) {
        auto* row = new QHBoxLayout;
        row->addStretch(1);
        auto* retry_btn = make_button(tr("Retry"), page);
        retry_btn->setObjectName("optionsLabRetry");
        connect(retry_btn, &QPushButton::clicked, this, &OptionsLabScreen::reload);
        row->addWidget(retry_btn);
        retry_btn_ = retry_btn;
        auto* open_btn = make_button(tr("Open in browser"), page);
        connect(open_btn, &QPushButton::clicked, this, &OptionsLabScreen::open_in_browser);
        row->addWidget(open_btn);
        row->addStretch(1);
        v->addLayout(row);
    }

    v->addStretch(1);
    return page;
}

void OptionsLabScreen::showEvent(QShowEvent* event) {
    QWidget::showEvent(event);
#ifdef HAS_QT_WEBENGINE
    ensure_web_view();
#endif
}

void OptionsLabScreen::open_in_browser() {
    LOG_INFO(kTag, QString("Open in browser: %1").arg(url_.toString()));
    if (!QDesktopServices::openUrl(url_))
        LOG_WARN(kTag, QString("QDesktopServices::openUrl failed for %1").arg(url_.toString()));
}

void OptionsLabScreen::reload() {
#ifdef HAS_QT_WEBENGINE
    if (!web_available_)
        return;
    if (!web_view_) {
        ensure_web_view();
        return;
    }
    // From the dashboard, Reload keeps the current page (and any in-page state
    // in the URL); from the connecting/unreachable pages, go back to the base URL.
    if (stack_->currentWidget() == web_view_) {
        LOG_INFO(kTag, "Reload requested");
        web_view_->reload();
    } else {
        LOG_INFO(kTag, QString("Retry requested: %1").arg(url_.toString()));
        stack_->setCurrentWidget(connecting_page_);
        web_view_->load(url_);
    }
#endif
}

#ifdef HAS_QT_WEBENGINE

void OptionsLabScreen::ensure_web_view() {
    if (web_view_ || !web_available_)
        return; // already built, or invalid URL / no runtime (message already shown)

    web_view_ = new QWebEngineView(this);
    web_view_->setObjectName("optionsLabWebView");
    stack_->addWidget(web_view_);

    load_timeout_ = new QTimer(this);
    load_timeout_->setSingleShot(true);
    load_timeout_->setInterval(kLoadTimeoutMs);
    connect(load_timeout_, &QTimer::timeout, this, &OptionsLabScreen::on_load_timeout);

    connect(web_view_, &QWebEngineView::loadStarted, this, &OptionsLabScreen::on_load_started);
    connect(web_view_, &QWebEngineView::loadFinished, this, &OptionsLabScreen::on_load_finished);
    // Diagnostics only: loadFinished(false) carries no reason, loadingChanged does.
    connect(web_view_->page(), &QWebEnginePage::loadingChanged, this, [](const QWebEngineLoadingInfo& info) {
        if (info.status() == QWebEngineLoadingInfo::LoadFailedStatus)
            LOG_WARN(kTag, QString("Load failed: %1 — %2 (code %3)")
                               .arg(info.url().toString(), info.errorString())
                               .arg(info.errorCode()));
    });

    stack_->setCurrentWidget(connecting_page_);
    web_view_->load(url_);
}

void OptionsLabScreen::on_load_started() {
    timed_out_ = false;
    load_clock_.start();
    load_timeout_->start();
    LOG_INFO(kTag, QString("Load started: %1").arg(url_.toString()));
}

void OptionsLabScreen::on_load_finished(bool ok) {
    load_timeout_->stop();
    const qint64 ms = load_clock_.isValid() ? load_clock_.elapsed() : -1;
    if (ok) {
        LOG_INFO(kTag, QString("Load finished OK in %1 ms").arg(ms));
        stack_->setCurrentWidget(web_view_);
        return;
    }
    LOG_WARN(kTag, QString("Load finished with failure after %1 ms%2").arg(ms).arg(timed_out_ ? " (timeout)" : ""));
    stack_->setCurrentWidget(unreachable_page_);
}

void OptionsLabScreen::on_load_timeout() {
    LOG_WARN(kTag, QString("Load timed out after %1 ms — stopping").arg(kLoadTimeoutMs));
    timed_out_ = true;
    web_view_->stop(); // emits loadFinished(false) → unreachable page
}

#endif

} // namespace fincept::screens
