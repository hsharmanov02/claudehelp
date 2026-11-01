package com.localforge.app.ui

import android.app.ActivityManager
import android.content.Context
import androidx.compose.foundation.background
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.data.LocalModel
import com.localforge.app.data.formatBytes

private fun totalRam(ctx: Context): Long {
    val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
    val info = ActivityManager.MemoryInfo()
    am.getMemoryInfo(info)
    return info.totalMem
}

@Composable
fun ModelsScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    val ram = remember { totalRam(ctx) }
    var confirmDelete by remember { mutableStateOf<LocalModel?>(null) }

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Spacer(Modifier.height(12.dp))
            ScreenTitle("Models")
            Text("Download, load and manage local GGUF models", color = C.Muted, fontSize = 15.sp)
            Text("Phone memory: ${formatBytes(ram)}", color = C.Muted, fontSize = 13.sp)
        }

        if (vm.localModels.isEmpty() && vm.downloads.isEmpty()) {
            item {
                Panel {
                    Text("No models yet", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                    Text("Search below. For coding on a phone, a 1.5B–3B \"Coder\" model in Q4_K_M is the sweet spot.", color = C.Muted)
                }
            }
        }

        items(vm.localModels, key = { "local-" + it.name }) { m ->
            val active = vm.loadedModel == m.name
            Panel(borderColor = if (active) C.Accent else C.Border) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) {
                        StatusDot(active, m.name.removeSuffix(".gguf").take(40))
                    }
                    if (active) Tag("Active", androidx.compose.ui.graphics.Color(0xFF1E3B26), C.Good) else Tag("Local")
                }
                Text(formatBytes(m.size) + if (m.size > ram * 0.6) " · may be too big for this phone" else "", color = C.Muted, fontSize = 13.sp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { vm.loadModel(m) }, enabled = !vm.loading && !active) {
                        Text(if (active) "Loaded" else "▶  Load & use", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = { confirmDelete = m }) { Icon(Icons.Filled.Delete, "Delete", tint = C.Muted) }
                }
            }
        }

        items(vm.downloads, key = { "dl-" + it.id }) { d ->
            Panel {
                Text(d.fileName, color = C.Text, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(8.dp))
                if (d.total > 0) LinearProgressIndicator(progress = { d.done.toFloat() / d.total }, modifier = Modifier.fillMaxWidth())
                else LinearProgressIndicator(Modifier.fillMaxWidth())
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${formatBytes(d.done)} of ${formatBytes(d.total)}", color = C.Muted, fontSize = 13.sp, modifier = Modifier.weight(1f))
                    TextButton(onClick = { vm.cancelDownload(d.fileName) }) { Text("Cancel", color = C.Bad) }
                }
            }
        }

        item {
            Spacer(Modifier.height(8.dp))
            SectionHeader("Download from Hugging Face", "Public models need no token. Add one in Home → settings for gated models.")
            OutlinedTextField(
                value = vm.searchQuery,
                onValueChange = { vm.searchQuery = it },
                placeholder = { Text("Search GGUF models") },
                leadingIcon = { Icon(Icons.Filled.Search, null) },
                singleLine = true,
                shape = RoundedCornerShape(28.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { vm.search() }),
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))
            Text("Try a model family", color = C.Muted, fontSize = 14.sp)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("qwen coder", "deepseek coder", "llama", "qwen", "gemma", "phi").forEach { q ->
                    OutlinedButton(onClick = { vm.search(q) }, shape = RoundedCornerShape(14.dp)) { Text(q) }
                }
            }
        }

        item {
            Panel(borderColor = C.Gold) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Star, null, tint = C.Gold)
                    Spacer(Modifier.width(8.dp))
                    Text("Recommended for coding", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 17.sp)
                }
                Text("Qwen2.5-Coder 1.5B Instruct — pick the Q4_K_M file (about 1 GB). Use the 3B version if your phone has 8 GB+ RAM.", color = C.Muted, fontSize = 14.sp)
                Row {
                    TextButton(onClick = { vm.search("Qwen2.5-Coder-1.5B-Instruct-GGUF") }) { Text("Show 1.5B") }
                    TextButton(onClick = { vm.search("Qwen2.5-Coder-3B-Instruct-GGUF") }) { Text("Show 3B") }
                }
            }
        }

        if (vm.searching || vm.repoLoading) {
            item { Box(Modifier.fillMaxWidth().padding(20.dp), contentAlignment = Alignment.Center) { CircularProgressIndicator() } }
        }
        vm.searchError?.let { err -> item { Text(err, color = C.Bad) } }

        val repo = vm.openRepo
        if (repo != null) {
            item {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = { vm.selectRepo(null) }) { Icon(Icons.Filled.ArrowBack, "Back", tint = C.Text) }
                    Text(repo, color = C.Text, fontWeight = FontWeight.Bold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
            }
            items(vm.repoFiles, key = { "f-" + it.path }) { f ->
                val tooBig = f.size > ram * 0.6
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(C.CardInner).padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Tag(f.quant, C.AccentDark, C.Accent)
                            Spacer(Modifier.width(8.dp))
                            Text(formatBytes(f.size), color = if (tooBig) C.Bad else C.Text, fontWeight = FontWeight.SemiBold)
                        }
                        Text(f.fileName, color = C.Muted, fontSize = 12.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        if (tooBig) Text("Probably too big for this phone", color = C.Bad, fontSize = 12.sp)
                    }
                    IconButton(onClick = { vm.download(f) }) { Icon(Icons.Filled.Download, "Download", tint = C.Accent) }
                }
            }
        } else {
            items(vm.searchResults, key = { "r-" + it.id }) { r ->
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(C.CardInner)
                        .clickable { vm.selectRepo(r.id) }.padding(14.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(r.id, color = C.Text, fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        Text("↓ ${r.downloads}   ♥ ${r.likes}", color = C.Muted, fontSize = 13.sp)
                    }
                    Text("Files", color = C.Accent)
                }
            }
        }
        item { Spacer(Modifier.height(16.dp)) }
    }

    confirmDelete?.let { m ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Delete model?") },
            text = { Text("${m.name} (${formatBytes(m.size)}) will be removed from this phone.") },
            confirmButton = { TextButton(onClick = { vm.deleteModel(m); confirmDelete = null }) { Text("Delete", color = C.Bad) } },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Cancel") } }
        )
    }
}

