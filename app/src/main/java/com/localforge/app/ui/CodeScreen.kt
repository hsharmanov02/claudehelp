package com.localforge.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.DeleteSweep
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Send
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.ActionStatus
import com.localforge.app.AppViewModel
import com.localforge.app.PendingAction
import com.localforge.app.Role
import com.localforge.app.UiMessage
import com.localforge.app.agent.FileAction
import com.localforge.app.agent.FileTools
import com.localforge.app.agent.BrowserTools

internal sealed class Segment {
    data class Prose(val text: String) : Segment()
    data class Code(val lang: String, val code: String) : Segment()
}

private val fence = Regex("```([\\w+#.-]*)[ \\t]*\\r?\\n([\\s\\S]*?)(```|$)")
private val writeTagDisplay = Regex("""<write\s+path\s*=\s*"([^"]+)"\s*>[\s\S]*?(</write>|$)""", RegexOption.IGNORE_CASE)
private val simpleTagDisplay = Regex("""<(list|read|mkdir|delete)\s+path\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)

private val runTagDisplay = Regex("""<run(?:\s+lang(?:uage)?\s*=\s*"([^"]*)")?\s*>\r?\n?([\s\S]*?)(</run>|$)""", RegexOption.IGNORE_CASE)
private val innerFence = Regex("```[\\w+#.-]*[ \\t]*\\r?\\n?|```")

private val findTagDisplay = Regex("""<find\s+(?:query|name|q)\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)

private val browserTagDisplay = Regex(
    """<(search|browse|goto|navigate|fetch|page|extract|click|press|scroll|network|requests|api)\b([^<>]*?)/?>|<type\s+(?:n|id|element)\s*=\s*"?(\d+)"?[^>]*>[\s\S]*?(</type>|$)|<js>[\s\S]*?(</js>|$)|<mod\b[^>]*>[\s\S]*?(</mod>|$)""",
    RegexOption.IGNORE_CASE
)
private val attr = Regex("""(\w+)\s*=\s*"?([^"]*)"?""")

/** Browser tags shown as friendly step chips ("🌐 Open …", "📊 Extract data", "🖱 Click [3]") instead of raw tags. */
private fun browserChip(m: MatchResult): String {
    val whole = m.value
    if (whole.startsWith("<js", true)) return "\n" + BrowserTools.chip("js", "") + "\n"
    if (whole.startsWith("<mod", true)) return "\n" + BrowserTools.chip("mod", Regex("site\\s*=\\s*\"([^\"]*)\"").find(whole)?.groupValues?.get(1) ?: "this site") + "\n"
    if (m.groupValues[3].isNotEmpty()) return "\n" + BrowserTools.chip("type", m.groupValues[3]) + "\n"
    var kind = m.groupValues[1].lowercase()
    if (kind == "goto" || kind == "navigate") kind = "browse"
    val attrs = attr.findAll(m.groupValues[2]).associate { it.groupValues[1].lowercase() to it.groupValues[2] }
    if (kind == "network" || kind == "requests" || kind == "api") {
        val mode = attrs.keys.firstOrNull() ?: ""
        return "\n" + BrowserTools.chip("network", if (mode.startsWith("websocket")) "ws" else if (mode == "har") "save" else mode, attrs[mode] ?: "") + "\n"
    }
    val path = attrs["url"] ?: attrs["href"] ?: attrs["query"] ?: attrs["q"] ?: attrs["n"] ?: attrs["row"] ?: attrs["key"] ?: attrs["dir"] ?: ""
    return "\n" + BrowserTools.chip(kind, path, if (attrs.containsKey("row")) "row" else "") + "\n"
}

private fun prettify(text: String): String = FileTools.stripThinking(text)
    .replace(browserTagDisplay) { browserChip(it) }
    .replace(findTagDisplay) { "🔎 search phone for \"${it.groupValues[1]}\"" }
    .replace(runTagDisplay) {
        val lang = it.groupValues[1].ifBlank { "python" }
        val code = it.groupValues[2].replace(innerFence, "").trim()
        "\n```$lang\n$code\n```\n"
    }
    .replace(writeTagDisplay) { "✏️ write ${it.groupValues[1]}" }
    .replace(simpleTagDisplay) {
        val icon = when (it.groupValues[1].lowercase()) { "list" -> "📂"; "read" -> "📄"; "mkdir" -> "📁"; else -> "🗑️" }
        "$icon ${it.groupValues[1].lowercase()} ${it.groupValues[2]}"
    }

