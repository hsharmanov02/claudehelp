package com.localforge.app.ui

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
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.data.HfFile
import com.localforge.app.data.HuggingFace
import com.localforge.app.data.LocalModel
import com.localforge.app.data.formatBytes

@Composable
fun ModelsScreen(vm: AppViewModel) {
    var confirmDelete by remember { mutableStateOf<LocalModel?>(null) }
    val ram = vm.device?.totalRam ?: 0L

    LazyColumn(
        Modifier.fillMaxSize().padding(horizontal = 20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        item {
            Spacer(Modifier.height(12.dp))
            ScreenTitle("Models")
            Text("Uncensored models only · runs fully on your phone", color = C.Muted, fontSize = 15.sp)
            vm.engineMessage?.let {
                Spacer(Modifier.height(8.dp))
                Text(it, color = C.Gold, fontSize = 14.sp)
            }
        }

        item { PhoneCard(vm) }

        if (vm.recommendations.isNotEmpty()) {
            item { SectionHeader("Best uncensored models for your phone", "Biggest model that fits your memory, at a good quality level") }
            items(vm.recommendations.withIndex().toList(), key = { "rec-" + it.value.file.repo }) { (i, r) ->
                Panel(borderColor = if (i == 0) C.Gold else C.Border) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (i == 0) {
                            Icon(Icons.Filled.Star, null, tint = C.Gold, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Best pick", color = C.Gold, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                        } else {
                            Text("#${i + 1}", color = C.Muted, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                        }
                    }
                    Text(r.file.repo, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 16.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(r.reason, color = C.Muted, fontSize = 13.sp)
                    Text("↓ ${r.downloads} downloads", color = C.Muted, fontSize = 12.sp)
                    Spacer(Modifier.height(8.dp))
                    FileRow(vm, r.file)
                }
            }
        }

        item { SectionHeader("Your models") }
        if (vm.localModels.isEmpty()) {
            item { Text("None yet. Scan your phone or download one below.", color = C.Muted) }
        }
        items(vm.localModels, key = { "local-" + it.path }) { m ->
            val active = vm.loadedModel == m.name
            val external = !vm.store.isOwn(m)
            Panel(borderColor = if (active) C.Accent else C.Border) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.weight(1f)) { StatusDot(active, m.name.removeSuffix(".gguf").take(40)) }
                    if (active) Tag("Active", Color(0xFF1E3B26), C.Good) else Tag(if (external) "On phone" else "Local")
                }
                Text(
                    formatBytes(m.size) + (if (ram > 0 && m.size > ram * 0.6) " · may be too big for this phone" else ""),
                    color = C.Muted, fontSize = 13.sp
                )
                if (external) Text(m.path, color = C.Muted, fontSize = 11.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { vm.loadModel(m) }, enabled = !vm.loading && !active) {
                        Text(if (active) "Loaded" else "▶  Load & use", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = { confirmDelete = m }) { Icon(Icons.Filled.Delete, "Remove", tint = C.Muted) }
                }
            }
        }

        val stray = vm.downloads.values.filter { d ->
            vm.recommendations.none { it.file.fileName == d.file.fileName } && vm.repoFiles.none { it.fileName == d.file.fileName }
        }
        if (stray.isNotEmpty()) {
            item { SectionHeader("Downloads") }
            items(stray, key = { "dl-" + it.file.fileName }) { d ->
                Panel {
                    Text(d.file.fileName, color = C.Text, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(6.dp))
                    FileRow(vm, d.file)
                }
            }
        }

        item {
            Spacer(Modifier.height(8.dp))
            SectionHeader("Search Hugging Face", "Results are filtered to uncensored / abliterated models")
            OutlinedTextField(
                value = vm.searchQuery,
                onValueChange = { vm.searchQuery = it },
                placeholder = { Text("Search uncensored GGUF models") },
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
                listOf("coder", "llama", "qwen", "gemma", "mistral", "dolphin", "phi").forEach { q ->
                    OutlinedButton(onClick = { vm.search(q) }, shape = RoundedCornerShape(14.dp)) { Text(q) }
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
                val tooBig = ram > 0 && f.size > ram * 0.6
                Column(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(C.CardInner).padding(14.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Tag(f.quant, C.AccentDark, C.Accent)
                        Spacer(Modifier.width(8.dp))
                        Text(formatBytes(f.size), color = if (tooBig) C.Bad else C.Text, fontWeight = FontWeight.SemiBold)
                        if (tooBig) Text("  too big for this phone", color = C.Bad, fontSize = 12.sp)
                    }
                    val speed = HuggingFace.estTokensPerSec(f.size).toInt()
                    val lowQuant = f.quant.startsWith("Q2") || f.quant.startsWith("Q3") || f.quant.startsWith("IQ1") ||
                        f.quant.startsWith("IQ2") || f.quant.startsWith("IQ3")
                    Text(
                        "~$speed tokens/sec" + when {
                            lowQuant -> " · low quality, small models often output gibberish"
                            speed < 5 -> " · slow on a phone"
                            f.quant == "Q4_0" || f.quant == "IQ4_NL" -> " · fastest on phones"
                            else -> ""
                        },
                        color = if (lowQuant || speed < 5) C.Gold else C.Muted, fontSize = 12.sp
                    )
                    Text(f.fileName, color = C.Muted, fontSize = 12.sp, fontFamily = FontFamily.Monospace, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.height(6.dp))
                    FileRow(vm, f)
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
                    Text("Files ›", color = C.Accent)
                }
            }
        }
        item { Spacer(Modifier.height(16.dp)) }
    }

    confirmDelete?.let { m ->
        val external = !vm.store.isOwn(m)
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text(if (external) "Remove from list?" else "Delete model?") },
            text = {
                Text(
                    if (external) "${m.name} stays on your phone; LocalForge just stops listing it."
                    else "${m.name} (${formatBytes(m.size)}) will be deleted from this phone."
                )
            },
            confirmButton = { TextButton(onClick = { vm.deleteModel(m); confirmDelete = null }) { Text(if (external) "Remove" else "Delete", color = C.Bad) } },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Cancel") } }
        )
    }
}

