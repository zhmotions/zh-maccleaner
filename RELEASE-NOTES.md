# ZH MacCleaner 1.1.3

- Self-installing updates: the app downloads new versions in the background and opens the installer automatically the next time you launch it.
- Update check now goes through the clean-IP relay first — works even where the host firewall blocked it.
- Carried: universal (Intel + Apple Silicon), rendering fix, honest delete/uninstall reporting.

# ZH MacCleaner 1.1.2

- Update check fixed: the host firewall 403s the app's direct check for many users ("no internet or site offline" even when online) — now falls back to GitHub Releases.
- Pro activation more reliable: license verify retries through a clean-IP relay when the direct request is blocked.
- Carried from 1.1.1: Tk rendering fix (no blank window), review relay fallback, honest delete/uninstall reporting. Universal, macOS 11+.
