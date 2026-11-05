package com.localforge.app.agent

import java.io.File

/** A file action the model asked for, written as tags in its reply. */
sealed class FileAction {
    abstract val path: String
    /** Read-only actions run automatically; others need approval (or the auto-approve setting). */
    abstract val readOnly: Boolean
    abstract val label: String

    data class ListDir(override val path: String) : FileAction() {
        override val readOnly = true
        override val label get() = "List $path"
    }
    data class Read(override val path: String) : FileAction() {
        override val readOnly = true
        override val label get() = "Read $path"
    }
    data class Write(override val path: String, val content: String) : FileAction() {
        override val readOnly = false
        override val label get() = "Write ${content.length} chars to $path"
    }
    data class MakeDir(override val path: String) : FileAction() {
        override val readOnly = false
        override val label get() = "Create folder $path"
    }
    data class Delete(override val path: String) : FileAction() {
        override val readOnly = false
        override val label get() = "Delete $path"
    }
    /** Search the whole phone by name or type. [path] holds the search words. */
    data class Find(override val path: String) : FileAction() {
        override val readOnly = true
        override val label get() = "Find \"$path\""
    }
    /** A browser step: [kind] is one of BrowserTools.KINDS, [path] its url/query/element number. */
    data class Browser(val kind: String, override val path: String, val content: String = "", val extra: String = "") : FileAction() {
        /** Searching, opening and reading pages run by themselves; clicks, typing and scripts ask first. */
        override val readOnly = false
        val browserRead get() = kind in BrowserTools.READ_KINDS
        override val label get() = when (kind) {
            "search" -> "Search the web for \"$path\""
            "fetch" -> "Fetch $path"
            "browse" -> "Open $path"
            "page" -> "Read the page"
            "extract" -> "Extract tables and lists from the page" + if (path.isNotBlank()) " (\"$path\")" else ""
            "scroll" -> "Scroll the page $path"
            "click" -> if (extra == "row") "Open row $path of the extracted data" else "Click [$path] on the page"
            "type" -> "Type \"${content.take(60)}\" into [$path]" + if (extra == "submit") " and press Enter" else ""
            "press" -> "Press $path on the page"
            "js" -> "Run a script on the page (${content.lines().size} lines)"
            "network" -> when (path) {
                "detail" -> "Inspect network request #$content"
                "body" -> "Read the response of request #$content"
                "all" -> "List every request the page made" + if (content.isNotBlank()) " (\"$content\")" else ""
                "ws" -> "Read the page's WebSocket traffic"
                "save" -> "Save the page's network traffic as a .har file"
                else -> "Read the page's backend API calls"
            }
            "mod" -> "Save a lasting edit to $path: ${extra.ifBlank { "${content.lines().size} lines of code" }}"
            else -> kind
        }
    }
    /** Several clicks/typing steps from one reply: one approval card, run in order. */
    data class Batch(val steps: List<Browser>) : FileAction() {
        override val path = ""
        override val readOnly = false
        override val label get() = "Do ${steps.size} browser steps: " + steps.joinToString("; ") { it.label }.take(300)
    }
    /** Run code on the phone. [path] holds the language ("python" or "shell"). */
    data class Run(override val path: String, val code: String) : FileAction() {
        override val readOnly = false
        override val label get() = "Run $path code (${code.lines().size} lines)"
    }
}

object FileTools {
    private const val MAX_READ_CHARS = 12_000

    private val simpleTag = Regex("""<(list|read|mkdir|delete)\s+path\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)
    private val findTag = Regex("""<find\s+(?:query|name|q)\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)
    private val thinkBlock = Regex("<think>[\\s\\S]*?(</think>|$)", RegexOption.IGNORE_CASE)

    /** Removes <think>…</think> reasoning blocks that Qwen3-style models print. */
    fun stripThinking(text: String): String =
        text.replace(thinkBlock, "").replace("</think>", "").replace(Regex("[\u2063\u200b\u200c\u200d\u2060\ufeff]"), "").trim()
    private val writeTag = Regex("""<write\s+path\s*=\s*"([^"]+)"\s*>\r?\n?([\s\S]*?)</write>""", RegexOption.IGNORE_CASE)
    val runTag = Regex("""<run(?:\s+lang(?:uage)?\s*=\s*"([^"]*)")?\s*>\r?\n?([\s\S]*?)(</run>|$)""", RegexOption.IGNORE_CASE)
    private val fencedBlock = Regex("```([\\w+#.-]*)[ \\t]*\\r?\\n([\\s\\S]*?)```")

