package com.localforge.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.border
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.ViewList
import androidx.compose.material3.Icon
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.localforge.app.ui.C
import com.localforge.app.ui.CodeScreen
import com.localforge.app.ui.EditorScreen
import com.localforge.app.ui.FilesScreen
import com.localforge.app.ui.HomeScreen
import com.localforge.app.ui.LocalForgeTheme
import com.localforge.app.ui.ModelsScreen

class MainActivity : ComponentActivity() {
    private val vm: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            LocalForgeTheme {
                App(vm)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        vm.refreshPermission()
        vm.refreshModels()
    }
}

@Composable
fun App(vm: AppViewModel) {
    val view = LocalView.current
    val downloading = vm.downloads.values.any { it.error == null }
    SideEffect { view.keepScreenOn = downloading }
    Box(
        Modifier
            .fillMaxSize()
            .background(C.Bg)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding()
    ) {
        val editing = vm.editingFile
        if (editing != null) {
            EditorScreen(vm, editing)
        } else Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) {
                when (vm.tab) {
                    Tab.HOME -> HomeScreen(vm)
                    Tab.MODELS -> ModelsScreen(vm)
                    Tab.CODE -> CodeScreen(vm)
                    Tab.FILES -> FilesScreen(vm)
                }
            }
            BottomBar(vm.tab) { vm.tab = it }
        }
    }
}

@Composable
private fun BottomBar(current: Tab, onSelect: (Tab) -> Unit) {
    val items = listOf(
        Tab.HOME to Icons.Filled.Home,
        Tab.MODELS to Icons.Filled.ViewList,
        Tab.CODE to Icons.Filled.Code,
        Tab.FILES to Icons.Filled.Folder
    )
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .clip(RoundedCornerShape(50))
            .background(C.Card)
            .border(1.5.dp, C.Border, RoundedCornerShape(50))
            .padding(8.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically
    ) {
        items.forEach { (tab, icon) -> NavItem(icon, tab == current) { onSelect(tab) } }
    }
}

@Composable
private fun NavItem(icon: ImageVector, selected: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .size(60.dp)
            .clip(CircleShape)
            .background(if (selected) Color.White else Color.Transparent)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, contentDescription = null, tint = if (selected) Color.Black else Color.White, modifier = Modifier.height(28.dp).size(28.dp))
    }
}
