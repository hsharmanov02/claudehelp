package com.localforge.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import com.localforge.app.engine.HordeEngine
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.AppViewModel
import com.localforge.app.engine.Engine
import com.localforge.app.engine.RemoteEngine

/**
 * "Remote engine": your own abliterated model on a rented GPU (remote/modal_engine.py), for phones too slow for
 * big models. Paste the address and your key once; every chat (and the Autopilot) then uses it.
 */
@Composable
fun RemoteEngineCard(vm: AppViewModel) {
    var open by remember { mutableStateOf(vm.useRemote) }
    var url by remember { mutableStateOf(vm.remoteUrl) }
    var key by remember { mutableStateOf(vm.remoteKey) }
    var showKey by remember { mutableStateOf(false) }
    val on = vm.useRemote && Engine.isRemote
    Panel(borderColor = if (on) C.Good else C.Border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { StatusDot(on, if (on) "Remote GPU engine on" else "Remote GPU engine") }
            if (on) Tag("☁ GPU", C.CardInner, C.Good) else TextButton(onClick = { open = !open }) {
                Text(if (open) "Hide" else "Set up", color = C.Accent)
            }
        }
        Text(
            if (on) "${RemoteEngine.model} · ${RemoteEngine.ctx} tokens · " +
                (if (RemoteEngine.lastTokensPerSecond > 0) "%.0f tokens/s".format(RemoteEngine.lastTokensPerSecond) else "sleeps when idle")
            else "Run a big uncensored model on your own rented GPU (Modal) instead of this phone. Much faster replies.",
            color = C.Muted, fontSize = 13.sp, fontFamily = if (on) FontFamily.Monospace else FontFamily.Default
        )
        if (on && vm.loading) vm.engineMessage?.let { Text(it, color = C.Gold, fontSize = 13.sp) }
        if (open || on) {
            Spacer(Modifier.height(10.dp))
            OutlinedTextField(
                value = url, onValueChange = { url = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                label = { Text("Address (https://…modal.run)") }, enabled = !on && !vm.remoteBusy
            )
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                value = key, onValueChange = { key = it }, singleLine = true, modifier = Modifier.fillMaxWidth(),
                label = { Text("Your key (the password you chose)") }, enabled = !on && !vm.remoteBusy,
                visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
                trailingIcon = { TextButton(onClick = { showKey = !showKey }) { Text(if (showKey) "Hide" else "Show", color = C.Muted) } }
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (on) {
                    OutlinedButton(onClick = { vm.stopGenerating(); vm.disconnectRemote() }) {
                        Text("Use this phone instead", color = C.Bad)
                    }
                } else {
                    Button(
                        onClick = { vm.connectRemote(url, key) },
                        enabled = !vm.remoteBusy && url.isNotBlank() && key.isNotBlank(),
                        colors = ButtonDefaults.buttonColors(containerColor = C.Accent)
                    ) {
                        if (vm.remoteBusy) {
                            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = C.Bg)
                            Spacer(Modifier.width(8.dp))
                            Text("Connecting…")
                        } else Text("Connect", fontWeight = FontWeight.Bold)
                    }
                }
            }
            if (!on) {
                vm.engineMessage?.takeIf { vm.remoteBusy || it.contains("remote", true) || it.contains("key", true) }?.let {
                    Spacer(Modifier.height(6.dp)); Text(it, color = C.Muted, fontSize = 13.sp)
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    "Deploy it with the \"Deploy Modal engine\" workflow in your GitHub repo. The first message after a quiet " +
                        "spell wakes the GPU (about a minute). Modal bills only while it's awake.",
                    color = C.Muted, fontSize = 12.sp
                )
            }
        }
    }
}

/**
 * "AI Horde": free volunteer GPUs with no account. Lists the models online now (uncensored first); tap one to use it.
 */
@Composable
fun HordeEngineCard(vm: AppViewModel) {
    var open by remember { mutableStateOf(vm.useHorde) }
    val on = vm.useHorde && Engine.isHorde
    Panel(borderColor = if (on) C.Good else C.Border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { StatusDot(on, if (on) "AI Horde on (free)" else "AI Horde: free, no account") }
            if (on) Tag("🌐 Horde", C.CardInner, C.Good) else TextButton(onClick = {
                open = !open
                if (open && vm.hordeModels.isEmpty()) vm.fetchHordeModels()
            }) { Text(if (open) "Hide" else "Show models", color = C.Accent) }
        }
        Text(
            if (on) "${HordeEngine.model.substringAfterLast('/')} · ${HordeEngine.ctx} tokens · replies up to ${HordeEngine.maxReply} tokens"
            else "Big uncensored models on volunteers' GPUs. Nothing to sign up for. Not private: the volunteer running the model could read your messages.",
            color = C.Muted, fontSize = 13.sp, fontFamily = if (on) FontFamily.Monospace else FontFamily.Default
        )
        vm.hordeMessage?.let { Text(it, color = C.Gold, fontSize = 13.sp) }
        if (open || on) {
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { open = true; vm.fetchHordeModels() }, enabled = !vm.hordeBusy) {
                    Text(if (vm.hordeBusy) "Checking…" else if (on) "Switch model" else "Refresh")
                }
                if (on) OutlinedButton(onClick = { vm.stopGenerating(); vm.stopHorde() }) { Text("Stop using Horde", color = C.Bad) }
            }
            vm.hordeModels.take(14).forEach { m ->
                val current = on && HordeEngine.model == m.name
                Row(
                    Modifier.fillMaxWidth().padding(top = 6.dp)
                        .clip(RoundedCornerShape(14.dp)).background(if (current) C.AccentDark else C.CardInner)
                        .clickable(enabled = !vm.hordeBusy) { vm.useHordeModel(m, vm.hordeKey) }
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(Modifier.weight(1f)) {
                        Text(m.short, color = C.Text, fontWeight = FontWeight.SemiBold, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Text("${"%.0f".format(m.billions)}B · ${m.workers} GPU${if (m.workers == 1) "" else "s"} · wait ~${m.eta}s · ${m.ctx / 1024}k context",
                            color = C.Muted, fontSize = 12.sp)
                    }
                    Tag(if (m.name.lowercase().let { "heretic" in it || "abliterat" in it }) "abliterated" else "uncensored", C.Bg, C.Good)
                }
            }
        }
    }
}