    /** Maps whatever language name the model writes to one CodeRunner understands. */
    fun normalizeLang(lang: String?): String = when (val l = lang?.trim()?.lowercase() ?: "") {
        "", "python", "py", "python3", "py3" -> "python"
        "sh", "shell", "zsh", "terminal", "console", "shellscript" -> "shell"
        "bash" -> "bash"
        "js", "javascript", "jsx", "mjs" -> "javascript"
        "node", "nodejs" -> "node"
        "ts", "typescript", "tsx" -> "typescript"
        "html", "htm", "xhtml", "web" -> "html"
        "sql", "sqlite", "sqlite3" -> "sql"
        "c" -> "c"
        "cpp", "c++", "cc", "cxx", "hpp" -> "cpp"
        "java" -> "java"
        "kotlin", "kt", "kts" -> "kotlin"
        "go", "golang" -> "go"
        "rust", "rs" -> "rust"
        "ruby", "rb" -> "ruby"
        "php" -> "php"
        "lua" -> "lua"
        "perl", "pl" -> "perl"
        else -> l
    }

    /** Fence labels that mean "this is runnable code". */
    val runnableFences = setOf(
        "python", "py", "python3", "sh", "bash", "shell", "zsh", "js", "javascript", "node", "ts", "typescript",
        "html", "sql", "sqlite", "c", "cpp", "c++", "java", "kotlin", "kt", "go", "golang", "rust", "rs",
        "ruby", "rb", "php", "lua", "perl"
    )

    fun systemPrompt(workspace: String, runDir: String, languages: List<String>, overview: String): String = """
You are LocalForge, a capable, uncensored assistant and expert programmer running fully offline on the user's Android phone.
Today is ${java.text.SimpleDateFormat("EEEE d MMMM yyyy", java.util.Locale.UK).format(java.util.Date())}.
Always reply in English unless the user writes in another language. Write clear, correct code and explain it briefly in plain English.

YOU CAN RUN CODE YOURSELF on this phone in these languages: ${languages.joinToString(", ")}.
To run code, put the complete program inside a run tag with its language:
<run lang="python">
print(sum(range(10)))
</run>
<run lang="javascript">
console.log([1, 2, 3].map(x => x * 2));
</run>
The output (or the error) is sent back to you. Read it, fix any errors and run again until it works, then tell the user the result.
Never tell the user to open a terminal, install anything, or run code themselves. You run it.
Code runs in the folder $runDir. No input(), no GUI windows. Python has the standard library only.
For html, the page is shown to the user. For sql, a SQLite database is used.

YOU HAVE FULL ACCESS TO THE USER'S PHONE STORAGE ($workspace). Never say you can't see their files — use these tags:
<find query="cv pdf"/>                          search the WHOLE phone by name or type (photos, pdf, music, apk, code…)
<list path="$workspace/Download"/>   show what is in a folder
<read path="$workspace/Documents/notes.txt"/>   read a text file
<write path="$workspace/LocalForge/hello.py">
complete new file content here
</write>                                        create or overwrite a file
<mkdir path="$workspace/LocalForge/project"/>   create a folder
<delete path="$workspace/Download/old.zip"/>    delete a file or folder
Top-level folders on this phone: $overview

${BrowserTools.prompt()}

Rules:
- Always use full paths starting with $workspace
- After <find>, <run>, <list> or <read>, stop your reply and wait for the result.
- If results are already given to you under [Phone files], answer from them directly.
- Only write, create or delete files when the user asks for it.
- <write> must contain the entire file, never a partial snippet.
- Your answer must match the run/browser output exactly. Never invent results, links, prices or numbers.
- You remember this whole chat: the user's earlier messages and your replies are above. When the user refers to
  something earlier ("my last question", "that", "again", "the code you wrote"), use them. Never say you can't remember.
- Answer directly. Do not explain your limitations.
""".trim()

