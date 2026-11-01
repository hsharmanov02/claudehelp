import os
import tempfile
from pathlib import Path

os.environ.setdefault("LOCALFORGE_HOME", tempfile.mkdtemp())

from localforge import tools, runner, hf  # noqa: E402
from localforge.fileindex import FileIndex  # noqa: E402


def test_parse_tags_in_order():
    acts = tools.parse('<think>hmm</think>Ok\n<list path="C:\\Users\\a"/>\n<write path="C:\\x\\a.py">\nprint(1)\n</write>\n<run lang="js">```js\nconsole.log(1)\n```</run>')
    assert [a.kind for a in acts] == ["list", "write", "run"]
    assert acts[1].content == "print(1)"
    assert acts[2].path == "javascript" and acts[2].content == "console.log(1)"


def test_fallback_run_only_when_asked():
    reply = "Here:\n```python\nprint(2)\n```"
    assert tools.fallback_run("write a script", reply) is None
    a = tools.fallback_run("write and test a script", reply)
    assert a and a.path == "python"


def test_strip_thinking():
    assert tools.strip_thinking("<think>x</think>hi\u2063") == "hi"


def test_uncensored_filter_and_params():
    assert hf.is_uncensored("HauhauCS/Qwen3.8-27B-Uncensored-GGUF")
    assert not hf.is_uncensored("huihui-ai/Qwen3-8B-abliterated")
    assert not hf.is_uncensored("someone/Qwen3-8B-abliterated-uncensored")
    assert not hf.is_uncensored("Qwen/Qwen3-8B-GGUF")
    assert hf.params_of("Qwen3-30B-A3B-abliterated") == 30
    assert hf.active_params_of("Qwen3-30B-A3B-abliterated") == 3
    f = hf.HfFile("r", "x/Model-Q4_K_M.gguf", 1)
    assert f.quant == "Q4_K_M"


def test_runner_python_and_sql(tmp_path):
    r = runner.run("python", "print(sum(range(5)))", tmp_path)
    assert r.ok and r.output == "10"
    r = runner.run("sql", "create table t(a); insert into t values (3); select a*2 from t;", tmp_path)
    assert r.ok and "6" in r.output


def test_file_index_search(tmp_path, monkeypatch):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "my_cv.pdf").write_text("x")
    (tmp_path / "song.mp3").write_text("x")
    idx = FileIndex()
    monkeypatch.setattr(idx, "roots", lambda: [tmp_path])
    idx.build()
    hits = idx.search("find my cv pdf")
    assert hits and hits[0][0].endswith("my_cv.pdf")
    assert idx.search("music")[0][0].endswith("song.mp3")


def test_browser_tags():
    acts = tools.parse('<search query="gpu prices"/>\n<browse url="https://a.com"/>\n<click n="4"/>\n<type n="2" submit="true">hi</type>\n<js>return 1</js>')
    assert [a.kind for a in acts] == ["search", "browse", "click", "type", "js"]
    assert acts[0].browser_read and acts[1].browser_read and not acts[2].browser_read
    assert acts[3].extra == "submit" and acts[3].content == "hi"


def test_fetch_tag_and_js_lang():
    acts = tools.parse('<fetch url="https://x.com/a.json"/>')
    assert acts[0].kind == "fetch" and acts[0].browser_read
    assert runner.normalize("js") == "javascript" and runner.normalize("nodejs") == "node"
