package com.localforge.app.agent

import android.annotation.SuppressLint
import android.content.Context
import android.webkit.JavascriptInterface
import android.webkit.WebView
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject

/** Runs JavaScript in a hidden WebView (Chrome's V8 engine) — no setup needed. */
object JsRunner {
    private var webView: WebView? = null
    private var pending: CompletableDeferred<String>? = null

    private class Bridge {
        @JavascriptInterface
        fun done(json: String) {
            pending?.complete(json)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun view(ctx: Context): WebView = webView ?: WebView(ctx.applicationContext).also {
        it.settings.javaScriptEnabled = true
        it.addJavascriptInterface(Bridge(), "LFBridge")
        it.loadDataWithBaseURL("https://localforge.local/", "<html><body></body></html>", "text/html", "utf-8", null)
        webView = it
    }

    suspend fun run(ctx: Context, code: String, timeoutMs: Long): RunResult {
        val deferred = CompletableDeferred<String>()
        withContext(Dispatchers.Main) {
            pending = deferred
            val wrapped = """
(function(){
  var out = [];
  function fmt(a){ try { return typeof a === 'string' ? a : JSON.stringify(a); } catch(e) { return String(a); } }
  var c = {
    log: function(){ out.push(Array.prototype.map.call(arguments, fmt).join(' ')); },
  };
  c.info = c.log; c.warn = c.log; c.error = c.log; c.debug = c.log;
  var finished = false;
  function finish(err){
    if (finished) return; finished = true;
    LFBridge.done(JSON.stringify({ out: out.join('\n'), error: err ? String(err && err.stack ? err.stack : err) : null }));
  }
  try {
    var AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    var fn = new AsyncFunction('console', ${JSONObject.quote(code)});
    fn(c).then(function(){ setTimeout(function(){ finish(null); }, 50); }, finish);
  } catch (e) { finish(e); }
})();
""".trimIndent()
            view(ctx).evaluateJavascript(wrapped, null)
        }
        val json = withTimeoutOrNull(timeoutMs) { deferred.await() }
        if (json == null) {
            // Stuck (e.g. an infinite loop): throw this WebView away so the next run starts fresh.
            withContext(Dispatchers.Main) {
                webView?.destroy()
                webView = null
            }
            return RunResult("javascript", false, true, "[Stopped: ran longer than ${timeoutMs / 1000} seconds]")
        }
        val o = JSONObject(json)
        val out = o.optString("out")
        val err = if (o.isNull("error")) null else o.optString("error")
        val text = listOfNotNull(out.ifBlank { null }, err).joinToString("\n").ifBlank { "(no output)" }
        return RunResult("javascript", err == null, false, text)
    }
}