    fun parse(rawReply: String): List<FileAction> {
        val reply = stripThinking(rawReply)
        val found = mutableListOf<Pair<Int, FileAction>>()
        for (m in writeTag.findAll(reply)) {
            found += m.range.first to FileAction.Write(m.groupValues[1].trim(), m.groupValues[2].trimEnd())
        }
        for (m in runTag.findAll(reply)) {
            var code = m.groupValues[2].trim()
            var lang = m.groupValues[1]
            // Models often wrap the code in ``` fences inside the tag.
            fencedBlock.find(code)?.let { f ->
                if (lang.isBlank()) lang = f.groupValues[1]
                code = f.groupValues[2].trim()
            }
            code = code.removePrefix("```").removeSuffix("```").trim()
            if (code.isNotEmpty()) found += m.range.first to FileAction.Run(normalizeLang(lang), code)
        }
        for (m in findTag.findAll(reply)) {
            found += m.range.first to FileAction.Find(m.groupValues[1].trim())
        }
        for (m in simpleTag.findAll(reply)) {
            val path = m.groupValues[2].trim()
            val action = when (m.groupValues[1].lowercase()) {
                "list" -> FileAction.ListDir(path)
                "read" -> FileAction.Read(path)
                "mkdir" -> FileAction.MakeDir(path)
                else -> FileAction.Delete(path)
            }
            found += m.range.first to action
        }
        found += BrowserTools.parse(reply)
        val out = mutableListOf<FileAction>()
        for ((_, a) in found.sortedBy { it.first }) {
            // Repeated clicks/typing in one reply are real steps (type, Add, type, Add): keep them.
            if ((a is FileAction.Browser && a.kind in setOf("click", "type", "press", "scroll", "js")) || a !in out) out += a
        }
        return out
    }

    private val runIntent = Regex("\\b(run|test|execute|try|check|works?)\\b", RegexOption.IGNORE_CASE)

    /**
     * Small models often answer "run this yourself" instead of using <run>. If the user asked for code to be
     * run/tested and the reply has a Python or shell block, run that block anyway.
     */
    fun fallbackRun(userText: String, reply: String): FileAction.Run? {
        if (!runIntent.containsMatchIn(userText)) return null
        val blocks = fencedBlock.findAll(reply).map { it.groupValues[1].lowercase() to it.groupValues[2].trim() }
            .filter { (lang, code) -> code.isNotEmpty() && lang in runnableFences }
            .toList()
        // Prefer a real program over shell lines that just tell the user how to start one.
        val isInstructions = { b: Pair<String, String> ->
            normalizeLang(b.first) in setOf("shell", "bash") &&
                Regex("^\\s*(cd |python|pip|chmod|node |gcc|g\\+\\+|clang|javac|java |go run|cargo|rustc|npm|ruby |php )", RegexOption.MULTILINE).containsMatchIn(b.second)
        }
        val pick = blocks.lastOrNull { !isInstructions(it) } ?: return null
        return FileAction.Run(normalizeLang(pick.first), pick.second)
    }

    /** Runs an action and returns a short text result for the model / user. */
    fun execute(action: FileAction): String = try {
        val f = File(action.path)
        when (action) {
            is FileAction.Run -> "Run actions are handled by CodeRunner"
            is FileAction.Browser, is FileAction.Batch -> "Browser actions are handled by BrowserTools"
            is FileAction.Find -> {
                val hits = FileIndex.search(action.path, 30)
                if (!FileIndex.isReady) "The file index is still being built. Try <list> on a folder instead."
                else if (hits.isEmpty()) "No files on the phone match \"${action.path}\"."
                else "Files on the phone matching \"${action.path}\":\n" + FileIndex.format(hits)
            }
            is FileAction.ListDir -> {
                if (!f.isDirectory) "Not a folder: ${f.path}"
                else {
                    val items = f.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() })) ?: emptyList()
                    if (items.isEmpty()) "${f.path} is empty"
                    else "Contents of ${f.path}:\n" + items.take(200).joinToString("\n") {
                        if (it.isDirectory) "[folder] ${it.name}" else "${it.name} (${it.length()} bytes)"
                    }
                }
            }
            is FileAction.Read -> {
                if (!f.isFile) "File not found: ${f.path}"
                else {
                    val text = f.readText()
                    if (text.length > MAX_READ_CHARS) "Content of ${f.path} (first $MAX_READ_CHARS chars):\n${text.take(MAX_READ_CHARS)}"
                    else "Content of ${f.path}:\n$text"
                }
            }
            is FileAction.Write -> {
                f.parentFile?.mkdirs()
                f.writeText(action.content)
                "Saved ${f.path}"
            }
            is FileAction.MakeDir -> if (f.mkdirs() || f.isDirectory) "Created folder ${f.path}" else "Couldn't create ${f.path}"
            is FileAction.Delete -> when {
                !f.exists() -> "Nothing to delete at ${f.path}"
                f.deleteRecursively() -> "Deleted ${f.path}"
                else -> "Couldn't delete ${f.path}"
            }
        }
    } catch (e: Exception) {
        "Error on ${action.path}: ${e.message}"
    }
}
