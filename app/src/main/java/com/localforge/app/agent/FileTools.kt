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
    /** Run code on the phone. [path] holds the language ("python" or "shell"). */
    data class Run(override val path: String, val code: String) : FileAction() {
        override val readOnly = false
        override val label get() = "Run $path code (${code.lines().size} lines)"
    }
}

object FileTools {
    private const val MAX_READ_CHARS = 12_000

    private val simpleTag = Regex("""<(list|read|mkdir|delete)\s+path\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)
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

    fun systemPrompt(workspace: String, runDir: String, languages: List<String>): String = """
You are LocalForge, an expert coding assistant running fully offline on the user's Android phone.
Always reply in English. Write clear, correct code and explain it briefly in plain English.

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

You can also work with the user's files using these tags, each on its own line:
<list path="/full/path/to/folder"/>      show what is in a folder
<read path="/full/path/to/file"/>        read a file
<write path="/full/path/to/file">
complete new file content here
</write>                                 create or overwrite a file
<mkdir path="/full/path/to/folder"/>     create a folder
<delete path="/full/path/to/file"/>      delete a file or folder

Rules:
- Always use full paths. The user's main folder is $workspace
- After <run>, <list> or <read>, stop your reply and wait for the result.
- Only write, create or delete files when the user asks for it.
- <write> must contain the entire file, never a partial snippet.
""".trim()

    fun parse(reply: String): List<FileAction> {
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
        return found.sortedBy { it.first }.map { it.second }.distinct()
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
