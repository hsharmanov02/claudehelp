package com.localforge.app.agent

/** Browser tags the model can write: <search>, <browse>, <fetch>, <page>, <extract>, <scroll>, <click>, <type>, <press>, <js>. */
object BrowserTools {
    val KINDS = setOf("search", "browse", "page", "scroll", "click", "type", "press", "js", "fetch", "extract", "network", "mod")
    /** These only read (or open) pages, so they run without asking. */
    val READ_KINDS = setOf("search", "browse", "page", "scroll", "fetch", "extract", "network")
    /** Steps that need a page to be open already. */
    val PAGE_KINDS = setOf("page", "scroll", "click", "type", "press", "js", "extract", "network")

    private val opt = setOf(RegexOption.IGNORE_CASE)
    private val bSearch = Regex("""<search\s+(?:query|q)\s*=\s*"([^"]+)"\s*/?>""", opt)
    private val bBrowse = Regex("""<(?:browse|goto|navigate)\s+(?:url|href)\s*=\s*"([^"]+)"\s*/?>""", opt)
    private val bOpenUrl = Regex("""<open\s+(?:url|href|path)\s*=\s*"(https?://[^"]+|[^"]+\.html?)"\s*/?>""", opt)
    private val bFetch = Regex("""<fetch\s+(?:url|href)\s*=\s*"([^"]+)"\s*/?>""", opt)
    private val bPage = Regex("""<page\s*/?>""", opt)
    private val bNetwork = Regex("""<(?:network|requests|api)(?:\s+(all|detail|body|ws|websockets?|save|har)\s*=\s*"([^"]*)")?\s*/?>""", opt)
    private val bExtract = Regex("""<extract(?:\s+(?:query|q|what|about)\s*=\s*"([^"]*)")?\s*/?>""", opt)
    private val bScroll = Regex("""<scroll(?:\s+(?:dir|direction)\s*=\s*"([^"]*)")?\s*/?>""", opt)
    private val bClick = Regex("""<click\s+(n|id|element|row)\s*=\s*"?(\d+)"?\s*/?>""", opt)
    private val bPress = Regex("""<press\s+key\s*=\s*"([^"]+)"\s*/?>""", opt)
    private val bType = Regex("""<type\s+(?:n|id|element)\s*=\s*"?(\d+)"?(\s+submit\s*=\s*"?(?:true|yes|1)"?)?\s*>([\s\S]*?)</type>""", opt)
    private val bJs = Regex("""<js>\r?\n?([\s\S]*?)</js>""", opt)
    private val bMod = Regex("""<mod(?:\s+site\s*=\s*"([^"]*)")?(?:\s+note\s*=\s*"([^"]*)")?\s*>\r?\n?([\s\S]*?)</mod>""", opt)
    private val fence = Regex("```[\\w+#.-]*[ \\t]*\\r?\\n([\\s\\S]*?)```")
    /** Small tag typos like <search query="..."] or <browse url="..."> */
    private val sloppy = Regex("""(<(?:search|browse|goto|navigate|fetch|extract|click|press|scroll)\b[^<>\n]*?")\s*[\]>)]""", opt)

    val anyTag = Regex("""<(search|browse|fetch|extract|click|type|press|scroll|page|js|run|write|read|list|find)\b""", opt)

    fun prompt(): String = """
YOU HAVE YOUR OWN WEB BROWSER (the user watches it live in the Browser tab and stays logged in to sites there).
<search query="WORDS"/>        search the web; you get titles, links and snippets
<browse url="FULL URL"/>       open a page; you get its text and numbered clickable/fillable elements
<fetch url="FULL URL"/>        get raw data (JSON, CSV, text, APIs)
<page/>                        read the current page again
<extract query="WHAT YOU NEED"/>   the page's tables and lists (results, products, prices, stories) as rows with links: USE THIS FIRST for data
<click n="12"/>                click element [12]
<click row="3"/>               open row 3 of the last <extract> results
<type n="5">text</type>        fill input [5]; add submit="true" to press Enter after
<press key="Enter"/>           press a key (Enter, Tab, Escape, PageDown, ArrowDown)
<scroll dir="down"/>           scroll down (or up) and read what is visible
<network/>                     the page's own backend API calls (the JSON data the site loaded), numbered
<network detail="3"/>          call 3 in full: headers, data sent, status, timing, TLS · <network body="3"/> its whole response
<network all=""/>              every request (all="js", all="img", all="api" or a site name) · <network ws=""/> WebSockets · <network save=""/> .har file
<js>document.title</js>        run JavaScript IN the live page, logged in as the user; the last expression is the result.
<mod site="example.com" note="what it does">JS</mod>   save a change to a site: it is applied every time that site loads
YOU HAVE REAL ACCESS TO THE OPEN WEBSITE through <js>:
- Read or change anything: the DOM, text, styles, forms, localStorage, cookies the page can see.
  e.g. <js>document.querySelector("h1").textContent = "New title"; "done"</js>
- Talk to the site's backend server with the user's login (cookies are sent):
  <js>await (await fetch("/api/items", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({name: "x"})})).text()</js>
  Find the real API addresses first with <network/>. Report the server's actual response.
- To keep an edit after reloads, use <mod>. Test it with <js> first.
Browser rules:
- For news, prices, deals, shops, weather or anything current, start with <search>, then <browse> one of the result links.
- Never make up or guess a web address. Only <browse> addresses the user gave you or that appeared in a result.
- If a site blocks you or shows an error, don't retry it: open another result or answer from the snippets.
- After a browser tag, stop your reply and wait for the result. Use element numbers from the latest page only.
- If the user asks to open, click or go to something, do that FIRST and answer from the new page.
- If the page is already given to you under [Page data], answer from it. Only use a tag if you need more.
- For data a site loads from its server (prices, feeds, dashboards), <network/> often has it as clean JSON.
- If the user is just chatting, answer normally without browser tags.
- Copy numbers, prices and links exactly from the page results. Never invent them.
- Never accept cookies or sign up for anything unless the user asks.
""".trim()

