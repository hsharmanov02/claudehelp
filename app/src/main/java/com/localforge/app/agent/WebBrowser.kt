package com.localforge.app.agent

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.content.MutableContextWrapper
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.RenderProcessGoneDetail
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.io.File
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** A saved change to a site, re-applied on every load. */
data class ModEntry(val site: String, val index: Int, val note: String, val js: String)

/** A dropdown or date/time/colour field the user tapped: LocalForge draws the picker itself. */
class PickRequest(val json: JSONObject) {
    val kind: String get() = json.optString("kind")
    val type: String get() = json.optString("type")
    val value: String get() = json.optString("value")
}

/**
 * LocalForge's own web browser on the phone: one WebView shared by the Browser tab and the AI.
 * The AI reads pages, lists their clickable elements by number, extracts tables/lists, clicks, types and scrolls.
 */
object WebBrowser {
    private val main = Handler(Looper.getMainLooper())
    private lateinit var appCtx: Context
    private val ctx by lazy { MutableContextWrapper(appCtx) }
    private var wv: WebView? = null
    private val scripts = mutableMapOf<String, String>()
    private val waiting = ConcurrentHashMap<String, CompletableDeferred<String>>()

    var url by mutableStateOf("")
        private set
    var title by mutableStateOf("")
        private set
    var loading by mutableStateOf(false)
        private set
    var progress by mutableStateOf(0)
        private set
    var canGoBack by mutableStateOf(false)
        private set
    /** The step the AI is doing right now, shown over the page so the user can watch. */
    var step by mutableStateOf<String?>(null)
    var picker by mutableStateOf<PickRequest?>(null)
    var notice by mutableStateOf<String?>(null)
    /** Bumped when the WebView is replaced (e.g. after its renderer crashed) so the screen re-attaches it. */
    var generation by mutableStateOf(0)
        private set

    private val console = ArrayDeque<Triple<Int, String, String>>()
    private var consoleSeq = 0
    private var extractRows: Map<Int, String> = emptyMap()
    private var extractUrl: String? = null

    fun init(context: Context) {
        if (!::appCtx.isInitialized) appCtx = context.applicationContext
    }

    /** Dialogs from the page (and any native picker) need an Activity window; swap it in while one is open. */
    fun attachActivity(activity: Context?) {
        ctx.baseContext = activity ?: appCtx
    }

    /** The app's own storage, for files the browser saves when it has no all-files access. */
    fun filesDir(): File = appCtx.getExternalFilesDir(null) ?: appCtx.filesDir

    val hasPage: Boolean get() = url.isNotBlank() && url != "about:blank"

    private fun script(name: String): String = scripts.getOrPut(name) {
        appCtx.assets.open("browser/$name.js").bufferedReader().use { it.readText() }
    }

    private class Bridge {
        @JavascriptInterface
        fun result(id: String, json: String) {
            waiting.remove(id)?.complete(json)
        }

        @JavascriptInterface
        fun pick(json: String) {
            val o = try { JSONObject(json) } catch (_: Exception) { return }
            main.post { picker = PickRequest(o) }
        }
    }

    private val bridge = Bridge()

