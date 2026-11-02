from localforge import agent, tools
from localforge.browser import Browser


def test_new_tags_parse():
    acts = tools.parse('Plan:\n1. find login\n<note>Price is £5 at https://a.com/x</note>\n'
                       '<page find="sign in"/><back/><wait for="Results"/><wait/>')
    assert [(a.kind, a.path) for a in acts] == [("page", "sign in"), ("back", ""), ("wait", "Results"), ("wait", "")]
    assert all(a.browser_read for a in acts)
    assert tools.notes('<note> Price  is £5 </note>') == ["Price is £5"]
    assert "Price" not in tools.prose_of("<note>Price</note> done")


def test_elements_prefer_fields_and_visible_ones():
    els = [{"s": f"[{i}] link \"menu {i}\"", "v": 0, "f": 0} for i in range(1, 200)]
    els[150] = {"s": "[151] button \"Add to basket\"", "v": 1, "f": 0}
    els[180] = {"s": "[181] input:text \"Search\"", "v": 0, "f": 1}
    picked, hidden = Browser.pick_elements(els)
    assert len(picked) == Browser.MAX_ELEMENTS and hidden == 199 - Browser.MAX_ELEMENTS
    assert any("Add to basket" in p for p in picked) and any("Search" in p for p in picked)
    nums = [int(p[1:p.index("]")]) for p in picked]
    assert nums == sorted(nums)
    found, _ = Browser.pick_elements(els, "basket")
    assert found == ['[151] button "Add to basket"']


class FakeEngine:
    loaded = True
    last_tps = 30.0

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def chat(self, messages, max_tokens, temperature, on_piece, think=False, think_cap=0):
        self.calls.append((think, think_cap, messages[-1]["content"]))
        text, finish, reasoning = self.replies.pop(0)
        self.last_finish, self.last_reasoning = finish, reasoning
        return text


class FakeSettings(dict):
    def get(self, k):
        return dict.get(self, k)


def _app(engine):
    app = object.__new__(agent.App)
    app.engine = engine
    app.settings = FakeSettings(think=False, thinkBrowser=True, maxTokens=2048, nCtx=16384, temperature=0.4)
    app.messages, app.total_steps, app.steps_left, app.stop_requested = [], 0, 30, False
    app.rethink, app.browser_task, app.task_notes, app.visited = True, True, [], []
    app._history = lambda shrink=1.0, extra="": [{"role": "user", "content": "task" + extra}]
    return app


def test_capped_thinking_then_acts():
    eng = FakeEngine([("", "think_cap", "I should open the site and look for the login button"),
                      ('1. open site\n<browse url="https://a.com"/>', "stop", "")])
    app = _app(eng)
    out = app._chat(lambda p: None, {"text": ""})
    assert "browse" in out
    assert eng.calls[0][:2] == (True, agent.App.PLAN_THINK)
    assert eng.calls[1][0] is False and "login button" in eng.calls[1][2]
    assert app.rethink is False


def test_no_thinking_when_setting_off():
    eng = FakeEngine([("ok", "stop", "")])
    app = _app(eng)
    app.settings["thinkBrowser"] = False
    app._chat(lambda p: None, {"text": ""})
    assert eng.calls[0][:2] == (False, 0)


def test_progress_block_lists_notes_and_pages():
    app = _app(FakeEngine([]))
    app.task_notes = ["Cheapest is £5"]
    app.visited = [("Shop", "https://shop.com/a")]
    p = app._progress("find the cheapest kettle")
    assert "cheapest kettle" in p and "£5" in p and "shop.com/a" in p and "Step 1" in p


def test_task_detection():
    def is_task(t):
        return bool((agent.WEB_WORDS.search(t) or agent.URL_IN.search(t)) and agent.TASK_WORDS.search(t))
    assert is_task("go to amazon.co.uk and add the cheapest usb cable to my basket")
    assert is_task("open the website and fill in the contact form")
    assert not is_task("what is the capital of france")
