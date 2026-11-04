package com.localforge.app.agent

import android.net.Uri
import android.os.Environment
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * Deep network inspection for the AI browser: <network/> and its options.
 * The page's fetch/XHR/beacon calls (with headers, bodies, timing) and WebSockets come from browser/init.js,
 * every other request (scripts, images, pages) from the WebView's request log and the page's Resource Timing.
 * Session cookies and auth tokens are shortened in what the model sees; the saved .har file keeps them in full.
 */
object NetInspect {
    private val secretHeader = Regex("^(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token|x-csrf-token|x-xsrf-token)$", RegexOption.IGNORE_CASE)
    private val secretParam = Regex("(token|key|secret|session|password|passwd|auth|sig|signature|jwt)", RegexOption.IGNORE_CASE)

    const val HELP = "More: <network detail=\"N\"/> one call in full (headers, data sent, timing, TLS) · <network body=\"N\"/> its whole response · " +
        "<network all=\"\"/> every request (or all=\"js\", all=\"img\", all=\"example.com\") · <network ws=\"\"/> WebSockets · <network save=\"\"/> save a .har file"

    fun mask(v: String): String = if (v.length <= 10) "•••" else v.take(6) + "…(${v.length} chars, hidden)"

    private fun maskHeaders(o: JSONObject?): List<String> {
        if (o == null) return emptyList()
        return o.keys().asSequence().map { k ->
            val v = o.optString(k)
            "  $k: " + if (secretHeader.matches(k)) mask(v) else v.take(300)
        }.toList()
    }

    private fun maskQuery(url: String): String = try {
        val u = Uri.parse(url)
        val names = if (u.isHierarchical) u.queryParameterNames else emptySet()
        if (names.isEmpty()) url.take(400) else {
            val b = u.buildUpon().clearQuery()
            for (n in names) for (v in u.getQueryParameters(n)) b.appendQueryParameter(n, if (secretParam.containsMatchIn(n) && v.length > 10) mask(v) else v)
            b.build().toString().take(400)
        }
    } catch (_: Exception) { url.take(400) }

    private suspend fun calls(): List<JSONObject> {
        val arr = WebBrowser.eval("window.__lfNet || []", 8_000) as? JSONArray ?: JSONArray()
        return (0 until arr.length()).map { arr.getJSONObject(it) }
    }

    private fun sizeOf(o: JSONObject): String {
        val n = if (o.has("size") && !o.isNull("size")) o.optLong("size", -1) else -1
        return if (n < 0) "" else if (n < 1024) "$n B" else if (n < 1024 * 1024) "${n / 1024} KB" else "%.1f MB".format(n / 1048576.0)
    }

    private fun line(o: JSONObject): String {
        val status = if (o.has("error")) "FAILED (${o.optString("error")})" else o.opt("status")?.toString() ?: "pending"
        val extra = listOf(o.optString("type"), sizeOf(o), if (o.has("time")) "${o.optLong("time")} ms" else "").filter { it.isNotBlank() }
        return "#${o.optInt("id")} ${o.optString("kind")} ${o.optString("method")} ${maskQuery(o.optString("url"))} -> $status" +
            if (extra.isNotEmpty()) " (${extra.joinToString(", ")})" else ""
    }

    /** <network/>: the page's API calls, the ones with the most data first. */
    suspend fun summary(): String {
        val all = calls()
        if (all.isEmpty()) return "This page hasn't made any backend API calls that LocalForge saw. Read it with <page/> or <extract/>, " +
            "or see every request with <network all=\"\"/>.\n$HELP"
        val rows = all.sortedWith(compareByDescending<JSONObject> { it.optString("body").length }.thenByDescending { it.optInt("id") }).take(12)
        val failed = all.count { it.has("error") || it.optInt("status", 200) >= 400 }
        return "Backend API calls this page made (${all.size} in total${if (failed > 0) ", $failed failed" else ""}; most data first):\n" +
            rows.joinToString("\n\n") { o ->
                line(o) + (o.optString("body").takeIf { it.isNotBlank() }?.let { "\n" + it.take(600) } ?: "")
            }.take(5000) + "\n\n$HELP"
    }