    /** The shared WebView. Main thread only. */
    @SuppressLint("SetJavaScriptEnabled")
    fun view(): WebView = wv ?: WebView(ctx).also { w ->
        wv = w
        // White until the page paints, instead of a black box.
        w.setBackgroundColor(android.graphics.Color.WHITE)
        val s = w.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.databaseEnabled = true
        s.loadWithOverviewMode = true
        s.useWideViewPort = true
        s.builtInZoomControls = true
        s.displayZoomControls = false
        s.allowFileAccess = true            // the AI's own HTML pages open from phone storage
        s.mediaPlaybackRequiresUserGesture = true
        s.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        s.setSupportMultipleWindows(false)  // "open in new tab" stays in this view
        // Some sites refuse in-app browsers ("; wv"): look like normal Chrome.
        s.userAgentString = s.userAgentString.replace("; wv", "").replace(Regex("Version/\\d+\\.\\d+ "), "")
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(w, true)
        w.addJavascriptInterface(bridge, "LFB")
        if (WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
            WebViewCompat.addDocumentStartJavaScript(w, script("init"), setOf("*"))
        }
        w.webViewClient = Client()
        w.webChromeClient = Chrome()
        w.setDownloadListener { dlUrl, ua, disposition, mime, _ ->
            try {
                val name = URLUtil.guessFileName(dlUrl, disposition, mime)
                val req = DownloadManager.Request(Uri.parse(dlUrl))
                    .addRequestHeader("User-Agent", ua)
                    .addRequestHeader("Cookie", CookieManager.getInstance().getCookie(dlUrl) ?: "")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
                (appCtx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
                notice = "Downloading $name to Download"
            } catch (e: Exception) {
                notice = "Couldn't download: ${e.message}"
            }
        }
    }

    /** Every request the browser makes (pages, scripts, images, API calls), seen from the app side. */
    data class SeenRequest(val time: Long, val method: String, val url: String, val headers: Map<String, String>, val mainFrame: Boolean, val page: String)
    private val seen = ArrayDeque<SeenRequest>()

    fun seenRequests(): List<SeenRequest> = synchronized(seen) { seen.toList() }

    private class Client : WebViewClient() {
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): android.webkit.WebResourceResponse? {
            // Only watches: returning null lets the WebView load everything itself, unchanged.
            try {
                val r = SeenRequest(System.currentTimeMillis(), request.method ?: "GET", request.url.toString().take(2000),
                    request.requestHeaders ?: emptyMap(), request.isForMainFrame, url)
                synchronized(seen) { seen.addLast(r); while (seen.size > 600) seen.removeFirst() }
            } catch (_: Exception) {}
            return null
        }

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val u = request.url
            val scheme = (u.scheme ?: "").lowercase()
            if (scheme in setOf("http", "https", "file", "about", "data", "blob", "javascript")) return false
            // App links (intent://, market://, whatsapp://…) used to end on an error page. Follow the web
            // fallback if there is one; only leave the app when the user tapped the link themselves.
            if (scheme == "intent") {
                try {
                    val i = Intent.parseUri(u.toString(), Intent.URI_INTENT_SCHEME)
                    val fallback = i.getStringExtra("browser_fallback_url")
                    if (!fallback.isNullOrBlank()) { view.loadUrl(fallback); return true }
                    if (request.hasGesture()) {
                        i.addCategory(Intent.CATEGORY_BROWSABLE); i.component = null; i.selector = null
                        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        appCtx.startActivity(i)
                    }
                } catch (_: Exception) {}
                return true
            }
            if (request.hasGesture()) {
                try {
                    appCtx.startActivity(Intent(Intent.ACTION_VIEW, u).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (_: Exception) {
                    notice = "No app on this phone opens $scheme: links."
                }
            }
            return true
        }

        override fun onPageStarted(view: WebView, u: String?, favicon: android.graphics.Bitmap?) {
            loading = true
            url = u ?: ""
            synchronized(console) { console.clear() }
        }

        override fun onPageFinished(view: WebView, u: String?) {
            loading = false
            url = view.url ?: u ?: ""
            title = view.title ?: ""
            canGoBack = view.canGoBack()
            if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) {
                view.evaluateJavascript(script("init"), null)
            }
            applyMods(view, url)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: android.webkit.WebResourceError) {
            if (request.isForMainFrame) {
                loading = false
                notice = "Couldn't load the page: ${error.description}"
            }
        }

        override fun doUpdateVisitedHistory(view: WebView, u: String?, isReload: Boolean) {
            url = view.url ?: u ?: url
            canGoBack = view.canGoBack()
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            // A heavy page crashed the browser's renderer: start a fresh browser instead of closing the app.
            (view.parent as? android.view.ViewGroup)?.removeView(view)
            view.destroy()
            if (wv === view) wv = null
            waiting.values.forEach { it.complete("{\"e\":\"The page crashed the browser and was closed.\"}") }
            waiting.clear()
            loading = false
            notice = "That page crashed the browser, so it was restarted."
            generation++
            return true
        }
    }

