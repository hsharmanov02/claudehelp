package com.localforge.app.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

object C {
    val Bg = Color(0xFF121315)
    val Card = Color(0xFF17181B)
    val CardInner = Color(0xFF222326)
    val Border = Color(0xFF3A3B3F)
    val Accent = Color(0xFF42A5F5)
    val AccentDark = Color(0xFF173A5C)
    val Text = Color(0xFFF2F2F2)
    val Muted = Color(0xFFA9AAAE)
    val Good = Color(0xFF3DDC84)
    val Bad = Color(0xFFFF6B6B)
    val Gold = Color(0xFFD9B44A)
}

@Composable
fun LocalForgeTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkColorScheme(
            primary = C.Accent,
            onPrimary = Color(0xFF0B1A2A),
            background = C.Bg,
            surface = C.Card,
            surfaceVariant = C.CardInner,
            onSurface = C.Text,
            onSurfaceVariant = C.Muted,
            outline = C.Border,
            error = C.Bad
        ),
        content = content
    )
}

/** Big serif-italic screen title, like "Providers" in the screenshots. */
@Composable
fun ScreenTitle(text: String, modifier: Modifier = Modifier) {
    Text(
        text,
        modifier = modifier,
        color = C.Text,
        fontSize = 30.sp,
        fontFamily = FontFamily.Serif,
        fontStyle = FontStyle.Italic,
        fontWeight = FontWeight.Bold
    )
}

@Composable
fun Panel(
    modifier: Modifier = Modifier,
    borderColor: Color = C.Border,
    radius: Dp = 28.dp,
    content: @Composable ColumnScope.() -> Unit
) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(radius))
            .background(C.Card)
            .border(1.5.dp, borderColor, RoundedCornerShape(radius))
            .padding(20.dp),
        content = content
    )
}

@Composable
fun SectionHeader(title: String, subtitle: String? = null) {
    Column(Modifier.padding(top = 8.dp, bottom = 6.dp)) {
        Text(title, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
        if (subtitle != null) Text(subtitle, color = C.Muted, fontSize = 14.sp)
    }
}

@Composable
fun StatusDot(on: Boolean, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Spacer(
            Modifier
                .size(12.dp)
                .clip(CircleShape)
                .background(if (on) C.Good else Color(0xFF8A8B8F))
        )
        Spacer(Modifier.width(10.dp))
        Text(label, color = C.Text, fontWeight = FontWeight.Bold, fontSize = 20.sp)
    }
}

@Composable
fun Tag(text: String, color: Color = C.CardInner, textColor: Color = C.Text) {
    Text(
        text,
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(color)
            .padding(horizontal = 10.dp, vertical = 4.dp),
        color = textColor,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold
    )
}
