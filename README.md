# LocalForge Desktop (Windows)

Your private, uncensored AI for your PC. The desktop version of the LocalForge Android app.
Everything runs on your own computer with llama.cpp — no account, no cloud, nothing you type leaves your PC.

## Get it

1. Open **Releases → LocalForge Desktop (Windows) latest build** (tag `desktop-latest`) and download `LocalForge-Desktop-Windows.zip`.
2. Right-click the zip → **Extract All**. Put the folder anywhere (e.g. `C:\LocalForge`).
3. Open the `LocalForge` folder and double-click **LocalForge.exe**.
   If Windows SmartScreen warns you ("unknown publisher"), click **More info → Run anyway**.

Keep the `engines` folder next to `LocalForge.exe`.

## First run

1. **Models** tab → **Find the best models for my PC**. It reads your graphics card, memory and disk, finds any uncensored models you already have, and lists the smartest uncensored models that will run fast on your hardware.
2. Click **Download** on a pick (downloads resume if interrupted).
3. **Home** → **Load & use** → **Chat**.

## What it can do

- Chat with uncensored / abliterated GGUF models (only those are listed). Search all of Hugging Face from the Models tab.
- Uses your graphics card automatically through the Vulkan engine (NVIDIA, AMD and Intel), and falls back to the CPU.
- Finds, lists, reads, writes, opens and deletes files anywhere on your PC (indexes your drives so it can find things by name or type).
  Writing, running and opening need your click on an approval card unless you turn on auto-approve; deletes always ask.
- Writes and runs its own code, reads the output and fixes errors: Python (built in), SQL (SQLite), HTML (shown in the app),
  PowerShell and CMD always work; JavaScript/TypeScript, C/C++, Java, Go, Rust, Ruby, PHP, Lua, Perl work when installed.
- Has its own web browser in the **Browser** tab (runs on the Edge that comes with Windows, in the background, with its own profile so your logins stay).
  You can browse it yourself, and the AI can search the web, open pages, read them, click, fill in forms and run JavaScript on the page to read or change data.
  Reading is automatic; clicking, typing and scripts show an approval card unless you tick "Let the AI click and type here without asking".
- File manager with a text editor and "Ask AI about this file".
- Chat is saved between runs.

## Tips

- Q4_K_M files are the best balance. Avoid Q2/Q3 (they produce gibberish).
- Models that fit fully in your graphics card's memory (VRAM) are many times faster. The Models tab sizes picks for you.
- NVIDIA users who want the CUDA engine: download `llama-…-bin-win-cuda-…-x64.zip` and its `cudart-…` zip from the llama.cpp releases,
  extract both into `engines\cuda` (so `engines\cuda\llama-server.exe` exists), and pick **cuda** under Home → Settings → Engine.
- Settings, models and chat live in `%LOCALAPPDATA%\LocalForge`. The AI's code runs in `Documents\LocalForge`.
- If the app window won't open (very old Windows without WebView2), it opens in your browser instead. `LocalForge.exe --browser` forces that.

## Build it yourself

Pushing to this branch builds `LocalForge-Desktop-Windows.zip` on GitHub Actions (`.github/workflows/build-windows.yml`):
unit tests, PyInstaller build, bundles llama.cpp CPU + Vulkan engines, then a self-test that downloads a tiny uncensored model
and chats with it on Windows before publishing the release.

Run from source: `pip install -r requirements.txt` then `python run_localforge.py` (set `LOCALFORGE_LLAMA_SERVER` to a llama-server binary,
or put engines in `engines\<cpu|vulkan|cuda>`).