@Composable
private fun PhoneCard(vm: AppViewModel) {
    val d = vm.device
    Panel(borderColor = C.Accent) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.PhoneAndroid, null, tint = C.Accent)
            Spacer(Modifier.width(10.dp))
            Text("Your phone", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
        }
        if (d != null) {
            Spacer(Modifier.height(6.dp))
            Text("${d.model} · ${d.chip}", color = C.Muted, fontSize = 13.sp)
            Text(
                "RAM ${formatBytes(d.totalRam)} (${formatBytes(d.availRam)} free) · ${d.cores} cores · ${formatBytes(d.freeStorage)} storage free",
                color = C.Muted, fontSize = 13.sp
            )
            Text("Best size for speed on this phone: up to ~${formatBytes(d.modelBudget)} (1.5B–4B models at Q4)", color = C.Text, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
        }
        vm.scanSummary?.let {
            Spacer(Modifier.height(6.dp))
            Text(it, color = C.Text, fontSize = 14.sp)
        }
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = { vm.scanPhone() },
            enabled = !vm.scanning,
            shape = RoundedCornerShape(50),
            colors = ButtonDefaults.buttonColors(containerColor = C.Accent),
            modifier = Modifier.fillMaxWidth().height(52.dp)
        ) {
            if (vm.scanning) {
                CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp, color = C.Bg)
                Spacer(Modifier.width(10.dp))
                Text("Scanning…")
            } else {
                Text("Scan phone & pick best uncensored model", fontWeight = FontWeight.Bold)
            }
        }
    }
}

/** Download control for one file: button, live progress, cancel, retry, or "downloaded". */
@Composable
private fun FileRow(vm: AppViewModel, f: HfFile) {
    val d = vm.downloads[f.fileName]
    val have = vm.localModels.any { it.name == f.fileName }
    when {
        d != null && d.error != null -> Column {
            Text(d.error ?: "", color = C.Bad, fontSize = 13.sp)
            Row {
                TextButton(onClick = { vm.download(f) }) {
                    Icon(Icons.Filled.Refresh, null); Spacer(Modifier.width(6.dp)); Text("Retry / resume")
                }
                TextButton(onClick = { vm.cancelDownload(f.fileName) }) { Text("Remove", color = C.Muted) }
            }
        }
        d != null -> Column {
            val total = if (d.total > 0) d.total else f.size
            if (total > 0) LinearProgressIndicator(progress = { (d.done.toFloat() / total).coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            else LinearProgressIndicator(Modifier.fillMaxWidth())
            Row(verticalAlignment = Alignment.CenterVertically) {
                val pct = if (total > 0) (d.done * 100 / total).toInt() else 0
                Text("Downloading $pct% · ${formatBytes(d.done)} of ${formatBytes(total)}", color = C.Text, fontSize = 13.sp, modifier = Modifier.weight(1f))
                IconButton(onClick = { vm.cancelDownload(f.fileName) }) { Icon(Icons.Filled.Close, "Cancel", tint = C.Bad) }
            }
            Text("Keep LocalForge open until it finishes.", color = C.Muted, fontSize = 12.sp)
        }
        have -> Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Filled.CheckCircle, null, tint = C.Good, modifier = Modifier.size(20.dp))
            Spacer(Modifier.width(6.dp))
            Text("Downloaded — load it from Your models", color = C.Good, fontSize = 14.sp)
        }
        else -> Button(
            onClick = { vm.download(f) },
            shape = RoundedCornerShape(50),
            colors = ButtonDefaults.buttonColors(containerColor = C.AccentDark, contentColor = C.Accent),
            modifier = Modifier.fillMaxWidth()
        ) {
            Icon(Icons.Filled.Download, null)
            Spacer(Modifier.width(8.dp))
            Text("Download ${f.quant} · ${formatBytes(f.size)}", fontWeight = FontWeight.SemiBold)
        }
    }
}
