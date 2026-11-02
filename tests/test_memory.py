from localforge import agent, config


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALFORGE_HOME", str(tmp_path))
    app = object.__new__(agent.App)
    app.settings = config.Settings()

    class Index:
        def overview(self):
            return "x"
    app.index = Index()
    app.messages = []
    return app


def _add(app, role, text, model_text=None):
    app.messages.append({"id": len(app.messages), "role": role, "text": text, "modelText": model_text, "actions": []})


def test_current_message_lists_earlier_ones_with_previous_marked(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    _add(app, "user", "My dog is called Biscuit")
    _add(app, "assistant", "Noted.")
    _add(app, "user", "What is the capital of Japan?")
    _add(app, "assistant", "Tokyo.")
    _add(app, "user", "What did I just ask?", "[page] What did I just ask?")
    h = app._history()
    assert "Never say you can't remember" in h[0]["content"]
    assert [m["role"] for m in h[1:]] == ["user", "assistant", "user", "assistant", "user"]
    assert h[1]["content"] == "My dog is called Biscuit"          # older turns stay unchanged (prompt cache)
    last = h[-1]["content"]
    assert last.startswith("[page] What did I just ask?")
    assert 'my previous message, the one just before this one, was: "What is the capital of Japan?"' in last
    assert '1. "My dog is called Biscuit"' in last


def test_no_note_when_not_referring_back(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    _add(app, "user", "My dog is called Biscuit")
    _add(app, "assistant", "Noted.")
    _add(app, "user", "Give me a fun fact about octopuses")
    assert app._history()[-1]["content"] == "Give me a fun fact about octopuses"


def test_refers_back_phrases():
    for t in ("what was my last prompt?", "What did I just ask?", "repeat my previous question",
              "what did i say before", "remind me what I asked"):
        assert agent.REFERS_BACK.search(t), t
    for t in ("what is the capital of Japan?", "write a python script", "open the first link"):
        assert not agent.REFERS_BACK.search(t), t


def test_no_recap_on_first_message(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    _add(app, "user", "hello")
    assert app._history()[-1]["content"] == "hello"
