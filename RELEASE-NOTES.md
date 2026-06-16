# ZH MacCleaner 1.0

A safe, simple Mac cleaner by ZH Motions.

## ⬇️ How to install (read this!)

### ✅ Recommended — use the **.pkg** installer (auto-fixes the "damaged" warning)
1. Download **`ZH-MacCleaner-1.0.pkg`** below.
2. **Right-click** the file → **Open** → in the popup click **Open** again.
   *(One-time step — macOS asks once for apps outside the App Store.)*
3. Click **Continue → Install** → enter your Mac password.
4. Done. Open **ZH MacCleaner** from Applications / Launchpad — it just opens. ✅

> The `.pkg` installs the app cleanly and automatically removes the macOS quarantine flag, so you will **not** see the "damaged" error.

### ⚠️ If you use the `.dmg` or `.zip` instead
macOS may say **"ZH MacCleaner is damaged and can't be opened."**
This is **not** a real problem — just macOS blocking an unsigned app. Fix it one of two ways:

**Easiest:** open **Terminal**, paste this, press Enter:
```
xattr -cr "/Applications/ZH MacCleaner.app"
```
Then open the app normally.

**Or:** after the warning → **System Settings → Privacy & Security** → scroll down → **Open Anyway**.

---

## Features
- 🧹 **Cleanup** — app caches, logs, browser caches (auto-scans on launch)
- 📦 **Large Files** — find files > 100 MB, send to Trash
- 🗑️ **Uninstaller** — remove an app + all its leftover files
- 👯 **Duplicates** — find identical files, trash the extras
- 🛠 **Maintenance** — free RAM, flush DNS, reindex Spotlight, rebuild Launch DB
- ℹ️ In-app Help & tooltips explaining every tool

## Notes
- **Safe by design:** only known cache folders are deleted; your files go to the Trash (recoverable).
- For full cache cleanup, grant **Full Disk Access** (Settings → Privacy & Security → Full Disk Access → add ZH MacCleaner).
- Requires **macOS 11 (Big Sur) or newer**, Apple Silicon.

## Downloads
| File | Use this when |
|------|---------------|
| **ZH-MacCleaner-1.0.pkg** | ✅ Recommended — easiest, no "damaged" error |
| ZH-MacCleaner-1.0.dmg | Classic drag-to-Applications (needs the fix above) |
| ZH-MacCleaner-1.0-macOS.zip | Plain app (needs the fix above) |
