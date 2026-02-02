LOGIN_URL = "https://www.overleaf.com/login"
PROJECT_URL = "https://www.overleaf.com/project"


def login_via_qt():
    """Open a Qt WebEngine browser to login and capture cookies + CSRF.

    Returns a dict: {"cookie": {name: value, ...}, "csrf": str}
    Requires PySide6 with Qt WebEngine.
    """
    try:
        from PySide6.QtCore import QUrl, QCoreApplication
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings, QWebEnginePage
    except Exception as e:
        raise RuntimeError(
            "PySide6 (Qt WebEngine) is required for this command. Install with 'conda install -c conda-forge pyside6' or 'pip install PySide6'."
        ) from e

    class _Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.webview = QWebEngineView()
            self._cookies = {}
            self._csrf = ""
            self._done = False

            self.profile = QWebEngineProfile(self.webview)
            self.cookie_store = self.profile.cookieStore()
            self.cookie_store.cookieAdded.connect(self._on_cookie_added)
            self.profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
            self.profile.settings().setAttribute(QWebEngineSettings.JavascriptEnabled, True)

            webpage = QWebEnginePage(self.profile, self)
            self.webview.setPage(webpage)
            self.webview.load(QUrl.fromUserInput(LOGIN_URL))
            self.webview.loadFinished.connect(self._on_load_finished)

            self.setCentralWidget(self.webview)
            self.resize(700, 900)

        def _on_cookie_added(self, cookie):
            name = cookie.name().data().decode("utf-8")
            if name in ("overleaf_session2", "GCLB"):
                self._cookies[name] = cookie.value().data().decode("utf-8")

        def _on_load_finished(self):
            # When arriving at dashboard, extract csrf from meta
            if self.webview.url().toString().startswith(PROJECT_URL):
                def _cb(result):
                    self._csrf = result or ""
                    self._done = True
                    QCoreApplication.quit()

                js = """
                (function(){
                  var m = document.querySelector('meta[name="ol-csrfToken"]');
                  return m ? m.content : '';
                })();
                """
                self.webview.page().runJavaScript(js, 0, _cb)

    app = QApplication([])
    win = _Window()
    win.show()
    app.exec()

    if not win._done:
        return None
    return {"cookie": win._cookies, "csrf": win._csrf}