    fun parse(rawReply: String): List<Pair<Int, FileAction.Browser>> {
        val reply = rawReply.replace(sloppy, "$1/>")
        val found = mutableListOf<Pair<Int, FileAction.Browser>>()
        fun add(m: MatchResult, kind: String, path: String, content: String = "", extra: String = "") {
            found += m.range.first to FileAction.Browser(kind, path, content, extra)
        }
        bSearch.findAll(reply).forEach { add(it, "search", it.groupValues[1].trim()) }
        bBrowse.findAll(reply).forEach { add(it, "browse", it.groupValues[1].trim()) }
        bOpenUrl.findAll(reply).forEach { add(it, "browse", it.groupValues[1].trim()) }
        bFetch.findAll(reply).forEach { add(it, "fetch", it.groupValues[1].trim()) }
        bPage.findAll(reply).forEach { add(it, "page", "") }
        bNetwork.findAll(reply).forEach { m ->
            val mode = when (val k = m.groupValues[1].lowercase()) { "websocket", "websockets" -> "ws"; "har" -> "save"; else -> k }
            add(m, "network", mode, m.groupValues[2].trim(), m.groupValues[2].trim())
        }
        bExtract.findAll(reply).forEach { add(it, "extract", it.groupValues[1].trim()) }
        bScroll.findAll(reply).forEach { add(it, "scroll", it.groupValues[1].ifBlank { "down" }.trim().lowercase()) }
        bClick.findAll(reply).forEach { add(it, "click", it.groupValues[2], extra = if (it.groupValues[1].lowercase() == "row") "row" else "") }
        bPress.findAll(reply).forEach { add(it, "press", it.groupValues[1].trim()) }
        bType.findAll(reply).forEach { add(it, "type", it.groupValues[1], it.groupValues[3], if (it.groupValues[2].isNotBlank()) "submit" else "") }
        bMod.findAll(reply).forEach { m ->
            var code = m.groupValues[3].trim()
            fence.find(code)?.let { code = it.groupValues[1].trim() }
            if (code.isNotEmpty()) add(m, "mod", m.groupValues[1].trim().ifEmpty { WebBrowser.siteOf(WebBrowser.url) }, code, m.groupValues[2].trim())
        }
        bJs.findAll(reply).forEach { m ->
            var code = m.groupValues[1].trim()
            fence.find(code)?.let { code = it.groupValues[1].trim() }
            if (code.isNotEmpty()) add(m, "js", "page", code)
        }
        return found
    }

    /** Friendly chip text for a tag in a reply, instead of showing raw <tags>. */
    fun chip(kind: String, path: String, extra: String = ""): String = when (kind) {
        "search" -> "🔎 Search \"$path\""
        "browse" -> "🌐 Open ${path.removePrefix("https://").removePrefix("http://").take(60)}"
        "fetch" -> "⬇ Fetch ${path.removePrefix("https://").removePrefix("http://").take(60)}"
        "page" -> "📖 Read the page"
        "extract" -> "📊 Extract data" + if (path.isNotBlank()) " (\"$path\")" else ""
        "scroll" -> "↕ Scroll $path"
        "click" -> if (extra == "row") "🖱 Open row $path" else "🖱 Click [$path]"
        "type" -> "⌨ Type into [$path]"
        "press" -> "⌨ Press $path"
        "js" -> "🧩 Run a page script"
        "network" -> when (path) {
            "detail" -> "📡 Inspect request #$extra"
            "body" -> "📡 Read response #$extra"
            "all" -> "📡 List every request"
            "ws" -> "📡 Read WebSockets"
            "save" -> "💾 Save network log (.har)"
            else -> "📡 Read the page's API data"
        }
        "mod" -> "✎ Save an edit to $path"
        else -> kind
    }

