package com.localforge.app.agent

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

data class RunResult(
    val lang: String,
    val ok: Boolean,
    val timedOut: Boolean,
    val output: String,
    /** Set for HTML: the page to show the user. */
    val previewFile: File? = null
) {
    /** What the model sees after its code ran. */
    fun forModel(): String {
        val head = when {
            previewFile != null -> "Your HTML page was saved and is now shown to the user."
            timedOut -> "Your $lang code was stopped because it took too long."
            ok -> "Your $lang code ran successfully. Output:"
            else -> "Your $lang code failed. Output and error:"
        }
        return "$head\n```\n$output\n```"
    }
}

/** A language that runs through Termux: file name and the bash command that compiles/runs it. */
private data class TermuxLang(val file: String, val command: String, val pkg: String)

/** Runs code on the phone: Python/JS/HTML/SQL/shell built in, everything else through Termux. */
object CodeRunner {
    private const val TIMEOUT_SEC = 30
    private const val COMPILE_TIMEOUT_SEC = 120
    private const val MAX_OUTPUT = 6000
    private val dispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()

    /** Languages that work with no setup at all. */
    val builtIn = listOf("python", "javascript", "typescript", "html", "sql", "shell", "lua", "ruby", "php", "c", "cpp")

    /** Languages that only work once Termux is set up. */
    val termuxOnly = setOf("java", "kotlin", "go", "rust", "perl", "node", "bash")

    private val termuxLangs = mapOf(
        "c" to TermuxLang("main.c", "clang main.c -o main -lm && ./main", "clang"),
        "cpp" to TermuxLang("main.cpp", "clang++ -std=c++17 main.cpp -o main && ./main", "clang"),
        "go" to TermuxLang("main.go", "go run main.go", "golang"),
        "rust" to TermuxLang("main.rs", "rustc main.rs -o main && ./main", "rust"),
        "java" to TermuxLang("Main.java", "java Main.java", "openjdk-21"),
        "kotlin" to TermuxLang("main.kt", "kotlinc main.kt -include-runtime -d main.jar 2>&1 | grep -v '^warning' ; java -jar main.jar", "kotlin"),
        "ruby" to TermuxLang("main.rb", "ruby main.rb", "ruby"),
        "php" to TermuxLang("main.php", "php main.php", "php"),
        "lua" to TermuxLang("main.lua", "(command -v lua5.4 >/dev/null && lua5.4 main.lua) || lua main.lua", "lua54"),
        "perl" to TermuxLang("main.pl", "perl main.pl", "perl"),
        "node" to TermuxLang("main.js", "node main.js", "nodejs"),
        "typescript" to TermuxLang("main.ts", "npx --yes tsx main.ts", "nodejs"),
        "bash" to TermuxLang("main.sh", "bash main.sh", "bash")
    )

    val termuxLanguages: Set<String> get() = termuxLangs.keys

    suspend fun run(context: Context, lang: String, code: String, workDir: File): RunResult = withContext(dispatcher) {
        workDir.mkdirs()
        try {
            when (lang) {
                "python" -> runPython(context, "run", code, workDir, "python")
                "sql" -> runPython(context, "run_sql", code, workDir, "sql")
                "shell" -> runShell(code, workDir)
                "html" -> {
                    val f = File(workDir, "index.html")
                    f.writeText(code)
                    RunResult("html", true, false, "Saved ${f.absolutePath}", previewFile = f)
                }
                "typescript" -> JsRunner.run(context, lang, code, TIMEOUT_SEC * 1000L)
                // Real compilers/interpreters when Termux is set up; built-in engines otherwise.
                "javascript", "lua", "ruby", "php", "c", "cpp" ->
                    if (TermuxBridge.isReady(context) && workDir.absolutePath.startsWith("/storage/")) {
                        runTermux(context, if (lang == "javascript") "node" else lang, code, workDir)
                    } else JsRunner.run(context, lang, code, TIMEOUT_SEC * 1000L)
                else -> runTermux(context, lang, code, workDir)
            }
        } catch (e: Throwable) {
            RunResult(lang, false, false, "Couldn't run $lang: ${e.message}")
        }
    }

    private fun runPython(context: Context, fn: String, code: String, workDir: File, lang: String): RunResult {
        if (!Python.isStarted()) Python.start(AndroidPlatform(context.applicationContext))
        val raw = Python.getInstance().getModule("lf_runner")
            .callAttr(fn, code, workDir.absolutePath, TIMEOUT_SEC).toString()
        val status = raw.substringBefore('\n')
        val output = raw.substringAfter('\n', "").trimEnd()
        return RunResult(lang, status == "ok", status == "timeout", output)
    }

