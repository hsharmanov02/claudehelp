"""Starts LocalForge: the local server plus a desktop window (or your browser)."""
import argparse
import sys
import threading
import time
import webbrowser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 3 and argv[0] == "--run-python":
        from .runner import run_python_inline
        sys.exit(run_python_inline(argv[1], argv[2]))

    ap = argparse.ArgumentParser(prog="LocalForge")
    ap.add_argument("--browser", action="store_true", help="open in your web browser instead of an app window")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-open", action="store_true", help="just run the server (for testing)")
    ap.add_argument("--selftest", action="store_true", help="check the engine, Hugging Face and a tiny model, then exit")
    ap.add_argument("--bench", nargs="+", metavar="MODEL.gguf", help="measure speed of one or more models, then exit")
    ap.add_argument("--ctx", type=int, default=8192, help="context size for --bench")
    args = ap.parse_args(argv)

    if args.bench:
        from .bench import run as bench
        sys.exit(bench(args.bench, args.ctx))

    if args.selftest:
        from .selftest import run
        sys.exit(run())

    from .agent import App
    from . import server

    app = App()
    holder = {}

    def pick_file(types):
        w = holder.get("window")
        if not w:
            return None
        import webview
        r = w.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=tuple(types))
        return r[0] if r else None

    httpd = server.serve(app, args.port, pick_file)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    # Its own port = a different origin from the app, so code run there can't read the app's page or token.
    app.browser.sandbox_url = f"http://127.0.0.1:{server.serve_sandbox()}/sandbox"
    print(f"LocalForge is running at {url}", flush=True)

    if args.no_open:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        app.engine.unload()
        return

    if not args.browser:
        try:
            import webview
            win = webview.create_window("LocalForge", url, width=1280, height=860, min_size=(820, 560),
                                        background_color="#0f1115")
            holder["window"] = win
            webview.start()
            app.engine.unload()
            return
        except Exception as e:  # no WebView2 / pywebview: fall back to the browser
            print(f"App window unavailable ({e}); opening your browser instead.", flush=True)

    webbrowser.open(url)
    print("Close this window to quit LocalForge.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    app.engine.unload()


if __name__ == "__main__":
    main()
