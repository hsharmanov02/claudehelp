from localforge import agent, tools


class FakeBrowser:
    url = "https://news.ycombinator.com/"
    def extract(self, q=""):
        return "Table 1 (3 rows):\n1. | Story -> https://a.com/x"
    def extract_link(self, n, row=False):
        return None


def _app():
    app = object.__new__(agent.App)
    app.browser = FakeBrowser()
    app.moved = app.move_nudged = False
    app.messages = []
    app._msg = lambda role, text, model_text=None: app.messages.append(
        {"role": role, "text": text, "modelText": model_text}) or app.messages[-1]
    return app


def test_exec_browser_runs_extract():
    app = _app()
    r = app._exec_browser(tools.Action("extract", ""))
    assert r.startswith("Data on the page") and "click row" in r
    assert not app.moved


def test_nudge_when_answering_without_opening():
    app = _app()
    app._msg("user", "open the new section and tell me the newest 5 titles",
             "[I'm looking at this page in your browser: x] open the new section")
    app._msg("tool", "Data on the page ...")
    assert app._skipped_navigation() is True
    assert app._skipped_navigation() is False      # only once


def test_no_nudge_for_plain_questions():
    app = _app()
    app._msg("user", "summarise this page", "[I'm looking at this page in your browser: x] summarise")
    assert app._skipped_navigation() is False
