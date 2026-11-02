package com.localforge.app.ui

import android.view.ViewGroup
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowForward
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.TableChart
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.localforge.app.AppViewModel
import com.localforge.app.agent.WebBrowser
import kotlinx.coroutines.launch

/** Orange used for the AI's live steps on the page (same as the outline drawn around what it clicks). */
private val Step = Color(0xFFFF7A28)

@Composable
fun BrowserScreen(vm: AppViewModel) {
    val b = WebBrowser
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    var address by remember { mutableStateOf("") }
    var editing by remember { mutableStateOf(false) }
    var extracted by remember { mutableStateOf<String?>(null) }
    var showMods by remember { mutableStateOf(false) }
    val modCount = run { b.modsVersion; b.modsFor(b.url).size }

    BackHandler(enabled = vm.pagePanelOpen || b.canGoBack) {
        if (vm.pagePanelOpen) vm.pagePanelOpen = false else b.back()
    }

    Column(Modifier.fillMaxSize()) {
        // ---- toolbar ----
        Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { b.back() }, enabled = b.canGoBack) { Icon(Icons.Filled.ArrowBack, "Back", tint = if (b.canGoBack) C.Text else C.Border) }
            IconButton(onClick = { b.forward() }, modifier = Modifier.size(36.dp)) { Icon(Icons.Filled.ArrowForward, "Forward", tint = C.Muted, modifier = Modifier.size(20.dp)) }
            OutlinedTextField(
                value = if (editing) address else (b.url.takeIf { b.hasPage } ?: ""),
                onValueChange = { address = it; editing = true },
                singleLine = true,
                placeholder = { Text("Search or type a web address", fontSize = 14.sp) },
                textStyle = androidx.compose.ui.text.TextStyle(fontSize = 14.sp, color = C.Text),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = { b.go(address); editing = false; focus.clearFocus() }),
                shape = RoundedCornerShape(50),
                modifier = Modifier.weight(1f).heightIn(min = 48.dp)
            )
            IconButton(onClick = { b.reload() }) { Icon(Icons.Filled.Refresh, "Reload", tint = C.Muted) }
            IconButton(onClick = {
                scope.launch { extracted = try { b.extract("") } catch (e: Exception) { "Couldn't read the page: ${e.message}" } }
            }, enabled = b.hasPage) { Icon(Icons.Filled.TableChart, "Extract data", tint = if (b.hasPage) C.Accent else C.Border) }
            if (modCount > 0) IconButton(onClick = { showMods = true }) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Filled.Edit, "Saved edits", tint = C.Gold)
                    Text("$modCount", color = C.Bg, fontSize = 9.sp, fontWeight = FontWeight.Bold,
                        modifier = Modifier.align(Alignment.TopEnd).size(13.dp).clip(RoundedCornerShape(50)).background(C.Gold))
                }
            }
        }
        if (b.loading) LinearProgressIndicator(progress = { b.progress / 100f }, modifier = Modifier.fillMaxWidth().height(2.dp), color = C.Accent)

        // ---- page + AI panel ----
        BoxWithConstraints(Modifier.weight(1f).fillMaxWidth()) {
            val full = maxHeight
            var fraction by remember { mutableFloatStateOf(0.5f) }
            val density = LocalDensity.current
            val panelHeight = full * fraction

            Column(Modifier.fillMaxSize()) {
                // The page shrinks above the panel so nothing is hidden behind it.
                Box(Modifier.fillMaxWidth().weight(1f)) {
                    WebViewHost()
                    if (!b.hasPage) StartPage(vm)
                    b.step?.let { s ->
                        Row(
                            Modifier.align(Alignment.TopCenter).padding(10.dp).clip(RoundedCornerShape(50))
                                .background(Step).padding(horizontal = 14.dp, vertical = 7.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) { Text("AI: $s…", color = Color.White, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
                    }
                    b.notice?.let { n ->
                        LaunchedEffect(n) { kotlinx.coroutines.delay(4000); if (b.notice == n) b.notice = null }
                        Text(n, color = C.Text, fontSize = 13.sp, modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp)
                            .clip(RoundedCornerShape(12.dp)).background(C.CardInner).padding(10.dp))
                    }
                    if (!vm.pagePanelOpen) {
                        Row(
                            Modifier.align(Alignment.BottomEnd).padding(14.dp).clip(RoundedCornerShape(50)).background(C.Accent)
                                .clickable { vm.pagePanelOpen = true }.padding(horizontal = 16.dp, vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Icon(Icons.Filled.AutoAwesome, null, tint = C.Bg, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Ask AI about this page", color = C.Bg, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                        }
                    }
                }
                if (vm.pagePanelOpen) {
                    Column(
                        Modifier.fillMaxWidth().height(panelHeight)
                            .clip(RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp))
                            .background(C.Card)
                            .border(1.dp, C.Border, RoundedCornerShape(topStart = 22.dp, topEnd = 22.dp))
                    ) {
                        // Drag the handle to give the page or the chat more room.
                        Box(
                            Modifier.fillMaxWidth().height(26.dp).pointerInput(full) {
                                detectVerticalDragGestures { _, dy ->
                                    val total = with(density) { full.toPx() }
                                    fraction = (fraction - dy / total).coerceIn(0.25f, 0.85f)
                                }
                            },
                            contentAlignment = Alignment.Center
                        ) { Box(Modifier.size(width = 44.dp, height = 5.dp).clip(RoundedCornerShape(50)).background(C.Border)) }
                        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("Ask AI about this page", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                                Text(
                                    if (vm.browserAutoApprove || vm.autoApprove) "Clicks and typing run by themselves (Home → settings)"
                                    else "You approve each click or typing step here",
                                    color = C.Muted, fontSize = 11.sp
                                )
                            }
                            IconButton(onClick = { vm.pagePanelOpen = false }) { Icon(Icons.Filled.Close, "Close", tint = C.Muted) }
                        }
                        if (vm.nCtx < 4096) Text(
                            "Web pages need more memory than the model has. Set Context size to 4096+ on the Home tab and reload the model.",
                            color = C.Gold, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp)
                        )
                        PanelChat(vm, Modifier.weight(1f))
                        InputBar(vm, placeholder = "What should I do on this page?") { vm.send(onPage = true) }
                    }
                }
            }
        }
    }

    b.picker?.let { PickerDialog(it) { b.picker = null } }
    if (showMods) {
        b.modsVersion
        val mods = b.modsFor(b.url)
        AlertDialog(
            onDismissRequest = { showMods = false },
            title = { Text("Saved edits for ${b.siteOf(b.url)}") },
            text = {
                if (mods.isEmpty()) Text("No saved edits for this site.", color = C.Muted)
                else Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
                    mods.forEach { m ->
                        Column(Modifier.fillMaxWidth().padding(vertical = 6.dp).clip(RoundedCornerShape(12.dp)).background(C.CardInner).padding(12.dp)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(m.note.ifBlank { "Edit" }, color = C.Text, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                TextButton(onClick = { b.removeMod(m); if (b.modsFor(b.url).isEmpty()) showMods = false }) { Text("Remove", color = C.Bad) }
                            }
                            Text(m.js.take(200), color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 11.sp, maxLines = 4)
                        }
                    }
                    Text("These run every time you open this site. Reload to apply changes.", color = C.Muted, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                }
            },
            confirmButton = { TextButton(onClick = { showMods = false }) { Text("Close") } }
        )
    }
    extracted?.let { text ->
        val clipboard = LocalClipboardManager.current
        AlertDialog(
            onDismissRequest = { extracted = null },
            title = { Text("Data on this page") },
            text = {
                SelectionContainer {
                    Text(text, fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = C.Text,
                        modifier = Modifier.heightIn(max = 460.dp).verticalScroll(rememberScrollState()))
                }
            },
            confirmButton = {
                TextButton(onClick = { clipboard.setText(AnnotatedString(text)); b.notice = "Copied"; extracted = null }) { Text("Copy") }
            },
            dismissButton = {
                TextButton(onClick = {
                    vm.input = "Using the data on this page, "
                    vm.pagePanelOpen = true
                    extracted = null
                }) { Text("Ask AI") }
            }
        )
    }
}