    private suspend fun runTermux(context: Context, lang: String, code: String, workDir: File): RunResult {
        val spec = termuxLangs[lang] ?: return RunResult(
            lang, false, false,
            "$lang can't run on this phone. Supported: ${(builtIn + termuxLangs.keys).joinToString()}."
        )
        if (!TermuxBridge.isInstalled(context)) {
            return RunResult(lang, false, false, "$lang can't run on this phone without Termux (not installed). Rewrite it in a supported language such as python, javascript, c or ruby — or the user can set up Termux on the Home tab.")
        }
        if (!TermuxBridge.hasPermission(context)) {
            return RunResult(lang, false, false, "LocalForge isn't allowed to use Termux yet. Tap \"Allow\" on the Home tab → Code runners.")
        }
        if (!workDir.absolutePath.startsWith("/storage/")) {
            return RunResult(lang, false, false, "Termux languages need all-files access so Termux can see the code. Allow it on the Home tab.")
        }
        // Each run gets its own folder so compiled files don't clash.
        val dir = File(workDir, "run-$lang").also { it.mkdirs() }
        var fileName = spec.file
        var command = spec.command
        if (lang == "java") {
            // `java X.java` needs the file named after the public class.
            val cls = Regex("public\\s+(?:final\\s+)?class\\s+(\\w+)").find(code)?.groupValues?.get(1) ?: "Main"
            fileName = "$cls.java"
            command = "java $cls.java"
        }
        File(dir, fileName).writeText(code)
        val timeout = if (lang in setOf("c", "cpp", "go", "rust", "kotlin", "java", "typescript")) COMPILE_TIMEOUT_SEC else TIMEOUT_SEC
        val script = "cd '${dir.absolutePath}' && timeout ${timeout}s bash -c ${shellQuote(command)}"
        val r = TermuxBridge.run(context, script, dir.absolutePath, (timeout + 15) * 1000L)
            ?: return RunResult(lang, false, true, "[No answer from Termux after ${timeout + 15}s. Open Termux once, then try again.]")
        if (r.error != null) {
            return RunResult(
                lang, false, false,
                "Termux refused to run it: ${r.error}\nMake sure you ran the setup command in Termux (it enables allow-external-apps)."
            )
        }
        var text = (r.stdout + (if (r.stderr.isNotBlank()) "\n" + r.stderr else "")).trimEnd()
        val timedOut = r.exitCode == 124
        when (r.exitCode) {
            0 -> {}
            124 -> text += "\n[Stopped: ran longer than ${timeout}s]"
            127 -> text += "\n[Command not found — in Termux run: pkg install ${spec.pkg}]"
            else -> text += "\n[exit code ${r.exitCode}]"
        }
        return RunResult(lang, r.exitCode == 0, timedOut, trim(text.trim()).ifBlank { "(no output)" })
    }

    private fun shellQuote(s: String) = "'" + s.replace("'", "'\\''") + "'"

    private fun trim(t: String) =
        if (t.length > MAX_OUTPUT) t.take(MAX_OUTPUT / 2) + "\n...[output trimmed]...\n" + t.takeLast(MAX_OUTPUT / 2) else t

    private fun runShell(code: String, workDir: File): RunResult {
        val proc = ProcessBuilder("sh", "-c", code)
            .directory(workDir)
            .redirectErrorStream(true)
            .start()
        proc.outputStream.close()
        val buf = StringBuilder()
        val reader = Thread {
            try {
                proc.inputStream.bufferedReader().use { r ->
                    val chunk = CharArray(4096)
                    while (true) {
                        val n = r.read(chunk)
                        if (n < 0) break
                        synchronized(buf) { if (buf.length < MAX_OUTPUT * 2) buf.append(chunk, 0, n) }
                    }
                }
            } catch (_: Exception) {
            }
        }
        reader.start()
        val finished = proc.waitFor(TIMEOUT_SEC.toLong(), TimeUnit.SECONDS)
        if (!finished) proc.destroyForcibly()
        reader.join(2000)
        var text = trim(synchronized(buf) { buf.toString() }.trimEnd())
        val exit = if (finished) proc.exitValue() else -1
        if (text.isEmpty()) text = if (finished && exit == 0) "(no output)" else ""
        if (finished && exit != 0) text += "\n[exit code $exit]"
        if (!finished) text += "\n[Stopped: ran longer than $TIMEOUT_SEC seconds]"
        return RunResult("shell", finished && exit == 0, !finished, text.trim())
    }
}
