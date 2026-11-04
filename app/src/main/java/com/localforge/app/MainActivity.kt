package com.localforge.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
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
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.SmartToy
import android.content.Intent
import com.localforge.app.autopilot.Autopilot
import com.localforge.app.autopilot.AutopilotNotify
import com.localforge.app.autopilot.AutopilotService
import com.localforge.app.ui.AutopilotScreen
import com.localforge.app.ui.ConsentDialog
import com.localforge.app.agent.WebBrowser
import com.localforge.app.ui.BrowserScreen
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
import com.localforge.app.ui.HtmlPreviewScreen
import com.localforge.app.ui.LocalForgeTheme
import com.localforge.app.ui.ModelsScreen

class MainActivity : ComponentActivity() {
    private val app get() = application as LocalForgeApp
    private val vm: AppViewModel get() = app.state

    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        WebBrowser.init(this)
        WebBrowser.attachActivity(this)
        // Needed to show "working in background" / "reply ready" notifications on Android 13+.
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (BuildConfig.AUTOPILOT) Autopilot.init(this)
        openAutopilotIfAsked(intent)
        setContent {
            LocalForgeTheme {
                App(vm)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        openAutopilotIfAsked(intent)
    }

    private fun openAutopilotIfAsked(intent: Intent?) {
        if (BuildConfig.AUTOPILOT && intent?.getBooleanExtra(AutopilotNotify.EXTRA_AUTOPILOT, false) == true) vm.tab = Tab.AUTOPILOT
    }

    override fun onStart() {
        super.onStart()
        app.visible = true
    }

    override fun onStop() {
        app.visible = false
        vm.saveChat()
        WebBrowser.saveCookies()
        super.onStop()
    }

    override fun onDestroy() {
        WebBrowser.attachActivity(null)
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        vm.refreshPermission()
        vm.refreshModels()
        vm.refreshTermux()
        if (BuildConfig.AUTOPILOT && Autopilot.enabled && !Autopilot.awaitingConsent && !AutopilotService.running) {
            AutopilotService.start(this)
        }
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
        val preview = vm.htmlPreview
        if (preview != null) {
            HtmlPreviewScreen(preview) { vm.htmlPreview = null }
        } else if (editing != null) {
            EditorScreen(vm, editing)
        } else Column(Modifier.fillMaxSize()) {
            Box(Modifier.weight(1f)) {
                when (vm.tab) {
                    Tab.AUTOPILOT -> AutopilotScreen(vm)
                    Tab.HOME -> HomeScreen(vm)
                    Tab.MODELS -> ModelsScreen(vm)
                    Tab.CODE -> CodeScreen(vm)
                    Tab.BROWSER -> BrowserScreen(vm)
                    Tab.FILES -> FilesScreen(vm)
                }
            }
            BottomBar(vm.tab) { vm.tab = it }
        }
        // After a reboot: ask before loading the model, whichever screen is open.
        if (BuildConfig.AUTOPILOT && Autopilot.awaitingConsent) ConsentDialog()
    }
}

@Composable
private fun BottomBar(current: Tab, onSelect: (Tab) -> Unit) {
    val items = listOfNotNull(
        if (BuildConfig.AUTOPILOT) Tab.AUTOPILOT to Icons.Filled.SmartToy else null,
        Tab.HOME to Icons.Filled.Home,
        Tab.MODELS to Icons.Filled.ViewList,
        Tab.CODE to Icons.Filled.Code,
        Tab.BROWSER to Icons.Filled.Public,
        Tab.FILES to Icons.Filled.Folder
    )
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = if (items.size > 5) 8.dp else 16.dp, vertical = 8.dp)
            .clip(RoundedCornerShape(50))
            .background(C.Card)
            .border(1.5.dp, C.Border, RoundedCornerShape(50))
            .padding(8.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically
    ) {
        val itemSize = if (items.size > 5) 46.dp else 54.dp
        items.forEach { (tab, icon) -> NavItem(icon, tab == current, itemSize) { onSelect(tab) } }
    }
}

@Composable
private fun NavItem(icon: ImageVector, selected: Boolean, size: androidx.compose.ui.unit.Dp, onClick: () -> Unit) {
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(if (selected) Color.White else Color.Transparent)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, contentDescription = null, tint = if (selected) Color.Black else Color.White, modifier = Modifier.height(28.dp).size(28.dp))
    }
}
