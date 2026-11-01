package com.localforge.app.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.Tab
import com.localforge.app.data.formatBytes

fun openAllFilesSettings(ctx: Context) {
    try {
        ctx.startActivity(
            Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, Uri.parse("package:${ctx.packageName}"))
        )
    } catch (e: Exception) {
        ctx.startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
    }
}

@Composable
fun HomeScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        ScreenTitle("LocalForge")
        Text("Offline coding assistant · your files, your phone", color = C.Muted, fontSize = 15.sp)

        EngineCard(vm)

        Panel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(52.dp).clip(RoundedCornerShape(16.dp)).background(C.AccentDark),
                    contentAlignment = Alignment.Center
                ) { Icon(Icons.Filled.CheckCircle, null, tint = C.Accent, modifier = Modifier.size(30.dp)) }
                Spacer(Modifier.width(14.dp))
                Column {
                    Text("Setup checklist", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 22.sp)
                    Text("Three steps and you're coding.", color = C.Muted, fontSize = 15.sp)
                }
            }
            Spacer(Modifier.height(14.dp))
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(C.CardInner).padding(vertical = 6.dp)
            ) {
                CheckRow("Allow all-files access", "Lets the assistant read, create and delete files", vm.hasFileAccess) {
                    openAllFilesSettings(ctx)
                }
                CheckRow("Download a model", "From Hugging Face on the Models tab", vm.localModels.isNotEmpty()) {
                    vm.tab = Tab.MODELS
                }
                CheckRow("Load a model", "Tap Load & use above", vm.loadedModel != null, null)
            }
        }

        RuntimeSettings(vm)
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun EngineCard(vm: AppViewModel) {
    var menu by remember { mutableStateOf(false) }
    val loaded = vm.loadedModel
    Panel(borderColor = if (loaded != null) C.Accent else C.Border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { StatusDot(loaded != null, if (loaded != null) "Model ready" else "No model loaded") }
            if (loaded != null) Tag("Local", C.CardInner) else Tag("Idle")
        }
        Spacer(Modifier.height(8.dp))
        Text(
            loaded ?: (vm.lastModelName?.let { "Last used: $it" } ?: "Pick a downloaded GGUF model"),
            color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 14.sp,
            maxLines = 1, overflow = TextOverflow.Ellipsis
        )
        Text("${vm.nCtx} tokens · ${vm.threads} threads · CPU", color = C.Muted, fontSize = 13.sp)
        vm.engineMessage?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, color = C.Text, fontSize = 14.sp)
        }
        Spacer(Modifier.height(14.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) {
                Button(
                    onClick = { if (vm.localModels.isEmpty()) vm.tab = Tab.MODELS else menu = true },
                    enabled = !vm.loading,
                    modifier = Modifier.fillMaxWidth().height(56.dp),
                    shape = RoundedCornerShape(50),
                    colors = ButtonDefaults.buttonColors(containerColor = C.Accent)
                ) {
                    if (vm.loading) {
                        CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = C.Bg)
                    } else {
                        Icon(Icons.Filled.PlayArrow, null)
                        Spacer(Modifier.width(6.dp))
                        Text(if (vm.localModels.isEmpty()) "Get a model" else "Load & use", fontSize = 17.sp, fontWeight = FontWeight.Bold)
                        if (vm.localModels.isNotEmpty()) Icon(Icons.Filled.KeyboardArrowDown, null)
                    }
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    vm.localModels.forEach { m ->
                        DropdownMenuItem(
                            text = { Text("${m.name}  (${formatBytes(m.size)})", maxLines = 2) },
                            onClick = { menu = false; vm.loadModel(m) }
                        )
                    }
                }
            }
            if (loaded != null) {
                Spacer(Modifier.width(8.dp))
                TextButton(onClick = { vm.unloadModel() }) { Text("Unload", color = C.Bad) }
            }
        }
    }
}

@Composable
private fun CheckRow(title: String, subtitle: String, done: Boolean, onClick: (() -> Unit)?) {
    Row(
        Modifier
            .fillMaxWidth()
            .let { if (onClick != null && !done) it.clickable(onClick = onClick) else it }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(
            if (done) Icons.Filled.CheckCircle else Icons.Filled.RadioButtonUnchecked,
            null,
            tint = if (done) C.Accent else C.Muted,
            modifier = Modifier.size(28.dp)
        )
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 17.sp)
            Text(subtitle, color = C.Muted, fontSize = 13.sp)
        }
        if (onClick != null && !done) Text("Open", color = C.Accent, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun RuntimeSettings(vm: AppViewModel) {
    var tokenDraft by remember { mutableStateOf(vm.hfToken) }
    Panel {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.Settings, null, tint = C.Accent)
            Spacer(Modifier.width(10.dp))
            Text("Runtime settings", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
        }
        Text("Context and threads apply next time you load a model.", color = C.Muted, fontSize = 13.sp)

        ChipRow("Context size", listOf(2048, 4096, 8192), vm.nCtx, { "$it" }) { vm.updateCtx(it) }
        ChipRow("CPU threads", listOf(2, 4, 6, 8), vm.threads, { "$it" }) { vm.updateThreads(it) }
        ChipRow("Max reply length", listOf(512, 1024, 2048), vm.maxTokens, { "$it" }) { vm.updateMaxTokens(it) }

        Spacer(Modifier.height(10.dp))
        Text("Creativity: ${"%.1f".format(vm.temperature)}  (lower = more precise code)", color = C.Text, fontSize = 15.sp)
        Slider(value = vm.temperature, onValueChange = { vm.updateTemperature((it * 10).toInt() / 10f) }, valueRange = 0f..1.2f)

        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("Auto-apply file changes", color = C.Text, fontWeight = FontWeight.SemiBold)
                Text("Saves and new folders happen without asking. Deletes always ask.", color = C.Muted, fontSize = 13.sp)
            }
            Switch(checked = vm.autoApprove, onCheckedChange = { vm.updateAutoApprove(it) })
        }

        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = tokenDraft,
            onValueChange = { tokenDraft = it; vm.updateHfToken(it) },
            label = { Text("Hugging Face token (optional, for gated models)") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth()
        )
    }
}

@Composable
private fun <T> ChipRow(label: String, options: List<T>, selected: T, text: (T) -> String, onPick: (T) -> Unit) {
    Spacer(Modifier.height(10.dp))
    Text(label, color = C.Text, fontSize = 15.sp)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        options.forEach { o ->
            FilterChip(selected = o == selected, onClick = { onPick(o) }, label = { Text(text(o)) })
        }
    }
}
