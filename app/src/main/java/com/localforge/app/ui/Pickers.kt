package com.localforge.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.localforge.app.agent.PickRequest
import com.localforge.app.agent.WebBrowser
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.temporal.IsoFields

/**
 * Dropdowns and date/time/colour fields tapped on a web page. The phone's own pickers often don't open inside
 * an app's browser, so LocalForge draws them and sets the chosen value on the page.
 */
@Composable
fun PickerDialog(req: PickRequest, onDone: () -> Unit) {
    val scope = rememberCoroutineScope()
    val set: (String) -> Unit = { v -> scope.launch { WebBrowser.setPickedValue(v) }; onDone() }
    when {
        req.kind == "select" -> SelectDialog(req, onDone) { i -> scope.launch { WebBrowser.chooseOption(i) }; onDone() }
        req.type == "color" -> ColorDialog(req.value, onDone, set)
        req.type == "time" -> TimeDialog(req.value, onDone, set)
        req.type == "datetime-local" -> DateTimeDialog(req.value, onDone, set)
        else -> DateDialog(req.type, req.value, onDone, set)   // date, month, week
    }
}

@Composable
private fun SelectDialog(req: PickRequest, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    val arr = req.json.optJSONArray("options")
    val options = (0 until (arr?.length() ?: 0)).map { arr!!.getJSONObject(it) }
    val selected = req.json.optInt("selected", -1)
    val listState = rememberLazyListState()
    LaunchedEffect(Unit) { if (selected > 2) listState.scrollToItem(selected - 2) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(req.json.optString("label").ifBlank { "Choose" }) },
        text = {
            LazyColumn(state = listState, modifier = Modifier.heightIn(max = 440.dp)) {
                itemsIndexed(options) { i, o ->
                    val group = o.optString("group")
                    if (group.isNotBlank() && (i == 0 || options[i - 1].optString("group") != group)) {
                        Text(group, color = C.Muted, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 8.dp, bottom = 2.dp))
                    }
                    val disabled = o.optBoolean("disabled")
                    Text(
                        o.optString("text").ifBlank { " " },
                        color = if (disabled) C.Border else if (i == selected) C.Accent else C.Text,
                        fontWeight = if (i == selected) FontWeight.Bold else FontWeight.Normal,
                        fontSize = 16.sp,
                        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp))
                            .background(if (i == selected) C.AccentDark else Color.Transparent)
                            .clickable(enabled = !disabled) { onPick(i) }
                            .padding(horizontal = 12.dp, vertical = 12.dp)
                    )
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

private fun parseDate(type: String, v: String): LocalDate? = try {
    when (type) {
        "month" -> LocalDate.parse("$v-01")
        "week" -> {
            val (y, w) = v.split("-W").map { it.toInt() }
            LocalDate.of(y, 1, 4).with(IsoFields.WEEK_OF_WEEK_BASED_YEAR, w.toLong()).with(java.time.DayOfWeek.MONDAY)
        }
        else -> LocalDate.parse(v.take(10))
    }
} catch (_: Exception) { null }

private fun formatDate(type: String, d: LocalDate): String = when (type) {
    "month" -> "%04d-%02d".format(d.year, d.monthValue)
    "week" -> "%04d-W%02d".format(d.get(IsoFields.WEEK_BASED_YEAR), d.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR))
    else -> d.toString()
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DateDialog(type: String, value: String, onDismiss: () -> Unit, onSet: (String) -> Unit) {
    val start = parseDate(type, value)
    val state = rememberDatePickerState(initialSelectedDateMillis = start?.atStartOfDay(ZoneOffset.UTC)?.toInstant()?.toEpochMilli())
    DatePickerDialog(
        onDismissRequest = onDismiss,
        confirmButton = {
            TextButton(onClick = {
                val ms = state.selectedDateMillis ?: return@TextButton onDismiss()
                onSet(formatDate(type, Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC).toLocalDate()))
            }) { Text("OK") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    ) { DatePicker(state = state) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TimeDialog(value: String, onDismiss: () -> Unit, onSet: (String) -> Unit) {
    val parts = value.split(":")
    val state = rememberTimePickerState(parts.getOrNull(0)?.toIntOrNull() ?: 12, parts.getOrNull(1)?.take(2)?.toIntOrNull() ?: 0, is24Hour = true)
    AlertDialog(
        onDismissRequest = onDismiss,
        text = { TimePicker(state = state) },
        confirmButton = { TextButton(onClick = { onSet("%02d:%02d".format(state.hour, state.minute)) }) { Text("OK") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun DateTimeDialog(value: String, onDismiss: () -> Unit, onSet: (String) -> Unit) {
    var date by remember { mutableStateOf<String?>(null) }
    val d = date
    if (d == null) DateDialog("date", value.take(10), onDismiss) { date = it }
    else TimeDialog(value.substringAfter('T', ""), onDismiss) { onSet("${d}T$it") }
}

private val palette = listOf(
    "#000000", "#444444", "#888888", "#cccccc", "#ffffff", "#ff1744", "#ff9100", "#ffea00",
    "#00e676", "#00b0ff", "#2979ff", "#651fff", "#d500f9", "#f50057", "#795548", "#1b5e20",
    "#0d47a1", "#b71c1c", "#ff7a28", "#42a5f5"
)

@Composable
private fun ColorDialog(value: String, onDismiss: () -> Unit, onSet: (String) -> Unit) {
    var hex by remember { mutableStateOf(value.ifBlank { "#000000" }) }
    val valid = Regex("^#[0-9a-fA-F]{6}$").matches(hex)
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Choose a colour") },
        text = {
            Column {
                palette.chunked(5).forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.padding(vertical = 5.dp)) {
                        row.forEach { c ->
                            Box(
                                Modifier.size(40.dp).clip(CircleShape).background(Color(android.graphics.Color.parseColor(c)))
                                    .border(if (c.equals(hex, true)) 3.dp else 1.dp, if (c.equals(hex, true)) C.Accent else C.Border, CircleShape)
                                    .clickable { hex = c }
                            )
                        }
                    }
                }
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(value = hex, onValueChange = { hex = it.trim() }, singleLine = true, label = { Text("Hex") },
                    isError = !valid, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = { TextButton(onClick = { onSet(hex.lowercase()) }, enabled = valid) { Text("OK") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
