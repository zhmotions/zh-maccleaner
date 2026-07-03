# ZH MacCleaner 1.1.1

## Fixed
- Blank/white window on new macOS (rebuilt with modern Tk 8.6) — UI renders correctly again.
- Review popup: retries through a clean-IP relay when the host firewall blocks the direct post, and
  shows the server's real reply (e.g. "already reviewed") instead of a generic network error.
- Uninstaller and deletes are honest: shows what couldn't be removed (running app / permission), with
  a fallback move to Trash when Finder refuses.
- Cache clean explains when caches came back (open Chrome/Safari/Adobe rebuilds them live) and when
  Full Disk Access is needed.

## App
- Universal (Intel + Apple Silicon), macOS 11+. Version shown in the header/sidebar/About.
- Ships as .zip containing the .app, the .pkg installer, and an install README.
