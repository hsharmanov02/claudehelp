"""Runs code written by the assistant and returns everything it printed (or the error)."""
import io
import os
import sys
import time
import traceback

MAX_OUTPUT = 6000


class _Timeout(BaseException):
    pass


def run(code, workdir, timeout):
    out = io.StringIO()
    old_out, old_err, old_in = sys.stdout, sys.stderr, sys.stdin
    old_cwd = os.getcwd()
    deadline = time.monotonic() + float(timeout)

    def tracer(frame, event, arg):
        if time.monotonic() > deadline:
            raise _Timeout()
        return tracer

    env = {"__name__": "__main__", "__builtins__": __builtins__}
    status = "ok"
    try:
        os.makedirs(workdir, exist_ok=True)
        os.chdir(workdir)
        sys.stdout = sys.stderr = out
        sys.stdin = io.StringIO("")  # input() fails fast instead of hanging
        sys.settrace(tracer)
        exec(compile(code, "main.py", "exec"), env)
    except _Timeout:
        status = "timeout"
        print("\n[Stopped: the code ran longer than %s seconds]" % timeout, file=out)
    except SystemExit as e:
        if e.code not in (None, 0):
            status = "error"
            print("[exit code %s]" % e.code, file=out)
    except BaseException as e:
        status = "error"
        tb = e.__traceback__.tb_next if e.__traceback__ is not None else None  # hide this runner's frame
        traceback.print_exception(type(e), e, tb, file=out)
    finally:
        sys.settrace(None)
        sys.stdout, sys.stderr, sys.stdin = old_out, old_err, old_in
        try:
            os.chdir(old_cwd)
        except OSError:
            pass

    text = out.getvalue()
    if len(text) > MAX_OUTPUT:
        half = MAX_OUTPUT // 2
        text = text[:half] + "\n...[output trimmed]...\n" + text[-half:]
    if not text.strip():
        text = "(no output)" if status == "ok" else text
    return status + "\n" + text


def run_sql(sql, workdir, timeout):
    """Runs SQL against a SQLite database (localforge.db in the run folder) and prints any results."""
    code = (
        "import sqlite3\n"
        "con = sqlite3.connect('localforge.db')\n"
        "sql = " + repr(sql) + "\n"
        "buf = ''\n"
        "for line in sql.splitlines(keepends=True):\n"
        "    buf += line\n"
        "    if sqlite3.complete_statement(buf):\n"
        "        stmt = buf.strip(); buf = ''\n"
        "        cur = con.execute(stmt)\n"
        "        if cur.description:\n"
        "            cols = [d[0] for d in cur.description]\n"
        "            rows = cur.fetchall()\n"
        "            print(' | '.join(cols))\n"
        "            for r in rows[:200]: print(' | '.join(str(v) for v in r))\n"
        "            print('(%d rows)' % len(rows))\n"
        "if buf.strip():\n"
        "    cur = con.execute(buf)\n"
        "    if cur.description:\n"
        "        for r in cur.fetchall()[:200]: print(r)\n"
        "con.commit()\n"
        "print('[database: ' + __import__('os').path.abspath('localforge.db') + ']')\n"
    )
    return run(code, workdir, timeout)
