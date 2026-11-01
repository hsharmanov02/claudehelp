# LocalForge

Offline coding assistant for Android. Runs GGUF models on the phone with llama.cpp, downloads them from Hugging Face, and can list, read, create, edit and delete your files (changes need your approval unless you turn on auto-apply; deletes always ask).

## Get the APK (phone only, free)

1. Sign in at github.com in your phone browser and create a new **private** repository (tick "Add a README").
2. In the repo: **Add file → Create new file**. Name it exactly `.github/workflows/build-apk.yml`, paste the contents of `build-apk.yml`, then **Commit**.
3. **Add file → Upload files**, upload `LocalForge-source.zip`, then **Commit**.
4. Open the **Actions** tab and wait about 15–25 minutes for the green tick.
5. Open **Releases → LocalForge latest build** and download `LocalForge.apk`. Install it (allow "install unknown apps" for your browser when asked).

To update the app later, upload a new `LocalForge-source.zip` (replace the old one). Every build is signed with the same key, so it installs over the previous version.

If a build fails, open the failed run, copy the red error lines, and send them to Claude.

## First run

1. Home → checklist → **Allow all-files access**.
2. Models → tap **Show 1.5B** under "Recommended for coding" → download the **Q4_K_M** file.
3. Home → **Load & use** → pick the model.
4. Code tab → ask away.

## Notes

- 64-bit ARM phones only (arm64-v8a), Android 11+.
- Phones can't give apps root. "All-files access" is the highest file permission an Android app can get; system folders stay off limits.
- Small phone models make mistakes, especially with file commands. Read the approval cards before tapping Approve.