    /** <network detail="N"/>: one call in full. */
    suspend fun detail(id: Int): String {
        val o = calls().firstOrNull { it.optInt("id") == id } ?: return "There's no call #$id on this page. List them with <network/>."
        val url = o.optString("url")
        val u = try { Uri.parse(url) } catch (_: Exception) { null }
        val timing = timingFor(url)
        val sb = StringBuilder(line(o)).append("\n")
        sb.append("Page: ${o.optString("page").take(200)}\n")
        if (u != null && u.isHierarchical) {
            sb.append("Server: ${u.scheme}://${u.host}${if (u.port > 0) ":" + u.port else ""}  path ${u.path}\n")
            if (u.queryParameterNames.isNotEmpty()) {
                sb.append("URL parameters:\n")
                for (n in u.queryParameterNames) for (v in u.getQueryParameters(n))
                    sb.append("  $n = ${if (secretParam.containsMatchIn(n) && v.length > 10) mask(v) else v.take(200)}\n")
            }
        }
        val rq = maskHeaders(o.optJSONObject("reqHeaders"))
        val seenH = WebBrowser.seenRequests().lastOrNull { it.url == url }?.headers.orEmpty()
        sb.append("Request headers:\n").append((rq.ifEmpty {
            seenH.map { (k, v) -> "  $k: " + if (secretHeader.matches(k)) mask(v) else v.take(300) }
        }).ifEmpty { listOf("  (none set by the page)") }.joinToString("\n")).append("\n")
        o.optString("reqBody").takeIf { it.isNotBlank() }?.let { sb.append("Data sent (${it.length} chars):\n${it.take(1500)}\n") }
        sb.append("Response: ${o.opt("status") ?: "pending"} ${o.optString("statusText")}\n")
        sb.append("Response headers:\n").append(maskHeaders(o.optJSONObject("resHeaders")).ifEmpty { listOf("  (none readable)") }.joinToString("\n")).append("\n")
        if (timing != null) {
            sb.append("Timing: total ${timing.optInt("duration")} ms (DNS ${timing.optInt("dns")} ms, connect ${timing.optInt("connect")} ms, " +
                "TLS ${timing.optInt("tls")} ms, waiting for server ${timing.optInt("ttfb")} ms, download ${timing.optInt("download")} ms)\n")
            sb.append("Protocol: ${timing.optString("protocol").ifBlank { "unknown" }} · transferred ${timing.optLong("transfer")} bytes" +
                " (body ${timing.optLong("encoded")} bytes compressed, ${timing.optLong("decoded")} bytes unpacked)\n")
        } else if (o.has("time")) sb.append("Time: ${o.optLong("time")} ms\n")
        sb.append("Encrypted: ${if (url.startsWith("https:") || url.startsWith("wss:")) "yes (HTTPS)" else "NO (plain HTTP: anyone on the network can read it)"}\n")
        val body = o.optString("body")
        sb.append(if (body.isNotBlank()) "Response (first 800 chars; whole thing with <network body=\"$id\"/>):\n${body.take(800)}" else "Response body: (not text, or not readable)")
        return sb.toString().take(6000)
    }

    /** <network body="N"/>: the whole response, JSON pretty-printed. */
    suspend fun body(id: Int): String {
        val o = calls().firstOrNull { it.optInt("id") == id } ?: return "There's no call #$id on this page. List them with <network/>."
        val b = o.optString("body")
        if (b.isBlank()) return "Call #$id has no readable text response (status ${o.opt("status")}, type ${o.optString("type").ifBlank { "unknown" }})."
        val pretty = try {
            val t = b.trim()
            when {
                t.startsWith("{") -> JSONObject(t).toString(1)
                t.startsWith("[") -> JSONArray(t).toString(1)
                else -> b
            }
        } catch (_: Exception) { b }
        return "Response of #$id ${o.optString("method")} ${maskQuery(o.optString("url"))} (${b.length} chars${if (pretty.length > 6000) ", cut" else ""}):\n" + pretty.take(6000)
    }

    private suspend fun timings(): List<JSONObject> {
        val arr = WebBrowser.eval("""performance.getEntriesByType('resource').concat(performance.getEntriesByType('navigation')).slice(-400).map(e => ({
            url: e.name, kind: e.initiatorType || e.entryType, duration: Math.round(e.duration),
            dns: Math.round(e.domainLookupEnd - e.domainLookupStart), connect: Math.round(e.connectEnd - e.connectStart),
            tls: e.secureConnectionStart > 0 ? Math.round(e.connectEnd - e.secureConnectionStart) : 0,
            ttfb: Math.round(e.responseStart - e.requestStart), download: Math.round(e.responseEnd - e.responseStart),
            protocol: e.nextHopProtocol || '', transfer: e.transferSize || 0, encoded: e.encodedBodySize || 0, decoded: e.decodedBodySize || 0,
            status: e.responseStatus || 0 }))""", 8_000) as? JSONArray ?: JSONArray()
        return (0 until arr.length()).map { arr.getJSONObject(it) }
    }

    private suspend fun timingFor(url: String): JSONObject? = try { timings().lastOrNull { it.optString("url") == url } } catch (_: Exception) { null }

