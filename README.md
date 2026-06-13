<div align="center">

<img src="assets/icon.png" width="96" alt="ZH MacCleaner"/>

# ZH MacCleaner

**A safe, simple Mac cleaner — free up disk space without breaking anything.**

Made by [ZH Motions](https://zhmotions.com) · macOS

![platform](https://img.shields.io/badge/platform-macOS%2011%2B-7A1F2B)
![license](https://img.shields.io/badge/license-MIT-7A1F2B)
![version](https://img.shields.io/badge/version-1.0-7A1F2B)

</div>

---

## What it does

ZH MacCleaner removes the junk your Mac rebuilds automatically — and **never touches system files or your documents**.

| Tool | What it does |
|------|--------------|
| 🧹 **Cleanup** | Clears app caches, logs & browser caches (they regenerate). |
| 📦 **Large Files** | Finds files > 100 MB in Downloads/Desktop/Documents/Movies. |
| 🗑️ **Uninstaller** | Removes an app **and** its leftover caches/prefs/support files. |
| 👯 **Duplicates** | Finds identical files, keeps one, trashes the extras. |
| 🛠 **Maintenance** | Free RAM, flush DNS, reindex Spotlight, rebuild Launch DB. |

Auto-scans on launch and shows sizes **before** you clean.

## Safety

- Only touches a **fixed whitelist** of safe user folders.
- Caches/logs are **deleted** (macOS rebuilds them).
- Your own files go to the **Trash** — fully recoverable.
- Never deletes documents, photos, or system files.

## Install

Download from **[zhmotions.com/maccleaner](https://www.zhmotions.com/maccleaner)** →

1. Open the **.dmg** → drag **ZH MacCleaner** to `/Applications`.
2. First launch: **right-click → Open** (unsigned app, Gatekeeper asks once).

The app checks zhmotions.com for updates automatically.

> **Tip:** For full cache cleanup, grant Full Disk Access:
> System Settings → Privacy & Security → Full Disk Access → **+** → add ZH MacCleaner.

## Build from source

Requires a Python with modern Tk (8.6 / 9.0). On Apple Silicon:

```bash
brew install python-tk@3.12
git clone https://github.com/ZHMotions/ZH-MacCleaner.git
cd ZH-MacCleaner
bash build_app.sh          # builds "ZH MacCleaner.app"
open "ZH MacCleaner.app"
```

Run directly (dev):

```bash
/opt/homebrew/bin/python3.12 zh_cleaner.py
```

> Apple's built-in Tk 8.5 mis-renders widgets on recent macOS — use Homebrew's `python-tk` (Tk 8.6/9.0). The app auto-detects a compatible Python at launch.

## License

MIT © ZH Motions — see [LICENSE](LICENSE).
