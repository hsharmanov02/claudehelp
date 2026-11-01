package com.localforge.app.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.localforge.app.AppViewModel
import com.localforge.app.agent.TermuxBridge
import java.io.File

/** Home-tab card: which languages can run, and guided Termux setup for the rest. */
@Composable
fun CodeRunnersCard(vm: AppViewModel) {
    val ctx = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { vm.refreshTermux() }
    val ready = vm.termuxInstalled && vm.termuxAllowed

    Panel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.Terminal, null, tint = C.Accent)
            Spacer(Modifier.width(10.dp))
            Text("Code runners", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
        }
        Spacer(Modifier.height(6.dp))
        Text(
            "Ready now, no setup: Python · JavaScript · TypeScript · HTML · SQL · Shell · Lua · Ruby · PHP · C · C++",
            color = C.Good, fontSize = 14.sp
        )
        Text(
            if (ready) "Termux connected: Java · Kotlin · Go · Rust · Perl, plus full compilers for C, C++, PHP, Ruby and Node"
            else "PHP, C and C++ run in simple built-in interpreters. Optional: connect Termux for Java, Kotlin, Go, Rust and full C/C++ compilers.",
            color = if (ready) C.Good else C.Muted, fontSize = 13.sp
        )
        var showSetup by remember { mutableStateOf(false) }
        if (!ready) {
            TextButton(onClick = { showSetup = !showSetup }) { Text(if (showSetup) "Hide Termux setup" else "Show optional Termux setup") }
        }
        if (ready || !showSetup) {
            vm.runnerMessage?.let {
                Spacer(Modifier.height(6.dp))
                Text(it, color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
            }
            if (ready) OutlinedButton(onClick = { vm.testTermux() }) { Text("Test Termux") }
            return@Panel
        }
        Spacer(Modifier.height(6.dp))

        Step(1, "Install Termux (from GitHub or F-Droid, not the Play Store)", vm.termuxInstalled) {
            ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/termux/termux-app/releases/latest")))
        }
        Step(2, "Open Termux, paste the setup command and press Enter (≈10 min, ~2 GB). Tap Allow when it asks for storage.", false) {
            clipboard.setText(AnnotatedString(TermuxBridge.SETUP_COMMAND))
            vm.runnerMessage = "Setup command copied. Paste it in Termux."
        }
        Text(
            TermuxBridge.SETUP_COMMAND,
            color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 11.sp,
            modifier = Modifier.fillMaxWidth().padding(start = 36.dp, bottom = 6.dp)
                .clip(RoundedCornerShape(10.dp)).background(C.CardInner).padding(8.dp)
        )
        Step(3, "Allow LocalForge to send code to Termux", vm.termuxAllowed) {
            permission.launch(TermuxBridge.PERMISSION)
        }
        Step(4, "Test it", false) { vm.testTermux() }

        vm.runnerMessage?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 12.sp)
        }
    }
}

@Composable
private fun Step(n: Int, text: String, done: Boolean, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(
            if (done) Icons.Filled.CheckCircle else Icons.Filled.RadioButtonUnchecked, null,
            tint = if (done) C.Accent else C.Muted
        )
        Spacer(Modifier.width(10.dp))
        Text("$n. $text", color = C.Text, fontSize = 14.sp, modifier = Modifier.weight(1f))
        if (!done) {
            OutlinedButton(onClick = onClick) {
                Text(
                    when (n) { 1 -> "Get"; 2 -> "Copy"; 3 -> "Allow"; else -> "Test" },
                    fontSize = 13.sp
                )
            }
        }
    }
}

/** Full-screen live preview of an HTML page the assistant made. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun HtmlPreviewScreen(file: File, onClose: () -> Unit) {
    BackHandler { onClose() }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onClose) { Icon(Icons.Filled.ArrowBack, "Back", tint = C.Text) }
            Column(Modifier.weight(1f)) {
                Text("Preview", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                Text(file.absolutePath, color = C.Muted, fontSize = 11.sp, maxLines = 1)
            }
            TextButton(onClick = onClose) { Text("Close") }
        }
        AndroidView(
            modifier = Modifier.weight(1f).fillMaxWidth(),
            factory = { c ->
                WebView(c).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.allowFileAccess = true
                    webViewClient = WebViewClient()
                }
            },
            update = { wv ->
                val html = try { file.readText() } catch (e: Exception) { "<p>Couldn't open: ${e.message}</p>" }
                wv.loadDataWithBaseURL("file://${file.parent}/", html, "text/html", "utf-8", null)
            }
        )
    }
}