    private fun typeOf(kind: String, url: String): String {
        val path = url.substringBefore('?').lowercase()
        return when {
            kind in setOf("fetch", "xmlhttprequest", "beacon") -> "api"
            kind == "navigation" -> "doc"
            kind == "script" || path.endsWith(".js") || path.endsWith(".mjs") -> "js"
            kind == "css" || path.endsWith(".css") -> "css"
            kind == "img" || Regex("\\.(png|jpe?g|gif|webp|svg|ico|avif)$").containsMatchIn(path) -> "img"
            Regex("\\.(woff2?|ttf|otf)$").containsMatchIn(path) -> "font"
            kind in setOf("video", "audio") || Regex("\\.(mp4|webm|m3u8|mp3|m4a|ts)$").containsMatchIn(path) -> "media"
            kind == "iframe" -> "frame"
            else -> kind.ifBlank { "other" }
        }
    }

    /** <network all="filter"/>: every request the page made, with type, status, size, time and protocol. */
    suspend fun all(filter: String): String {
        val f = filter.trim().lowercase()
        val t = timings()
        val known = t.map { it.optString("url") }.toHashSet()
        val page = WebBrowser.url
        // Requests the WebView saw that the page's timing list doesn't have (e.g. blocked or still loading).
        val extra = WebBrowser.seenRequests().filter { it.page == page && it.url !in known }.takeLast(100)
        data class Row(val type: String, val text: String, val url: String)
        val rows = t.map { e ->
            val type = typeOf(e.optString("kind"), e.optString("url"))
            val st = e.optInt("status").takeIf { it > 0 }?.toString() ?: "-"
            val size = e.optLong("transfer").let { if (it <= 0) "cached/0 B" else if (it < 1024) "$it B" else "${it / 1024} KB" }
            Row(type, "$type $st ${maskQuery(e.optString("url"))} ($size, ${e.optInt("duration")} ms, ${e.optString("protocol").ifBlank { "?" }})", e.optString("url"))
        } + extra.map { r ->
            val type = if (r.mainFrame) "doc" else typeOf("", r.url)
            Row(type, "$type ${r.method} ${maskQuery(r.url)} (seen by the browser, no timing yet)", r.url)
        }
        val picked = rows.filter { f.isEmpty() || it.type == f || it.url.lowercase().contains(f) }
        if (picked.isEmpty()) return "No requests match \"$filter\" on this page (${rows.size} requests in total)."
        val hosts = picked.mapNotNull { try { Uri.parse(it.url).host } catch (_: Exception) { null } }.groupingBy { it }.eachCount()
            .entries.sortedByDescending { it.value }.take(12).joinToString(", ") { "${it.key} (${it.value})" }
        val counts = picked.groupingBy { it.type }.eachCount().entries.joinToString(", ") { "${it.value} ${it.key}" }
        val plain = picked.count { it.url.startsWith("http:") }
        return "Requests on this page${if (f.isNotEmpty()) " matching \"$filter\"" else ""}: ${picked.size} ($counts)\n" +
            "Servers: $hosts\n" + (if (plain > 0) "⚠ $plain request(s) over plain HTTP (not encrypted)\n" else "") +
            "type status url (size, time, protocol):\n" + picked.takeLast(60).joinToString("\n") { it.text }.take(5500) +
            (if (picked.size > 60) "\n…${picked.size - 60} earlier ones not shown: narrow it down, e.g. <network all=\"api\"/>" else "")
    }

    /** <network ws=""/>: WebSocket connections and their latest messages. */
    suspend fun sockets(): String {
        val arr = WebBrowser.eval("window.__lfWs || []", 8_000) as? JSONArray ?: JSONArray()
        if (arr.length() == 0) return "This page has no WebSocket connections."
        return (0 until arr.length()).joinToString("\n\n") { i ->
            val o = arr.getJSONObject(i)
            val msgs = o.optJSONArray("msgs") ?: JSONArray()
            "WebSocket ${o.optInt("id")}: ${maskQuery(o.optString("url"))} [${o.optString("state")}] sent ${o.optInt("sent")}, received ${o.optInt("received")}\n" +
                (maxOf(0, msgs.length() - 12) until msgs.length()).joinToString("\n") { j ->
                    val m = msgs.getJSONObject(j)
                    "  ${if (m.optString("dir") == "in") "←" else "→"} ${m.optString("data").take(300)}"
                }
        }.take(6000)
    }

    private fun iso(t: Long): String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date(t))

    private fun nv(o: JSONObject?): JSONArray = JSONArray().apply {
        o?.keys()?.forEach { k -> put(JSONObject().put("name", k).put("value", o.optString(k))) }
    }

    /** <network save=""/>: everything captured on this page as a .har file (opens in Chrome DevTools, Fiddler, Charles…). */
    suspend fun saveHar(): String {
        val c = calls()
        val t = timings()
        val byUrl = c.map { it.optString("url") }.toHashSet()
        val entries = JSONArray()
        for (o in c) {
            val url = o.optString("url")
            val tm = t.lastOrNull { it.optString("url") == url }
            val u = try { Uri.parse(url) } catch (_: Exception) { null }
            val query = JSONArray().apply { if (u != null && u.isHierarchical) u.queryParameterNames.forEach { n -> u.getQueryParameters(n).forEach { v -> put(JSONObject().put("name", n).put("value", v)) } } }
            val req = JSONObject().put("method", o.optString("method")).put("url", url).put("httpVersion", tm?.optString("protocol") ?: "")
                .put("headers", nv(o.optJSONObject("reqHeaders"))).put("queryString", query).put("cookies", JSONArray())
                .put("headersSize", -1).put("bodySize", o.optString("reqBody").length)
            if (o.optString("reqBody").isNotEmpty()) req.put("postData", JSONObject().put("mimeType", o.optJSONObject("reqHeaders")?.optString("content-type") ?: "").put("text", o.optString("reqBody")))
            val res = JSONObject().put("status", o.optInt("status")).put("statusText", o.optString("statusText")).put("httpVersion", tm?.optString("protocol") ?: "")
                .put("headers", nv(o.optJSONObject("resHeaders"))).put("cookies", JSONArray())
                .put("content", JSONObject().put("size", o.optString("body").length).put("mimeType", o.optString("type")).put("text", o.optString("body")))
                .put("redirectURL", "").put("headersSize", -1).put("bodySize", tm?.optLong("encoded") ?: -1)
            val total = tm?.optInt("duration") ?: o.optInt("time")
            entries.put(JSONObject().put("startedDateTime", iso(o.optLong("started"))).put("time", total).put("request", req).put("response", res)
                .put("cache", JSONObject()).put("_resourceType", o.optString("kind"))
                .put("timings", JSONObject().put("dns", tm?.optInt("dns") ?: -1).put("connect", tm?.optInt("connect") ?: -1).put("ssl", tm?.optInt("tls") ?: -1)
                    .put("send", 0).put("wait", tm?.optInt("ttfb") ?: total).put("receive", tm?.optInt("download") ?: 0)))
        }
        val now = System.currentTimeMillis()
        for (e in t) {
            if (e.optString("url") in byUrl) continue
            entries.put(JSONObject().put("startedDateTime", iso(now)).put("time", e.optInt("duration"))
                .put("request", JSONObject().put("method", "GET").put("url", e.optString("url")).put("httpVersion", e.optString("protocol"))
                    .put("headers", JSONArray()).put("queryString", JSONArray()).put("cookies", JSONArray()).put("headersSize", -1).put("bodySize", 0))
                .put("response", JSONObject().put("status", e.optInt("status")).put("statusText", "").put("httpVersion", e.optString("protocol"))
                    .put("headers", JSONArray()).put("cookies", JSONArray()).put("content", JSONObject().put("size", e.optLong("decoded")).put("mimeType", ""))
                    .put("redirectURL", "").put("headersSize", -1).put("bodySize", e.optLong("encoded")))
                .put("cache", JSONObject()).put("_resourceType", e.optString("kind"))
                .put("timings", JSONObject().put("dns", e.optInt("dns")).put("connect", e.optInt("connect")).put("ssl", e.optInt("tls"))
                    .put("send", 0).put("wait", e.optInt("ttfb")).put("receive", e.optInt("download"))))
        }
        val har = JSONObject().put("log", JSONObject().put("version", "1.2")
            .put("creator", JSONObject().put("name", "LocalForge").put("version", "1"))
            .put("pages", JSONArray().put(JSONObject().put("id", "page_1").put("title", WebBrowser.title).put("startedDateTime", iso(now)).put("pageTimings", JSONObject())))
            .put("entries", entries))
        val dir = if (Environment.isExternalStorageManager())
            File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS), "LocalForge/network")
        else File(WebBrowser.filesDir(), "network")
        dir.mkdirs()
        val host = WebBrowser.siteOf(WebBrowser.url).ifBlank { "page" }.replace(Regex("[^a-z0-9.-]"), "_")
        val f = File(dir, "$host-${SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date())}.har")
        f.writeText(har.toString(1))
        return "Saved ${entries.length()} requests (${c.size} with full headers and bodies) to ${f.absolutePath}. " +
            "It opens in Chrome DevTools (Network → import), Fiddler or Charles. It contains the page's cookies and tokens in full: don't share it."
    }
}