private fun splitSegments(text: String): List<Segment> {
    val out = mutableListOf<Segment>()
    var last = 0
    for (m in fence.findAll(text)) {
        if (m.range.first > last) out += Segment.Prose(text.substring(last, m.range.first))
        out += Segment.Code(m.groupValues[1], m.groupValues[2].trimEnd())
        last = m.range.last + 1
    }
    if (last < text.length) out += Segment.Prose(text.substring(last))
    return out.filter { it !is Segment.Prose || it.text.isNotBlank() }
}

private fun extensionFor(lang: String): String = when (lang.lowercase()) {
    "python", "py" -> "py"
    "kotlin", "kt" -> "kt"
    "java" -> "java"
    "javascript", "js" -> "js"
    "typescript", "ts" -> "ts"
    "html" -> "html"
    "css" -> "css"
    "json" -> "json"
    "bash", "sh", "shell" -> "sh"
    "c" -> "c"
    "cpp", "c++" -> "cpp"
    "rust", "rs" -> "rs"
    "go" -> "go"
    "markdown", "md" -> "md"
    else -> "txt"
}

@Composable
fun CodeScreen(vm: AppViewModel) {
    val listState = rememberLazyListState()
    var saveTarget by remember { mutableStateOf<Segment.Code?>(null) }
    val lastText = vm.messages.lastOrNull()?.text?.length ?: 0
    LaunchedEffect(vm.messages.size, lastText) {
        if (vm.messages.isNotEmpty()) listState.scrollToItem(vm.messages.size)
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                ScreenTitle("Code")
                Text(
                    vm.loadedModel?.removeSuffix(".gguf") ?: "No model loaded",
                    color = if (vm.loadedModel != null) C.Muted else C.Bad,
                    fontSize = 13.sp, fontFamily = FontFamily.Monospace, maxLines = 1, overflow = TextOverflow.Ellipsis
                )
            }
            IconButton(onClick = { vm.clearChat() }, enabled = !vm.generating) {
                Icon(Icons.Filled.DeleteSweep, "Clear chat", tint = C.Muted)
            }
        }

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            if (vm.messages.isEmpty()) {
                item { Intro(vm) }
            }
            items(vm.messages) { m -> MessageView(vm, m) { saveTarget = it } }
            item { Spacer(Modifier.height(4.dp)) }
        }

        InputBar(vm)
    }

    saveTarget?.let { seg ->
        var path by remember(seg) {
            mutableStateOf(vm.currentDir.absolutePath + "/snippet." + extensionFor(seg.lang))
        }
        AlertDialog(
            onDismissRequest = { saveTarget = null },
            title = { Text("Save code to file") },
            text = {
                Column {
                    Text("Full path (folders are created if needed):", color = C.Muted, fontSize = 13.sp)
                    OutlinedTextField(value = path, onValueChange = { path = it }, singleLine = false, modifier = Modifier.fillMaxWidth())
                    if (!vm.hasFileAccess) Text("Grant all-files access on the Files tab first.", color = C.Bad, fontSize = 13.sp)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    vm.fileMessage = FileTools.execute(FileAction.Write(path.trim(), seg.code))
                    vm.refreshFiles()
                    saveTarget = null
                }) { Text("Save") }
            },
            dismissButton = { TextButton(onClick = { saveTarget = null }) { Text("Cancel") } }
        )
    }
}