/** The shared browser WebView. It's moved here from wherever it was, so the AI and the user see the same page. */
@Composable
private fun WebViewHost() {
    val gen = WebBrowser.generation
    val url = WebBrowser.url
    val loading = WebBrowser.loading
    androidx.compose.runtime.key(gen) {
        AndroidView(
            modifier = Modifier.fillMaxSize(),
            factory = {
                val w = WebBrowser.view()
                (w.parent as? ViewGroup)?.removeView(w)
                w.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                w.onResume()
                w.resumeTimers()
                // A WebView moved back onto the screen can keep showing a black frame until it's asked to redraw.
                w.post { w.requestLayout(); w.invalidate() }
                w
            },
            update = { w ->
                if (url.isNotEmpty() || !loading) w.postInvalidate()
            },
            onRelease = { w -> (w.parent as? ViewGroup)?.removeView(w) }
        )
    }
}

@Composable
private fun StartPage(vm: AppViewModel) {
    Column(
        Modifier.fillMaxSize().background(C.Bg).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        Text("Browser", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 22.sp)
        Text(
            "Browse here yourself, or tap \"Ask AI about this page\" and watch the AI read the page, pull its data into rows, " +
                "click and fill in forms. Each step shows on the page in orange.",
            color = C.Muted, fontSize = 14.sp
        )
        listOf("news.ycombinator.com", "en.wikipedia.org", "www.bbc.co.uk/news").forEach { site ->
            Text(site, color = C.Accent, fontSize = 15.sp, modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp))
                .background(C.CardInner).clickable { WebBrowser.go(site) }.padding(12.dp))
        }
        if (vm.nCtx < 4096) {
            Text("Tip: web pages are long. Set Context size to 4096 or more on the Home tab (then reload the model) so the AI can read them.",
                color = C.Gold, fontSize = 13.sp)
        }
    }
}

@Composable
private fun PanelChat(vm: AppViewModel, modifier: Modifier) {
    val listState = rememberLazyListState()
    val lastText = vm.messages.lastOrNull()?.text?.length ?: 0
    val lastActions = vm.messages.lastOrNull()?.actions?.size ?: 0
    LaunchedEffect(vm.messages.size, lastText, lastActions) {
        if (vm.messages.isNotEmpty()) listState.scrollToItem(vm.messages.size)
    }
    LazyColumn(
        state = listState,
        modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        if (vm.messages.isEmpty()) item {
            Text(
                "What should I do on this page? I can read it, pull its data into a table, click, type and fill in forms. " +
                    "You'll see each step happen on the page.",
                color = C.Muted, fontSize = 14.sp, modifier = Modifier.padding(vertical = 8.dp)
            )
        }
        items(vm.messages) { m -> MessageView(vm, m) }
        item { Spacer(Modifier.height(4.dp)) }
    }
}
