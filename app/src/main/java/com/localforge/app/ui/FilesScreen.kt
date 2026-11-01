package com.localforge.app.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Save
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.data.formatBytes
import java.io.File
import android.content.Context
import android.content.Intent
import android.webkit.MimeTypeMap
import androidx.core.content.FileProvider
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private enum class Prompt { NEW_FILE, NEW_FOLDER, RENAME }

@Composable
fun FilesScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    if (!vm.hasFileAccess) {
        Column(Modifier.fillMaxSize().padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            ScreenTitle("Files")
            Panel(borderColor = C.Accent) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Lock, null, tint = C.Accent)
                    Spacer(Modifier.width(10.dp))
                    Text("All-files access needed", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "To browse, create, edit and delete files — and let the assistant do the same with your approval — " +
                        "turn on \"Allow access to manage all files\" for LocalForge.",
                    color = C.Muted
                )
                Spacer(Modifier.height(12.dp))
                Button(onClick = { openAllFilesSettings(ctx) }, colors = ButtonDefaults.buttonColors(containerColor = C.Accent)) {
                    Text("Open settings")
                }
            }
        }
        return
    }

    BackHandler(enabled = vm.currentDir != android.os.Environment.getExternalStorageDirectory()) { vm.goUp() }

    var prompt by remember { mutableStateOf<Prompt?>(null) }
    var target by remember { mutableStateOf<File?>(null) }
    var confirmDelete by remember { mutableStateOf<File?>(null) }
    val dateFmt = remember { SimpleDateFormat("d MMM yyyy, HH:mm", Locale.getDefault()) }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 20.dp, end = 8.dp, top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            ScreenTitle("Files", Modifier.weight(1f))
            IconButton(onClick = { prompt = Prompt.NEW_FILE }) { Icon(Icons.Filled.Add, "New file", tint = C.Accent) }
            IconButton(onClick = { prompt = Prompt.NEW_FOLDER }) { Icon(Icons.Filled.CreateNewFolder, "New folder", tint = C.Accent) }
            IconButton(onClick = { vm.refreshFiles() }) { Icon(Icons.Filled.Refresh, "Refresh", tint = C.Muted) }
        }
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            IconButton(onClick = { vm.goUp() }) { Icon(Icons.Filled.ArrowUpward, "Up", tint = C.Text) }
            Text(
                vm.currentDir.absolutePath.removePrefix(android.os.Environment.getExternalStorageDirectory().absolutePath).ifEmpty { "/" }
                    .let { "Phone storage$it" },
                color = C.Muted, fontFamily = FontFamily.Monospace, fontSize = 13.sp,
                modifier = Modifier.horizontalScroll(rememberScrollState())
            )
        }
        vm.fileMessage?.let {
            Text(it, color = C.Gold, fontSize = 13.sp, modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp))
        }

        LazyColumn(Modifier.weight(1f).padding(horizontal = 14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (vm.entries.isEmpty()) item { Text("This folder is empty", color = C.Muted, modifier = Modifier.padding(16.dp)) }
            items(vm.entries, key = { it.absolutePath }) { f ->
                var menu by remember { mutableStateOf(false) }
                Row(
                    Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(16.dp))
                        .background(C.Card)
                        .clickable {
                            when {
                                f.isDirectory -> vm.openDir(f)
                                isTextFile(f) -> vm.openEditor(f)
                                else -> openWithOtherApp(ctx, f) { vm.fileMessage = it }
                            }
                        }
                        .padding(start = 14.dp, top = 10.dp, bottom = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(
                        if (f.isDirectory) Icons.Filled.Folder else Icons.Filled.Description,
                        null,
                        tint = if (f.isDirectory) C.Accent else C.Muted,
                        modifier = Modifier.size(28.dp)
                    )
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(f.name, color = C.Text, fontSize = 16.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text(
                            (if (f.isDirectory) "Folder" else formatBytes(f.length())) + " · " + dateFmt.format(Date(f.lastModified())),
                            color = C.Muted, fontSize = 12.sp
                        )
                    }
                    Box {
                        IconButton(onClick = { menu = true }) { Icon(Icons.Filled.MoreVert, "Options", tint = C.Muted) }
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            if (f.isFile) {
                                DropdownMenuItem(
                                    text = { Text("Ask AI about this file") },
                                    leadingIcon = { Icon(Icons.Filled.AutoAwesome, null) },
                                    onClick = { menu = false; vm.attachFile(f) }
                                )
                                DropdownMenuItem(
                                    text = { Text("Open with…") },
                                    leadingIcon = { Icon(Icons.Filled.OpenInNew, null) },
                                    onClick = { menu = false; openWithOtherApp(ctx, f) { vm.fileMessage = it } }
                                )
                                DropdownMenuItem(
                                    text = { Text("Edit") },
                                    leadingIcon = { Icon(Icons.Filled.Edit, null) },
                                    onClick = { menu = false; vm.openEditor(f) }
                                )
                            }
                            DropdownMenuItem(
                                text = { Text("Rename") },
                                leadingIcon = { Icon(Icons.Filled.Description, null) },
                                onClick = { menu = false; target = f; prompt = Prompt.RENAME }
                            )
                            DropdownMenuItem(
                                text = { Text("Delete", color = C.Bad) },
                                leadingIcon = { Icon(Icons.Filled.Delete, null, tint = C.Bad) },
                                onClick = { menu = false; confirmDelete = f }
                            )
                        }
                    }
                }
            }
        }
    }

    prompt?.let { p ->
        var name by remember(p, target) { mutableStateOf(if (p == Prompt.RENAME) target?.name ?: "" else "") }
        AlertDialog(
            onDismissRequest = { prompt = null },
            title = {
                Text(
                    when (p) {
                        Prompt.NEW_FILE -> "New file"
                        Prompt.NEW_FOLDER -> "New folder"
                        Prompt.RENAME -> "Rename"
                    }
                )
            },
            text = { OutlinedTextField(value = name, onValueChange = { name = it }, singleLine = true, label = { Text("Name") }) },
            confirmButton = {
                TextButton(enabled = name.isNotBlank() && !name.contains('/'), onClick = {
                    when (p) {
                        Prompt.NEW_FILE -> vm.createFile(name)
                        Prompt.NEW_FOLDER -> vm.createFolder(name)
                        Prompt.RENAME -> target?.let { vm.rename(it, name) }
                    }
                    prompt = null
                }) { Text("OK") }
            },
            dismissButton = { TextButton(onClick = { prompt = null }) { Text("Cancel") } }
        )
    }

    confirmDelete?.let { f ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Delete ${if (f.isDirectory) "folder" else "file"}?") },
            text = { Text("\"${f.name}\"${if (f.isDirectory) " and everything inside it" else ""} will be permanently deleted.") },
            confirmButton = { TextButton(onClick = { vm.delete(f); confirmDelete = null }) { Text("Delete", color = C.Bad) } },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Cancel") } }
        )
    }
}

