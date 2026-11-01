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

/**
 * Runs code inside a hidden WebView (Chrome's V8 engine) — no setup needed.
 * JavaScript runs directly; TypeScript, Lua, Ruby, PHP and C/C++ run through bundled engines
 * (the .js files in assets/engines: sucrase, fengari, Opal, Uniter, JSCPP).
 */
object JsRunner {
    /** Languages this runner handles, mapped to their engine file (null = plain JavaScript). */
    val languages = mapOf(
        "javascript" to null, "typescript" to "ts", "lua" to "lua", "ruby" to "ruby",
        "php" to "php", "c" to "cpp", "cpp" to "cpp"
    )

    private var webView: WebView? = null
    private val loadedEngines = mutableSetOf<String>()
    @Volatile private var pending: CompletableDeferred<String>? = null

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
        loadedEngines.clear()
    }

    private suspend fun ensureEngine(ctx: Context, engine: String) {
        if (engine in loadedEngines) return
        val script = withContext(Dispatchers.IO) {
            ctx.assets.open("engines/$engine.js").bufferedReader().use { it.readText() }
        }
        val loaded = CompletableDeferred<Unit>()
        withContext(Dispatchers.Main) {
            view(ctx).evaluateJavascript(script) { loaded.complete(Unit) }
        }
        withTimeoutOrNull(30_000) { loaded.await() }
        loadedEngines += engine
    }

    private fun body(lang: String, quoted: String): String = when (lang) {
        "javascript" -> "(new AsyncFunction('console', $quoted))(c)"
        "typescript" -> "(new AsyncFunction('console', LF_TS($quoted)))(c)"
        "lua" -> "LF_LUA($quoted, w)"
        "ruby" -> "LF_RUBY($quoted, w)"
        "php" -> "LF_PHP($quoted, w)"
        else -> "LF_CPP($quoted, w)" // c and cpp
    }

    suspend fun run(ctx: Context, lang: String, code: String, timeoutMs: Long): RunResult {
        val engine = languages[lang]
        if (engine != null) ensureEngine(ctx, engine)
        val deferred = CompletableDeferred<String>()
        withContext(Dispatchers.Main) {
            pending = deferred
            val wrapped = """
(function(){
  var out = '';
  function w(s){ out += String(s); }
  function fmt(a){ try { return typeof a === 'string' ? a : JSON.stringify(a); } catch(e) { return String(a); } }
  var c = { log: function(){ w(Array.prototype.map.call(arguments, fmt).join(' ') + '\n'); } };
  c.info = c.log; c.warn = c.log; c.error = c.log; c.debug = c.log;
  var finished = false;
  function finish(err){
    if (finished) return; finished = true;
    var msg = null;
    if (err) { msg = (err && err.stack && String(err.stack).indexOf(String(err.message)) >= 0) ? String(err.stack) : String(err && err.message ? err.message : err); }
    LFBridge.done(JSON.stringify({ out: out, error: msg }));
  }
  try {
    var AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    var p = ${body(lang, JSONObject.quote(code))};
    Promise.resolve(p).then(function(){ setTimeout(function(){ finish(null); }, 50); }, finish);
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
                loadedEngines.clear()
            }
            return RunResult(lang, false, true, "[Stopped: ran longer than ${timeoutMs / 1000} seconds]")
        }
        val o = JSONObject(json)
        val out = o.optString("out").trimEnd()
        val err = if (o.isNull("error")) null else o.optString("error")
        val text = listOfNotNull(out.ifBlank { null }, err?.let { "Error: $it" }).joinToString("\n").ifBlank { "(no output)" }
        return RunResult(lang, err == null, false, text)
    }
}
