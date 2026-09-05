# ZH MacCleaner 1.1.7

- Fixed: the Cleanup screen's Rescan / Clean Selected / Empty Trash buttons were pushed off-screen and unclickable when the card list was tall. Buttons are now pinned and the cards scroll.
- Headings now use the brand display font (Bricolage Grotesque); body text stays Inter.

# ZH MacCleaner 1.1.6

- New app icon — leaf-green on the ZH Motions brand palette (was maroon), matching the in-app theme.

# ZH MacCleaner 1.1.5

- **Old Installers** — finds .dmg / .pkg / .iso in Downloads older than two weeks and trashes just the download (never the installed app).
- **iOS Backups** — lists local iPhone / iPad backups (device name, date, size — often tens of GB) so you can remove ones you don't need. iCloud backups untouched.
- **App Caches** — new Cleanup card for Slack, Spotify, Zoom, Discord, Teams, Creative Cloud, Xcode cache folders.
- **Maintenance** — added Clean Simulators, Trim Xcode DeviceSupport, and Docker Prune.
- **Settings** — protect any folder so no scan ever lists or deletes anything inside it; shows lifetime space reclaimed.
- Cleanup screen now shows an all-time "space reclaimed" total.
- Hardened safety: every automatic scan filters its results through the protected-folder list, and a last-ditch guard blocks any attempt to trash a home container (Documents, Desktop, Library…) or a path outside your home folder.

# ZH MacCleaner 1.1.4

- New look: re-themed to the ZH Motions "Fetchleaf" brand — leaf-green surfaces, gold action buttons, ink text — matching ZH Downloader.
- Action buttons are now drawn (rounded gold/leaf pills) instead of the flat grey macOS button, so they carry the theme colour and hover/press state.
- Brand type (Outfit / Inter) picked up when installed, system font otherwise.
- Carried: universal build target, self-installing updates, relay-first update check.

# ZH MacCleaner 1.1.3

- Self-installing updates: the app downloads new versions in the background and opens the installer automatically the next time you launch it.
- Update check now goes through the clean-IP relay first — works even where the host firewall blocked it.
- Carried: universal (Intel + Apple Silicon), rendering fix, honest delete/uninstall reporting.

# ZH MacCleaner 1.1.2

- Update check fixed: the host firewall 403s the app's direct check for many users ("no internet or site offline" even when online) — now falls back to GitHub Releases.
- Pro activation more reliable: license verify retries through a clean-IP relay when the direct request is blocked.
- Carried from 1.1.1: Tk rendering fix (no blank window), review relay fallback, honest delete/uninstall reporting. Universal, macOS 11+.
