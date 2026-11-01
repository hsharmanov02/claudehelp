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
}

object FileTools {
    private const val MAX_READ_CHARS = 12_000

    private val simpleTag = Regex("""<(list|read|mkdir|delete)\s+path\s*=\s*"([^"]+)"\s*/?>""", RegexOption.IGNORE_CASE)
    private val writeTag = Regex("""<write\s+path\s*=\s*"([^"]+)"\s*>\r?\n?([\s\S]*?)</write>""", RegexOption.IGNORE_CASE)

    fun systemPrompt(workspace: String): String = """
You are LocalForge, an expert coding assistant running fully offline on the user's Android phone.
You write clear, correct code and explain briefly. Put code in ``` blocks.

You can work with the user's files by writing these tags in your reply, each on its own line:
<list path="/full/path/to/folder"/>      show what is in a folder
<read path="/full/path/to/file"/>        read a file
<write path="/full/path/to/file">
complete new file content here
</write>                                 create or overwrite a file
<mkdir path="/full/path/to/folder"/>     create a folder
<delete path="/full/path/to/file"/>      delete a file or folder

Rules:
- Always use full paths. The user's main folder is $workspace
- After <list> or <read>, stop your reply. The results will be sent back to you.
- Only write, create or delete files when the user asks for it.
- <write> must contain the entire file, never a partial snippet.
""".trim()

    fun parse(reply: String): List<FileAction> {
        val found = mutableListOf<Pair<Int, FileAction>>()
        for (m in writeTag.findAll(reply)) {
            found += m.range.first to FileAction.Write(m.groupValues[1].trim(), m.groupValues[2].trimEnd())
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

    /** Runs an action and returns a short text result for the model / user. */
    fun execute(action: FileAction): String = try {
        val f = File(action.path)
        when (action) {
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