@Composable
private fun Intro(vm: AppViewModel) {
    Panel(Modifier.padding(top = 8.dp)) {
        Text("Your offline coding assistant", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
        Spacer(Modifier.height(6.dp))
        Text(
            "Ask for code, fixes or explanations. It can also list, read, create and delete files on your phone — " +
                "changes show up as cards you approve. Attach a file from the Files tab to talk about it.",
            color = C.Muted, fontSize = 14.sp
        )
        Spacer(Modifier.height(10.dp))
        listOf(
            "Write and run a Python script that prints the first 20 prime numbers",
            "List the files in my Download folder",
            "Create a simple HTML to-do app at ${vm.workspaceRoot}/LocalForge/todo.html"
        ).forEach { s ->
            Text(
                s,
                color = C.Accent,
                fontSize = 14.sp,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 3.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(C.CardInner)
                    .clickable { vm.input = s }
                    .padding(10.dp)
            )
        }
    }
}

@Composable
internal fun MessageView(vm: AppViewModel, m: UiMessage, onSave: (Segment.Code) -> Unit = {}) {
    when (m.role) {
        Role.USER -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(
                m.text,
                color = C.Text,
                modifier = Modifier
                    .widthIn(max = 320.dp)
                    .clip(RoundedCornerShape(20.dp, 20.dp, 4.dp, 20.dp))
                    .background(C.AccentDark)
                    .padding(12.dp)
            )
        }
        Role.ASSISTANT -> Column(Modifier.fillMaxWidth()) {
            val display = prettify(m.text)
            if (display.isEmpty()) {
                LiveProgress(vm, m)
            }
            splitSegments(display).forEach { seg ->
                when (seg) {
                    is Segment.Prose -> SelectionContainer {
                        Text(seg.text.trim(), color = C.Text, fontSize = 15.sp, lineHeight = 21.sp, modifier = Modifier.padding(vertical = 4.dp))
                    }
                    is Segment.Code -> CodeBlock(seg, onSave) { vm.runSnippet(seg.lang, seg.code) }
                }
            }
            m.actions.forEach { ActionCard(vm, it) }
            m.stats?.let { Text(it, color = C.Muted, fontSize = 11.sp, modifier = Modifier.padding(top = 2.dp)) }
        }
        Role.TOOL -> {
            var open by remember { mutableStateOf(false) }
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(C.CardInner)
                    .clickable { open = !open }.padding(12.dp)
            ) {
                val web = Regex("^(Browser|Web search|Data |Clicked|Typed|Pressed|Scrolled|Script result|Fetched|Opened row|Did \\d+ of|Skipped|You already|No search)").containsMatchIn(m.text)
                val kind = if (web) "🌐 Page results" else "📂 Results"
                Text(if (open) "$kind (tap to hide)" else "$kind sent to the assistant (tap to view)", color = C.Muted, fontSize = 13.sp)
                if (open) Text(m.text, color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
            }
        }
        Role.RUN -> {
            val ok = m.text.startsWith("✓")
            val running = m.text.startsWith("Running")
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Color(0xFF0B0C0E))
                    .border(1.dp, if (running) C.Border else if (ok) C.Good else C.Bad, RoundedCornerShape(16.dp))
                    .padding(12.dp)
            ) {
                Text(
                    if (running) "▶ Running on your phone…" else if (ok) "▶ Output" else "▶ Output (error)",
                    color = if (running) C.Muted else if (ok) C.Good else C.Bad, fontSize = 12.sp, fontWeight = FontWeight.SemiBold
                )
                if (!running) SelectionContainer {
                    Text(
                        m.text.substringAfter('\n', ""),
                        color = Color(0xFFD7E3F4), fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                        modifier = Modifier.padding(top = 6.dp)
                    )
                }
            }
        }
        Role.NOTE -> Text(m.text, color = C.Gold, fontSize = 14.sp, modifier = Modifier.fillMaxWidth().padding(4.dp))
    }
}

/** "Thinking… step 2 · 1:05 · 120 words" while a reply is being written, so a slow model never looks frozen. */
@Composable
private fun LiveProgress(vm: AppViewModel, m: UiMessage) {
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(m) {
        while (true) { kotlinx.coroutines.delay(1000); now = System.currentTimeMillis() }
    }
    val secs = if (vm.requestStartedAt > 0) ((now - vm.requestStartedAt) / 1000).coerceAtLeast(0) else 0
    val words = m.text.split(Regex("\\s+")).count { it.isNotBlank() }
    val active = vm.generating && vm.messages.lastOrNull() === m
    val parts = buildList {
        add("Step ${vm.stepNumber}")
        add("%d:%02d".format(secs / 60, secs % 60))
        if (words > 0) add("$words words of hidden thinking")
    }
    Column {
        Text(if (active) "Thinking… " + parts.joinToString(" · ") else "(no reply)", color = C.Muted, fontSize = 14.sp)
        if (active && words > 0) Text("Tap Stop if it takes too long. It stops by itself after a few minutes.", color = C.Muted, fontSize = 12.sp)
    }
}