    private class Chrome : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) { progress = newProgress }
        override fun onReceivedTitle(view: WebView, t: String?) { title = t ?: "" }
        override fun onConsoleMessage(m: ConsoleMessage): Boolean {
            val level = when (m.messageLevel()) {
                ConsoleMessage.MessageLevel.ERROR -> "error"
                ConsoleMessage.MessageLevel.WARNING -> "warning"
                else -> "log"
            }
            val where = if (level == "error" && m.lineNumber() > 0) " (line ${m.lineNumber()})" else ""
            log(level, m.message() + where)
            return true
        }
        // Page dialogs would block the AI: accept them and note the text.
        override fun onJsAlert(view: WebView, u: String?, message: String?, result: JsResult): Boolean {
            log("log", "[alert] $message"); result.confirm(); return true
        }
        override fun onJsConfirm(view: WebView, u: String?, message: String?, result: JsResult): Boolean {
            log("log", "[confirm] $message"); result.confirm(); return true
        }
        override fun onJsPrompt(view: WebView, u: String?, message: String?, def: String?, result: JsPromptResult): Boolean {
            log("log", "[prompt] $message"); result.confirm(def ?: ""); return true
        }
    }

    private fun log(level: String, text: String) = synchronized(console) {
        consoleSeq++
        console.addLast(Triple(consoleSeq, level, text.take(1000)))
        while (console.size > 300) console.removeFirst()
    }

    val consoleMark: Int get() = synchronized(console) { consoleSeq }

    fun consoleText(since: Int = 0, onlyProblems: Boolean = false, limit: Int = 60): String = synchronized(console) {
        console.filter { it.first > since && (!onlyProblems || it.second == "error" || it.second == "warning") }
            .takeLast(limit).joinToString("\n") { "[${it.second}] ${it.third}" }
    }

    /** A WebView that isn't on screen has no size, so pages would look empty to the AI. Give it the screen's. */
    private fun ensureLaidOut(w: WebView) {
        if (w.width > 0 && w.height > 0) return
        val dm = appCtx.resources.displayMetrics
        val h = (dm.heightPixels * 0.75).toInt()
        w.measure(View.MeasureSpec.makeMeasureSpec(dm.widthPixels, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY))
        w.layout(0, 0, dm.widthPixels, h)
    }

    // ---------- JavaScript ----------

    /** Runs a JS expression (may return a promise) and gives back its value: String, Number, JSONObject, JSONArray or null. */
    suspend fun eval(expr: String, timeoutMs: Long = 30_000): Any? {
        val id = UUID.randomUUID().toString()
        val d = CompletableDeferred<String>()
        waiting[id] = d
        val js = "(async () => { try { const v = await ($expr);" +
            " let j; try { j = JSON.stringify({ v: v === undefined ? null : v }); } catch (e) { j = JSON.stringify({ v: String(v) }); }" +
            " LFB.result('$id', j); } catch (e) { LFB.result('$id', JSON.stringify({ e: String(e && e.message || e) })); } })(); 0"
        withContext(Dispatchers.Main) { ensureLaidOut(view()); view().evaluateJavascript(js, null) }
        val raw = withTimeoutOrNull(timeoutMs) { d.await() }
        waiting.remove(id)
        raw ?: throw IllegalStateException("The page didn't answer in time.")
        val o = JSONObject(raw)
        if (o.has("e")) throw IllegalStateException(o.getString("e"))
        return if (o.isNull("v")) null else o.get("v")
    }

    private suspend fun evalString(expr: String, timeoutMs: Long = 30_000): String =
        eval(expr, timeoutMs)?.toString() ?: ""

    /** Plain evaluateJavascript, for when the page's own scripts may be broken (no bridge needed). */
    private suspend fun evalRaw(js: String): Any? {
        val d = CompletableDeferred<String?>()
        withContext(Dispatchers.Main) { view().evaluateJavascript(js) { d.complete(it) } }
        val raw = withTimeoutOrNull(5_000) { d.await() } ?: return null
        return try { JSONTokener(raw).nextValue() } catch (_: Exception) { null }
    }

    // ---------- navigation ----------

    private suspend fun waitLoaded(timeoutMs: Long = 20_000) {
        delay(300)
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            if (!loading && evalRaw("document.readyState") == "complete") break
            delay(250)
        }
        delay(400)
    }

    fun normalizeUrl(input: String): String {
        val u = input.trim().trim('"')
        if (u.isEmpty()) return u
        if (u.startsWith("/") && File(u).exists()) return Uri.fromFile(File(u)).toString()
        if (Regex("^(https?|about|file|data):", RegexOption.IGNORE_CASE).containsMatchIn(u)) return u
        if (' ' in u || ('.' !in u && !u.startsWith("localhost"))) return "https://www.bing.com/search?q=" + URLEncoder.encode(u, "UTF-8")
        val host = u.substringBefore('/').substringBefore(':')
        val local = host == "localhost" || Regex("^\\d+\\.\\d+\\.\\d+\\.\\d+$").matches(host)
        return (if (local) "http://" else "https://") + u
    }

    suspend fun navigate(input: String) {
        val u = normalizeUrl(input)
        if (u.isEmpty()) return
        withContext(Dispatchers.Main) {
            loading = true
            url = u
            ensureLaidOut(view())
            view().loadUrl(u)
        }
        waitLoaded()
        dismissCookies()
        refreshTitle()
    }

    /** The user typed in the address bar. */
    fun go(input: String) {
        val u = normalizeUrl(input)
        if (u.isNotEmpty()) view().loadUrl(u)
    }

    fun back(): Boolean {
        val w = wv ?: return false
        if (!w.canGoBack()) return false
        w.goBack(); return true
    }

    fun forward() { wv?.takeIf { it.canGoForward() }?.goForward() }
    fun reload() { wv?.reload() }

    private suspend fun refreshTitle() {
        withContext(Dispatchers.Main) {
            wv?.let { url = it.url ?: url; title = it.title ?: title; canGoBack = it.canGoBack() }
        }
    }

    /** Closes cookie banners with the privacy-friendly choice (reject / necessary only), never accept. */
    private suspend fun dismissCookies() {
        try {
            var hit = evalString(script("cookies"), 5_000)
            if (hit.isEmpty()) { delay(1000); hit = evalString(script("cookies"), 5_000) }
            if (hit.isNotEmpty()) delay(600)
        } catch (_: Exception) {}
    }

    // ---------- reading pages ----------

    suspend fun describe(maxText: Int = 2200, withConsole: Boolean = true): String {
        val d = JSONObject(evalString(script("snapshot")))
        url = d.optString("url"); title = d.optString("title")
        var text = d.optString("text")
        if (text.length > maxText) text = text.take(maxText) + "\n…[page text cut — scroll or use <extract/> for more]"
        val els = d.optJSONArray("elements") ?: JSONArray()
        val list = (0 until minOf(els.length(), 40)).joinToString("\n") { els.getString(it).take(90) }
        val cuesArr = d.optJSONArray("cues") ?: JSONArray()
        val cues = (0 until cuesArr.length()).joinToString("\n") { cuesArr.getString(it) }.take(1000)
        val problems = if (withConsole) consoleText(onlyProblems = true, limit = 10) else ""
        val apiCalls = try { (eval("(window.__lfNet || []).filter(x => x.body).length", 3_000) as? Number)?.toInt() ?: 0 } catch (_: Exception) { 0 }
        return "Browser page: ${d.optString("title")}\nURL: ${d.optString("url")}\n\nPage text:\n${text.ifBlank { "(empty)" }}\n\n" +
            (if (cues.isNotBlank()) "Visual cues (meaning shown by colour/style, not in the text):\n$cues\n\n" else "") +
            "Clickable / fillable elements (use their numbers):\n${list.ifBlank { "(none)" }}" +
            (if (problems.isNotBlank()) "\n\nConsole errors/warnings on this page:\n$problems" else "") +
            (if (apiCalls > 0) "\n\nThis page loaded data from $apiCalls backend API call(s). Use <network/> to see them." else "")
    }

    /** Pulls tables and repeated lists (results, products, stories) off the page as rows with links. */
    suspend fun extract(query: String = ""): String {
        val out = evalString("(${script("extract")})(${JSONObject.quote(query)})", 20_000).take(6000)
        extractRows = Regex("^(\\d+)\\. .*? -> (https?://\\S+)", RegexOption.MULTILINE).findAll(out)
            .associate { it.groupValues[1].toInt() to it.groupValues[2] }
        extractUrl = url
        return out.ifBlank { "No tables or repeated lists found on this page. Read it with <page/> or use <js>." }
    }

    /** Row n's link from the last extract on this page: for <click row="n"/>, or <click n="n"/> when element n doesn't exist. */
    suspend fun extractLink(n: Int, row: Boolean): String? {
        if (n !in extractRows || extractUrl != url) {
            if (row) throw IllegalStateException("There's no row $n in the last extracted data for this page. Use <extract/> again.")
            return null
        }
        if (row) return extractRows[n]
        val exists = try { eval("!!document.querySelector('[data-lf-id=\"$n\"]')") == true } catch (_: Exception) { false }
        return if (exists) null else extractRows[n]
    }

    /** The page's own calls to its backend (fetch/XHR) with the start of each response. See [NetInspect] for the rest. */
    suspend fun network(): String = NetInspect.summary()

    // ---------- acting on pages ----------

    /** Scrolls element n into view and outlines it in orange for a moment so the user sees what's about to happen. */
    private suspend fun highlight(n: Int) {
        val ok = eval("""(() => { const el = document.querySelector('[data-lf-id="$n"]');
            if (!el) return false; el.scrollIntoView({block: 'center', inline: 'center'});
            const old = el.style.outline; el.style.outline = '3px solid #ff7a28'; el.style.outlineOffset = '2px';
            setTimeout(() => { el.style.outline = old; el.style.outlineOffset = ''; }, 1200); return true; })()""")
        if (ok != true) throw IllegalStateException("Element [$n] isn't on the page any more. Read the page again.")
        delay(550)
    }

    suspend fun click(n: Int) {
        highlight(n)
        eval("""(() => { const el = document.querySelector('[data-lf-id="$n"]'); if (!el) return false;
            window.__lfAi = true;
            try {
              const r = el.getBoundingClientRect(), o = { bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
              el.dispatchEvent(new PointerEvent('pointerdown', o)); el.dispatchEvent(new MouseEvent('mousedown', o));
              el.focus && el.focus();
              el.dispatchEvent(new PointerEvent('pointerup', o)); el.dispatchEvent(new MouseEvent('mouseup', o));
              el.click();
            } finally { setTimeout(() => { window.__lfAi = false; }, 50); }
            return true; })()""")
        waitLoaded(8_000)
    }

    suspend fun fill(n: Int, text: String, submit: Boolean) {
        highlight(n)
        val q = JSONObject.quote(text)
        val r = evalString("""(() => { const el = document.querySelector('[data-lf-id="$n"]'); if (!el) return 'gone';
            const want = $q;
            if (el.tagName === 'SELECT') {
              const w = want.toLowerCase();
              const o = [...el.options].find(o => o.text.trim().toLowerCase() === w || o.value.toLowerCase() === w)
                     || [...el.options].find(o => o.text.toLowerCase().includes(w));
              if (!o) return 'nooption'; el.value = o.value;
              el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); return 'ok';
            }
            el.focus();
            if (el.isContentEditable) {
              document.execCommand('selectAll'); document.execCommand('insertText', false, want); return 'ok';
            }
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, want);
            el.dispatchEvent(new InputEvent('input', {bubbles: true, data: want, inputType: 'insertText'}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return 'ok'; })()""")
        when (r) {
            "gone" -> throw IllegalStateException("Element [$n] isn't on the page any more. Read the page again.")
            "nooption" -> throw IllegalStateException("No option \"$text\" in [$n]")
        }
        if (submit) key("enter")
    }

    suspend fun key(name: String) {
        val k = name.trim().lowercase().replace(" ", "")
        if (k == "pagedown" || k == "pageup") {
            eval("window.scrollBy(0, ${if (k == "pageup") "-" else ""}innerHeight * 0.8)")
            return
        }
        val keyName = when (k) {
            "enter", "return" -> "Enter"; "tab" -> "Tab"; "escape", "esc" -> "Escape"; "backspace" -> "Backspace"
            "arrowdown", "down" -> "ArrowDown"; "arrowup", "up" -> "ArrowUp"; "arrowleft", "left" -> "ArrowLeft"
            "arrowright", "right" -> "ArrowRight"; "space" -> " "; "home" -> "Home"; "end" -> "End"; "delete" -> "Delete"
            else -> name.trim()
        }
        eval("""(() => { const el = document.activeElement || document.body; const key = ${JSONObject.quote(keyName)};
            const code = {Enter: 13, Tab: 9, Escape: 27, Backspace: 8, ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39}[key] || 0;
            const o = { key, code: key, keyCode: code, which: code, bubbles: true, cancelable: true };
            const go = el.dispatchEvent(new KeyboardEvent('keydown', o));
            el.dispatchEvent(new KeyboardEvent('keypress', o)); el.dispatchEvent(new KeyboardEvent('keyup', o));
            if (go && key === 'Enter' && el.form) { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); }
            if (go && key === 'Tab') { const all = [...document.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')];
              const i = all.indexOf(el); if (all[i + 1]) all[i + 1].focus(); }
            return true; })()""")
        waitLoaded(6_000)
    }

    suspend fun scroll(up: Boolean): String {
        eval("window.scrollBy(0, ${if (up) "-" else ""}innerHeight * 0.8)")
        delay(400)
        return evalString("""(() => { const t = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
            while ((n = w.nextNode())) { const r = n.parentElement && n.parentElement.getBoundingClientRect();
              if (r && r.bottom > 0 && r.top < innerHeight && n.textContent.trim()) t.push(n.textContent.trim()); }
            return t.join(' ').slice(0, 3000); })()""")
    }

    /** Runs the AI's script on the page. The last expression is the result, like the browser console. */
    suspend fun runJs(code: String): String {
        val q = JSONObject.quote(code)
        val v = eval("""(async () => { const code = $q; let v;
            try { v = (0, eval)(code); }
            catch (e) { if (!/Illegal return|await is only valid|return not in function/i.test(String(e))) throw e;
              const AF = Object.getPrototypeOf(async function () {}).constructor; v = new AF(code)(); }
            return await v; })()""")
        return when (v) {
            null -> "(script ran, returned nothing)"
            is String -> v.take(6000)
            is JSONObject -> v.toString(1).take(6000)
            is JSONArray -> v.toString(1).take(6000)
            else -> v.toString()
        }
    }

    /** Opens an HTML file the AI wrote and reports its text, elements and console output. */
    suspend fun openPage(file: File): String {
        val mark = consoleMark
        navigate(Uri.fromFile(file).toString())
        delay(1200)
        val logs = consoleText(since = mark, limit = 40)
        return describe(2500, withConsole = false) + if (logs.isNotBlank()) "\n\nConsole output:\n$logs" else "\n\nConsole: no output, no errors."
    }

    /** Loads a URL (with the user's cookies) and returns its raw text, e.g. JSON or CSV. */
    suspend fun fetchRaw(u: String, maxChars: Int = 5000): String {
        navigate(u)
        val txt = evalString("document.contentType + '\\n' + (document.body ? ((document.querySelector('pre') && document.body.children.length === 1) ? document.querySelector('pre').innerText : document.body.innerText) : '')")
        val ctype = txt.substringBefore('\n')
        val body = txt.substringAfter('\n', "")
        return "Fetched $url ($ctype, ${body.length} chars${if (body.length > maxChars) ", cut" else ""}):\n${body.take(maxChars)}"
    }

    suspend fun search(query: String): String {
        val q = URLEncoder.encode(query, "UTF-8")
        val engines = listOf(
            Triple("Bing", "https://www.bing.com/search?q=$q&setlang=en", """JSON.stringify([...document.querySelectorAll('#b_results > li.b_algo')].slice(0, 8).map(r => {
                const a = r.querySelector('h2 a'); const s = r.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_algoSlug, p');
                let href = a ? a.href : '';
                try { const u = new URL(href); const enc = u.searchParams.get('u');
                  if (u.hostname.endsWith('bing.com') && enc && enc.startsWith('a1')) {
                    const b = enc.slice(2).replace(/-/g, '+').replace(/_/g, '/'); href = atob(b + '==='.slice((b.length + 3) % 4)); } } catch (e) {}
                return { title: a ? a.innerText.trim() : '', url: href, snippet: s ? s.innerText.trim() : '' }; }))"""),
            Triple("DuckDuckGo", "https://html.duckduckgo.com/html/?q=$q", """JSON.stringify([...document.querySelectorAll('.result')].slice(0, 8).map(r => {
                const a = r.querySelector('a.result__a'); const s = r.querySelector('.result__snippet');
                let href = a ? a.href : ''; try { const u = new URL(href); if (u.searchParams.get('uddg')) href = u.searchParams.get('uddg'); } catch (e) {}
                return { title: a ? a.innerText.trim() : '', url: href, snippet: s ? s.innerText.trim() : '' }; }))""")
        )
        val generic = """JSON.stringify([...document.querySelectorAll('h2 a[href^="http"], h3 a[href^="http"], a[href^="http"] h3')]
            .map(x => x.closest('a')).filter((a, i, all) => a && all.indexOf(a) === i && !a.href.includes(location.hostname))
            .slice(0, 8).map(a => ({ title: a.innerText.trim(), url: a.href, snippet: '' })))"""
        for ((name, u, js) in engines) {
            val items = try {
                navigate(u)
                var arr = JSONArray(evalString(js).ifBlank { "[]" })
                if ((0 until arr.length()).none { arr.getJSONObject(it).optString("title").isNotBlank() }) arr = JSONArray(evalString(generic).ifBlank { "[]" })
                (0 until arr.length()).map { arr.getJSONObject(it) }.filter { it.optString("title").isNotBlank() && it.optString("url").isNotBlank() }
            } catch (_: Exception) { emptyList() }
            if (items.isNotEmpty()) {
                return "Web search results for \"$query\" ($name):\n" + items.mapIndexed { i, r ->
                    "${i + 1}. ${r.optString("title")}\n   ${r.optString("url")}\n   ${r.optString("snippet").take(200)}"
                }.joinToString("\n")
            }
        }
        return "No search results could be read for \"$query\" (the search page may be asking for a CAPTCHA). Current page:\n" + describe(2000)
    }

    // ---------- the user's pickers ----------

    suspend fun chooseOption(index: Int) {
        val ok = eval("""(() => { const s = document.querySelector('select[data-lf-pick]'); if (!s) return false;
            s.focus(); s.selectedIndex = $index;
            s.dispatchEvent(new Event('input', {bubbles: true})); s.dispatchEvent(new Event('change', {bubbles: true})); return true; })()""")
        if (ok != true) notice = "That dropdown isn't on the page any more."
    }

    suspend fun setPickedValue(value: String) {
        val ok = eval("""(() => { const el = document.querySelector('input[data-lf-pick]'); if (!el) return false;
            const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            el.focus(); set.call(el, ${JSONObject.quote(value)});
            el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true})); return true; })()""")
        if (ok != true) notice = "That field isn't on the page any more."
    }

    // ---------- saved site edits ----------
    // Changes the AI made to a site that the user wanted to keep: re-applied every time that site loads.

    private val modPrefs by lazy { appCtx.getSharedPreferences("sitemods", Context.MODE_PRIVATE) }
    /** Bumped when edits change, so the screen's count updates. */
    var modsVersion by mutableStateOf(0)
        private set

    fun siteOf(u: String): String = try { (Uri.parse(u).host ?: "").removePrefix("www.").lowercase() } catch (_: Exception) { "" }

    private fun allMods(): JSONObject = try { JSONObject(modPrefs.getString("mods", "{}") ?: "{}") } catch (_: Exception) { JSONObject() }

    /** Saved edits that apply to this address: (site, index, note, code). */
    fun modsFor(u: String): List<ModEntry> {
        val host = siteOf(u)
        if (host.isEmpty()) return emptyList()
        val all = allMods()
        return all.keys().asSequence().filter { host == it || host.endsWith(".$it") }.flatMap { site ->
            val arr = all.getJSONArray(site)
            (0 until arr.length()).map { i -> ModEntry(site, i, arr.getJSONObject(i).optString("note"), arr.getJSONObject(i).optString("js")) }
        }.toList()
    }

    fun saveMod(site: String, js: String, note: String) {
        val key = siteOf(if (site.contains("://")) site else "https://$site").ifEmpty { site.lowercase() }
        val all = allMods()
        val arr = all.optJSONArray(key) ?: JSONArray()
        arr.put(JSONObject().put("js", js).put("note", note))
        all.put(key, arr)
        modPrefs.edit().putString("mods", all.toString()).apply()
        modsVersion++
    }

    fun removeMod(m: ModEntry) {
        val all = allMods()
        val arr = all.optJSONArray(m.site) ?: return
        val left = JSONArray()
        for (i in 0 until arr.length()) if (i != m.index) left.put(arr.get(i))
        if (left.length() == 0) all.remove(m.site) else all.put(m.site, left)
        modPrefs.edit().putString("mods", all.toString()).apply()
        modsVersion++
    }

    private fun applyMods(view: WebView, u: String) {
        for (m in modsFor(u)) {
            view.evaluateJavascript("(async () => { try {\n${m.js}\n} catch (e) { console.error('LocalForge saved edit failed: ' + e); } })()", null)
        }
    }

    fun saveCookies() {
        try { CookieManager.getInstance().flush() } catch (_: Exception) {}
    }
}
