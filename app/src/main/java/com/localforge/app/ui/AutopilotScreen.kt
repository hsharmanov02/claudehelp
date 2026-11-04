package com.localforge.app.ui

import android.app.AlarmManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
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
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.Tab
import com.localforge.app.autopilot.Autopilot
import com.localforge.app.autopilot.AutopilotLogic
import com.localforge.app.autopilot.AutopilotService
import com.localforge.app.data.formatBytes
import kotlinx.coroutines.delay

private val TermBg = Color(0xFF050806)
private val TermGreen = Color(0xFF3DDC84)
private val TermDim = Color(0xFF7FAF8F)
private val StopRed = Color(0xFFE53935)

private fun pauseLabel(s: Int) = when {
    s == 0 -> "none"
    s < 60 -> "${s}s"
    s < 3600 -> "${s / 60} min"
    else -> "${s / 3600} h"
}

/** LocalForge Autopilot's main screen: the main command, a terminal-style status and the STOP button. */
@Composable
fun AutopilotScreen(vm: AppViewModel) {
    val ctx = LocalContext.current
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) { while (true) { now = System.currentTimeMillis(); delay(1000) } }

    var draft by remember { mutableStateOf(Autopilot.goal) }
    var model by remember { mutableStateOf(Autopilot.modelName ?: vm.loadedModel ?: vm.lastModelName) }
    var pause by remember { mutableStateOf(Autopilot.pauseSec) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmStop by remember { mutableStateOf(false) }
    var confirmNew by remember { mutableStateOf(false) }
    var modelMenu by remember { mutableStateOf(false) }
    val running = Autopilot.enabled && !Autopilot.awaitingConsent

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp)
    ) {
        ScreenTitle("Autopilot")
        Text("One main command, worked on 24/7 until you press STOP.", color = C.Muted, fontSize = 15.sp)

        // ---- terminal ----
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(18.dp)).background(TermBg)
                .border(1.5.dp, if (running) TermGreen else C.Border, RoundedCornerShape(18.dp)).padding(14.dp)
        ) {
            val mono = FontFamily.Monospace
            val state = when {
                Autopilot.awaitingConsent -> "WAITING FOR YOU"
                running && Autopilot.inRound -> "RUNNING"
                running -> "ON"
                else -> "OFF"
            }
            Text("$ autopilot --status", color = TermDim, fontFamily = mono, fontSize = 13.sp)
            Text("[$state] ${Autopilot.status}", color = if (running) TermGreen else C.Text, fontFamily = mono, fontSize = 14.sp)
            if (Autopilot.goal.isNotBlank()) Text("task: ${Autopilot.goal}", color = C.Text, fontFamily = mono, fontSize = 13.sp)
            Text(
                "round ${Autopilot.round} · model ${(vm.loadedModel ?: Autopilot.modelName ?: "-").removeSuffix(".gguf").take(32)}" +
                    " · restarts ${Autopilot.restarts} · errors ${Autopilot.errors}" +
                    (if (running && !Autopilot.inRound && Autopilot.nextAt > now) " · next in ${AutopilotLogic.dur((Autopilot.nextAt - now) / 1000)}" else "") +
                    (if (running && !AutopilotService.running) " · service restarting…" else ""),
                color = TermDim, fontFamily = mono, fontSize = 12.sp
            )
            val tail = Autopilot.journal.takeLast(6)
            if (tail.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                tail.forEach { j ->
                    Text("${j.time.takeLast(5)} #${j.round} ${j.summary}", color = TermDim, fontFamily = mono, fontSize = 12.sp,
                        maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
            Spacer(Modifier.height(12.dp))
            if (Autopilot.enabled || Autopilot.awaitingConsent) {
                Button(
                    onClick = { confirmStop = true },
                    modifier = Modifier.fillMaxWidth().height(60.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = StopRed)
                ) { Text("■  STOP", fontSize = 20.sp, fontWeight = FontWeight.Bold, fontFamily = mono, color = Color.White) }
                if (running) {
                    Spacer(Modifier.height(6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { Autopilot.runNow() }, enabled = !Autopilot.inRound) { Text("Run a round now", color = TermGreen) }
                        OutlinedButton(onClick = { vm.tab = Tab.CODE }) { Text("Watch chat", color = C.Text) }
                        OutlinedButton(onClick = { vm.tab = Tab.BROWSER }) { Text("Browser", color = C.Text) }
                    }
                }
            }
        }

        // ---- task ----
        Panel {
            Text(if (running) "Main command" else "Main command (what it should keep doing)", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = if (running) Autopilot.goal else draft,
                onValueChange = { draft = it },
                enabled = !running,
                placeholder = { Text("e.g. Every hour, check example.com/deals for laptops under £500 and keep a list of the best ones") },
                modifier = Modifier.fillMaxWidth().heightIn(min = 110.dp)
            )
            Spacer(Modifier.height(10.dp))
            Text("Model (the same files as LocalForge)", color = C.Muted, fontSize = 13.sp)
            Box {
                OutlinedButton(onClick = { vm.refreshModels(); modelMenu = true }, enabled = !running, modifier = Modifier.fillMaxWidth()) {
                    Text((model ?: "Pick a model").removeSuffix(".gguf"), maxLines = 1, overflow = TextOverflow.Ellipsis,
                        color = C.Text, modifier = Modifier.weight(1f))
                    Icon(Icons.Filled.KeyboardArrowDown, null, tint = C.Text)
                }
                DropdownMenu(expanded = modelMenu, onDismissRequest = { modelMenu = false }) {
                    if (vm.localModels.isEmpty()) DropdownMenuItem(text = { Text("No models yet: open Models") }, onClick = { modelMenu = false; vm.tab = Tab.MODELS })
                    vm.localModels.forEach { m ->
                        DropdownMenuItem(text = { Text("${m.name}  (${formatBytes(m.size)})", maxLines = 2) },
                            onClick = { model = m.name; modelMenu = false; Autopilot.setModel(m.name) })
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            Text("Pause between rounds", color = C.Muted, fontSize = 13.sp)
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                AutopilotLogic.PAUSES.forEach { p ->
                    FilterChip(selected = pause == p, onClick = { pause = p; Autopilot.setPause(p) },
                        label = { Text(pauseLabel(p), fontSize = 11.sp) })
                }
            }
            error?.let { Text(it, color = C.Bad, fontSize = 14.sp) }
            if (!running) {
                Spacer(Modifier.height(10.dp))
                val canContinue = Autopilot.goal.isNotBlank() && draft.trim() == Autopilot.goal
                if (canContinue) {
                    Button(
                        onClick = { error = Autopilot.continueTask(model, pause) },
                        modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = C.Good)
                    ) { Text("▶  Continue task (round ${Autopilot.round + 1})", fontWeight = FontWeight.Bold, color = Color.Black) }
                    Spacer(Modifier.height(6.dp))
                }
                Button(
                    onClick = {
                        if (Autopilot.goal.isNotBlank() && (Autopilot.memory.isNotEmpty() || Autopilot.journal.isNotEmpty())) confirmNew = true
                        else error = Autopilot.newTask(draft, model, pause)
                    },
                    modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = if (canContinue) C.CardInner else C.Accent)
                ) { Text("+  Start as a new 24/7 task", fontWeight = FontWeight.Bold) }
            }
        }

        // ---- unattended permissions ----
        Panel {
            Text("Let it run unattended", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            ToggleRow("AI can click and type in the browser without asking", vm.browserAutoApprove) { vm.updateBrowserAutoApprove(it) }
            ToggleRow("AI can create and edit files without asking (deletes always ask)", vm.autoApprove) { vm.updateAutoApprove(it) }
            Spacer(Modifier.height(6.dp))
            SetupChecklist(vm, ctx)
        }

        // ---- memory + journal ----
        Panel {
            Text("Memory (${Autopilot.memory.size})", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            Text("What the AI saved with <note> for later rounds. It survives restarts.", color = C.Muted, fontSize = 13.sp)
            if (Autopilot.memory.isEmpty()) Text("Nothing yet.", color = C.Muted)
            Autopilot.memory.toList().asReversed().forEachIndexed { revIndex, n ->
                val index = Autopilot.memory.size - 1 - revIndex
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("• ${n.text}", color = C.Text, fontSize = 14.sp, modifier = Modifier.weight(1f))
                    Icon(Icons.Filled.Close, "Forget", tint = C.Muted, modifier = Modifier.size(20.dp).clickable { Autopilot.forget(index) })
                }
            }
        }
        Panel {
            Text("Journal", color = C.Text, fontWeight = FontWeight.Bold, fontSize = 18.sp)
            if (Autopilot.journal.isEmpty()) Text("No rounds yet.", color = C.Muted)
            Autopilot.journal.takeLast(40).asReversed().forEach { j ->
                Text("${j.time}  round ${j.round}", color = C.Muted, fontSize = 12.sp, fontFamily = FontFamily.Monospace)
                Text(j.summary, color = C.Text, fontSize = 14.sp)
                Spacer(Modifier.height(4.dp))
            }
        }
        Spacer(Modifier.height(8.dp))
    }

    if (confirmStop) {
        AlertDialog(
            onDismissRequest = { confirmStop = false },
            title = { Text("Stop the autopilot?") },
            text = { Text("The task stops and the model is unloaded. Its memory is kept, so you can press Continue later.") },
            confirmButton = { TextButton(onClick = { confirmStop = false; Autopilot.stop(); draft = Autopilot.goal }) { Text("STOP", color = StopRed, fontWeight = FontWeight.Bold) } },
            dismissButton = { TextButton(onClick = { confirmStop = false }) { Text("Keep running") } }
        )
    }
    if (confirmNew) {
        AlertDialog(
            onDismissRequest = { confirmNew = false },
            title = { Text("Start a new task?") },
            text = { Text("The current task's memory and journal are archived and the new task starts with an empty memory.") },
            confirmButton = { TextButton(onClick = { confirmNew = false; error = Autopilot.newTask(draft, model, pause) }) { Text("Start new task") } },
            dismissButton = { TextButton(onClick = { confirmNew = false }) { Text("Cancel") } }
        )
    }
}

/** Shown over any screen after the phone restarted while a task was running. Nothing loads until the user answers. */
@Composable
fun ConsentDialog() {
    AlertDialog(
        onDismissRequest = {},
        title = { Text("Continue the autopilot?") },
        text = {
            Text("Your phone restarted while this task was running:\n\n${Autopilot.goal}\n\nLoad the model and continue? " +
                "If you choose Not now, nothing runs until you press Continue.")
        },
        confirmButton = { TextButton(onClick = { Autopilot.consent(true) }) { Text("Load model & continue", fontWeight = FontWeight.Bold) } },
        dismissButton = { TextButton(onClick = { Autopilot.consent(false) }) { Text("Not now") } }
    )
}

@Composable
private fun ToggleRow(label: String, on: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, color = C.Text, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        Switch(checked = on, onCheckedChange = onChange)
    }
}

@Composable
private fun SetupChecklist(vm: AppViewModel, ctx: Context) {
    val pm = ctx.getSystemService(Context.POWER_SERVICE) as PowerManager
    val batteryOk = pm.isIgnoringBatteryOptimizations(ctx.packageName)
    val am = ctx.getSystemService(AlarmManager::class.java)
    val alarmsOk = Build.VERSION.SDK_INT < 31 || am?.canScheduleExactAlarms() == true
    fun open(i: Intent) { try { ctx.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (_: Exception) {} }
    Check("All-files access", "Finds the models LocalForge downloaded (LocalForge/models)", vm.hasFileAccess) { openAllFilesSettings(ctx) }
    Check("No battery limits", "So Android doesn't pause it at night", batteryOk) {
        open(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}")))
    }
    Check("Alarms & reminders", "Lets the watchdog wake it if Android stops it", alarmsOk) {
        if (Build.VERSION.SDK_INT >= 31) open(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")))
    }
    Text("On Xiaomi, Oppo, Vivo, OnePlus, Huawei and Samsung phones also allow Autostart / \"Unrestricted\" battery for " +
        "LocalForge Autopilot in the phone's settings, or it can't come back after a restart.", color = C.Muted, fontSize = 12.sp)
}

@Composable
private fun Check(title: String, subtitle: String, done: Boolean, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().let { if (!done) it.clickable(onClick = onClick) else it }.padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(if (done) Icons.Filled.CheckCircle else Icons.Filled.RadioButtonUnchecked, null,
            tint = if (done) C.Good else C.Muted, modifier = Modifier.size(24.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 15.sp)
            Text(subtitle, color = C.Muted, fontSize = 12.sp)
        }
        if (!done) Text("Open", color = C.Accent, fontWeight = FontWeight.SemiBold)
    }
}