@Composable
private fun CodeBlock(seg: Segment.Code, onSave: (Segment.Code) -> Unit, onRun: () -> Unit) {
    val runnable = seg.lang.lowercase() in setOf("python", "py", "python3", "sh", "bash", "shell")
    val clipboard = LocalClipboardManager.current
    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 6.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(Color(0xFF0B0C0E))
    ) {
        Row(Modifier.fillMaxWidth().background(C.CardInner).padding(start = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(seg.lang.ifBlank { "code" }, color = C.Muted, fontSize = 12.sp, modifier = Modifier.weight(1f))
            IconButton(onClick = { clipboard.setText(AnnotatedString(seg.code)) }) {
                Icon(Icons.Filled.ContentCopy, "Copy", tint = C.Muted, modifier = Modifier.size(18.dp))
            }
            IconButton(onClick = { onSave(seg) }) {
                Icon(Icons.Filled.Save, "Save to file", tint = C.Accent, modifier = Modifier.size(20.dp))
            }
            if (runnable) {
                IconButton(onClick = onRun) {
                    Icon(Icons.Filled.PlayArrow, "Run", tint = C.Good, modifier = Modifier.size(22.dp))
                }
            }
        }
        SelectionContainer {
            Text(
                seg.code,
                color = Color(0xFFD7E3F4),
                fontFamily = FontFamily.Monospace,
                fontSize = 13.sp,
                lineHeight = 18.sp,
                softWrap = false,
                modifier = Modifier.horizontalScroll(rememberScrollState()).padding(12.dp)
            )
        }
    }
}

@Composable
private fun ActionCard(vm: AppViewModel, p: PendingAction) {
    val danger = p.action is FileAction.Delete
    val isRun = p.action is FileAction.Run
    val isWeb = p.action is FileAction.Browser || p.action is FileAction.Batch
    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(C.CardInner)
            .padding(14.dp)
    ) {
        Text(
            if (danger) "⚠️ Assistant wants to delete" else if (isRun) "▶ Assistant wants to run this code"
            else if (isWeb) "🖱 Assistant wants to do this on the page" else "Assistant wants to change files",
            color = if (danger) C.Bad else C.Accent, fontWeight = FontWeight.SemiBold, fontSize = 13.sp
        )
        val act = p.action
        if (act is FileAction.Batch) {
            act.steps.forEachIndexed { i, st -> Text("${i + 1}. ${st.label}", color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 13.sp) }
        } else Text(act.label, color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 13.sp)
        if (act is FileAction.Browser && (act.kind == "js" || act.kind == "mod")) {
            Text(act.content.take(400), color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 11.sp, maxLines = 8, modifier = Modifier.padding(top = 6.dp))
        }
        val w = p.action
        if (w is FileAction.Write) {
            Text(
                w.content.take(400) + if (w.content.length > 400) "\n…" else "",
                color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 11.sp, maxLines = 8,
                modifier = Modifier.padding(top = 6.dp)
            )
        }
        Spacer(Modifier.height(8.dp))
        when (p.status) {
            ActionStatus.PENDING -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { vm.approve(p) },
                    colors = ButtonDefaults.buttonColors(containerColor = if (danger) C.Bad else C.Accent)
                ) { Text(if (danger) "Delete" else if (isRun) "Run" else if (isWeb) "Do it" else "Approve") }
                OutlinedButton(onClick = { vm.reject(p) }) { Text("Skip") }
            }
            ActionStatus.DONE -> Text("✓ ${p.result ?: "Working…"}", color = C.Good, fontSize = 13.sp)
            ActionStatus.REJECTED -> Text("Skipped", color = C.Muted, fontSize = 13.sp)
        }
        if (p.status == ActionStatus.PENDING && p.result != null) Text(p.result ?: "", color = C.Bad, fontSize = 13.sp)
    }
}

@Composable
internal fun InputBar(vm: AppViewModel, placeholder: String = "Ask for code, a file task or a web search…", onSend: () -> Unit = { vm.send() }) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 6.dp)) {
        vm.attachment?.let { f ->
            Row(
                Modifier.clip(RoundedCornerShape(50)).background(C.AccentDark).padding(start = 12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(Icons.Filled.AttachFile, null, tint = C.Accent, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(4.dp))
                Text(f.name, color = C.Text, fontSize = 13.sp, maxLines = 1)
                IconButton(onClick = { vm.attachment = null }) { Icon(Icons.Filled.Close, "Remove", tint = C.Muted, modifier = Modifier.size(16.dp)) }
            }
            Spacer(Modifier.height(6.dp))
        }
        Row(verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(
                value = vm.input,
                onValueChange = { vm.input = it },
                placeholder = { Text(placeholder) },
                maxLines = 6,
                shape = RoundedCornerShape(24.dp),
                modifier = Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier
                    .size(54.dp)
                    .clip(CircleShape)
                    .background(if (vm.generating) C.Bad else C.Accent)
                    .clickable { if (vm.generating) vm.stopGenerating() else onSend() },
                contentAlignment = Alignment.Center
            ) {
                Icon(if (vm.generating) Icons.Filled.Stop else Icons.Filled.Send, if (vm.generating) "Stop" else "Send", tint = C.Bg)
            }
        }
    }
}