@Composable
fun EditorScreen(vm: AppViewModel, file: File) {
    BackHandler { vm.closeEditor() }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { vm.closeEditor() }) { Icon(Icons.Filled.ArrowBack, "Back", tint = C.Text) }
            Column(Modifier.weight(1f)) {
                Text(file.name, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(vm.fileMessage ?: file.parent ?: "", color = C.Muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            if (!vm.editorReadOnly) {
                IconButton(onClick = { vm.attachFile(file); vm.closeEditor() }) { Icon(Icons.Filled.AutoAwesome, "Ask AI", tint = C.Accent) }
                IconButton(onClick = { vm.saveEditor() }) { Icon(Icons.Filled.Save, "Save", tint = C.Accent) }
            }
        }
        Box(
            Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 4.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(C.Card)
                .verticalScroll(rememberScrollState())
                .horizontalScroll(rememberScrollState())
                .padding(12.dp)
        ) {
            BasicTextField(
                value = vm.editorText,
                onValueChange = { vm.editorText = it },
                readOnly = vm.editorReadOnly,
                textStyle = TextStyle(color = C.Text, fontFamily = FontFamily.Monospace, fontSize = 13.sp, lineHeight = 19.sp),
                cursorBrush = SolidColor(C.Accent)
            )
        }
    }
}

private val textExtensions = setOf(
    "txt", "md", "log", "csv", "json", "xml", "yml", "yaml", "ini", "cfg", "conf", "properties", "toml",
    "py", "js", "mjs", "ts", "tsx", "jsx", "kt", "kts", "java", "c", "h", "cpp", "hpp", "cc", "cs", "go", "rs",
    "rb", "php", "lua", "pl", "sh", "bash", "sql", "html", "htm", "css", "scss", "gradle", "bat", "srt", "tex"
)

fun isTextFile(f: File): Boolean {
    val ext = f.extension.lowercase()
    return ext in textExtensions || (ext.isEmpty() && f.length() < 256 * 1024)
}

/** Opens any file (photo, PDF, video, APK, …) in the phone's usual app for it. */
fun openWithOtherApp(ctx: Context, f: File, onError: (String) -> Unit) {
    try {
        val uri = FileProvider.getUriForFile(ctx, ctx.packageName + ".files", f)
        val mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(f.extension.lowercase()) ?: "*/*"
        val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        ctx.startActivity(Intent.createChooser(intent, "Open ${f.name}").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (e: Exception) {
        onError("No app can open ${f.name}: ${e.message}")
    }
}