    private val blockSigns = Regex(
        "access denied|you don't have permission to access|request blocked|403 forbidden|\\b403\\b.{0,40}forbidden|" +
            "are you a (?:robot|human)|verify you are (?:a )?human|unusual traffic|captcha|bot detection|" +
            "something went wrong|pardon our interruption|press (?:&|and) hold|checking your browser|attention required|" +
            "enable javascript and cookies|this site can.t be reached|err_name_not_resolved|err_connection|err_unknown_url_scheme|" +
            "dns_probe|webpage not available|page (?:not|cannot be) found|\\b404\\b.{0,40}not found|server not found|err_file_not_found",
        RegexOption.IGNORE_CASE
    )
    private val siteBlock = Regex(
        "access denied|permission to access|request blocked|forbidden|robot|human|unusual traffic|captcha|bot detection|" +
            "something went wrong|pardon our interruption|press (?:&|and) hold|checking your browser|attention required",
        RegexOption.IGNORE_CASE
    )

    /** Spots block, CAPTCHA and error pages so the model goes elsewhere instead of retrying the same site. */
    fun blockedHint(result: String): String {
        val text = Regex("Page text:\\n([\\s\\S]*?)\\n\\nClickable").find(result)?.groupValues?.get(1) ?: result.take(3000)
        if (text.length > 2500 || !blockSigns.containsMatchIn(text.take(2500))) return ""
        return if (siteBlock.containsMatchIn(text.take(2500)))
            "\n\n[LocalForge note] This site blocked the browser. This page is not real content. Do NOT open this site again " +
                "and do not guess other addresses on it. Open a different search result, or answer from the search snippets you already have."
        else "\n\n[LocalForge note] This page doesn't exist or couldn't load (an error page, not real content). " +
            "Don't retry the same address. Use an address from search results, or <search> again."
    }

    fun siteOf(url: String): String =
        Regex("^(?:https?://)?(?:www\\.)?([^/:?#]+)", RegexOption.IGNORE_CASE).find(url.trim())?.groupValues?.get(1)?.lowercase() ?: ""

    /** Runs one browser action and returns what the model should see. */
    suspend fun execute(a: FileAction.Browser): String = try {
        val b = WebBrowser
        when (a.kind) {
            "search" -> b.search(a.path)
            "browse" -> { b.navigate(a.path); val r = b.describe(); r + blockedHint(r) }
            "extract" -> "Data on the page (tables and repeated lists; ▲/▼ = up/down shown by colour or icon). " +
                "To open a row's main link use <click row=\"N\"/> (N = the row's number here, NOT a page element number). " +
                "For another link in a row (e.g. its comments), use <browse url=\"that link\"/>:\n" + b.extract(a.path)
            "page" -> { val r = b.describe(); r + blockedHint(r) }
            "network" -> when (a.path) {
                "detail" -> NetInspect.detail(a.content.filter { it.isDigit() }.toIntOrNull() ?: -1)
                "body" -> NetInspect.body(a.content.filter { it.isDigit() }.toIntOrNull() ?: -1)
                "all" -> NetInspect.all(a.content)
                "ws" -> NetInspect.sockets()
                "save" -> NetInspect.saveHar()
                else -> b.network()
            }
            "fetch" -> { val r = b.fetchRaw(a.path); r + blockedHint(r) }
            "scroll" -> "Scrolled. Visible part of the page:\n" + b.scroll(a.path.startsWith("up"))
            "click" -> {
                val n = a.path.toInt()
                val link = b.extractLink(n, a.extra == "row")
                if (link != null) {      // the model meant row N of the last <extract>, not page element N
                    b.navigate(link)
                    val r = "Opened row $n of the extracted data ($link). Now:\n" + b.describe(1800)
                    r + blockedHint(r)
                } else {
                    b.click(n)
                    val r = "Clicked. Now:\n" + b.describe(1800)
                    r + blockedHint(r)
                }
            }
            "type" -> {
                b.fill(a.path.toInt(), a.content, a.extra == "submit")
                (if (a.extra == "submit") "Typed and submitted. Now:\n" else "Typed. Now:\n") + b.describe(1800)
            }
            "press" -> { b.key(a.path); "Pressed ${a.path}. Now:\n" + b.describe(1800) }
            "js" -> "Script result:\n" + b.runJs(a.content)
            "mod" -> {
                b.saveMod(a.path, a.content, a.extra.ifBlank { "Edit by the AI" })
                val now = if (b.siteOf(b.url).let { it == a.path || it.endsWith(".${a.path}") }) {
                    "\nApplied now: " + b.runJs("(async () => {\n${a.content}\n})()").take(300)
                } else ""
                "Saved the edit for ${a.path}. It will be applied every time that site loads.$now"
            }
            else -> "Unknown browser action"
        }
    } catch (e: Exception) {
        "Browser error (${a.label}): ${e.message}"
    }
}
