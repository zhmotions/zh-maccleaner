#!/usr/bin/env python3
"""
ZH Cleaner — a safe Mac cleaner (pro UI, ZH Motions "Fetchleaf" brand theme)

Cleans: System junk (caches/logs), Browser caches, Dev junk, Large/old files.
Safety:
  • Only a hard-coded whitelist of known-safe user paths.
  • Cache/log contents are deleted (OS/apps regenerate them).
  • Your own files (large-file finder) move to the macOS Trash (recoverable).
  • Auto-scans on launch and shows sizes BEFORE you clean.
"""

import os, sys, threading, queue, time, subprocess, hashlib, plistlib, json, re, shutil
import urllib.request, urllib.parse, ssl

# SSL context with a real CA bundle. A PyInstaller .app on a fresh client Mac often
# can't find the system CA certs → urlopen raises SSLCertVerificationError →
# "Couldn't reach the license server" even though the network is fine. Bundle certifi
# so verification works on every Mac; fall back to the default context if unavailable.
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    try:
        SSL_CTX = ssl.create_default_context()
    except Exception:
        SSL_CTX = None
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

HOME = Path.home()
if getattr(sys, "frozen", False):                       # PyInstaller bundle
    APP_DIR = Path(getattr(sys, "_MEIPASS", Path.cwd()))
elif "__file__" in globals():
    APP_DIR = Path(__file__).resolve().parent
else:
    APP_DIR = Path.cwd()

APP_VERSION = "1.1.7"
SITE        = "https://www.zhmotions.com"
# Same update system as ZH Downloader: zhmotions.com FIRST, GitHub as fallback.
#   version.json -> {"version":"1.1","download_url":"https://.../ZH-MacCleaner.dmg","notes":"..."}
UPDATE_SOURCES = [
    # Relay FIRST — the Cloudflare Worker forwards server-side, so Hostinger's firewall
    # (which 403s Python's TLS fingerprint for many client IPs) never sees the client.
    ("relay", "https://api-relay-2.zhmotionspanel.workers.dev/api.php?action=app_version&app=maccleaner", "zhm"),
    ("zhmotions", "https://zhmotions.com/maccleaner/version.json", "zhm"),
    ("github", "https://api.github.com/repos/zhmotions/zh-maccleaner/releases/latest", "gh"),
]
MAC_DL    = "https://zhmotions.com/maccleaner/download"
UPD_DIR   = HOME/".config"/"zhmaccleaner"
UPD_STATE = UPD_DIR/"update.json"

# ── Licensing: free app, Pro features unlocked by a key (self-hosted) ──
LICENSE_URL   = "https://zhmotions.com/api/license/verify"   # non-www + no .php (server strips it; redirects drop POST body)
LIC_FILE      = HOME/".config/zhmaccleaner/license.json"
PRO_FEATURES  = {"uninstall", "dupes", "maint"}     # locked until Pro
# ── In-app review prompt (after a few days of use) ──
REVIEW_URL    = "https://zhmotions.com/api.php?action=review_submit"
# Hostinger's firewall serves flagged IPs a 403 HTML challenge on direct POSTs (the "network problem"
# reports) — the Cloudflare Worker relay forwards from a clean IP, same as SMS/STT.
REVIEW_URL_FALLBACK = "https://api-relay-2.zhmotionspanel.workers.dev/api.php?action=review_submit"
REVIEW_FILE   = HOME/".config/zhmaccleaner/review.json"
REVIEW_AFTER_DAYS = 3
APP_SLUG      = "maccleaner"
GRACE_DAYS    = 14                                  # offline grace after last good check

# ── Settings + lifetime stats ───────────────────────────────────────────
SETTINGS_FILE = HOME/".config/zhmaccleaner/settings.json"   # { "exclusions": [paths] }
STATS_FILE    = HOME/".config/zhmaccleaner/stats.json"      # { "freed": bytes, "runs": n }

def load_settings():
    try: return json.loads(SETTINGS_FILE.read_text())
    except Exception: return {}

def save_settings(d):
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(d, indent=2))
    except Exception: pass

def load_stats():
    try: return json.loads(STATS_FILE.read_text())
    except Exception: return {"freed": 0, "runs": 0}

def bump_stats(freed_bytes):
    """Add to the lifetime 'space reclaimed' counter shown on the Cleanup screen."""
    try:
        s = load_stats()
        s["freed"] = int(s.get("freed", 0)) + max(0, int(freed_bytes))
        s["runs"]  = int(s.get("runs", 0)) + (1 if freed_bytes else 0)
        STATS_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(s))
    except Exception: pass

# ── Safety: paths ZH Cleaner must NEVER trash, whatever a scan turns up ──
# The auto-scans (installers, iOS backups, dev junk) pattern-match, so every
# candidate is filtered through path_protected() before it can be trashed. The
# manual pick lists (Large Files, Duplicates) also honour the user's own
# exclusions. move_to_trash() has a last-ditch guard on top of all this.
def _rp(p):
    try: return os.path.realpath(os.path.expanduser(str(p)))
    except Exception: return ""

HARD_PROTECT = [_rp(HOME/x) for x in (
    "Documents", "Desktop", "Pictures", "Music",
    "Library/Mobile Documents",              # iCloud Drive
    "Library/CloudStorage",                  # Dropbox / OneDrive / Google Drive
    "Library/Keychains", "Library/Group Containers/group.com.apple.notes",
    "Library/Application Support/AddressBook", "Library/Messages",
    "Library/Application Support/MobileSync",  # the container — only its children go
    ".ssh", ".gnupg", ".aws", ".config/gcloud", ".password-store",
)]

def user_exclusions():
    return [_rp(p) for p in load_settings().get("exclusions", []) if _rp(p)]

def _under(path, base):
    return path == base or path.startswith(base.rstrip("/") + "/")

def path_protected(path, hard=True):
    """True if `path` sits at/under a user exclusion (always) or a HARD_PROTECT
    root (when hard=True — used by the auto-scans, not the manual pick lists)."""
    rp = _rp(path)
    if not rp: return True                       # unresolvable → refuse to touch
    for base in user_exclusions():
        if _under(rp, base): return True
    if hard:
        for base in HARD_PROTECT:
            if base and _under(rp, base): return True
    return False

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")   # Cloudflare blocks bot UAs

def device_id():
    try:
        out = subprocess.run(["ioreg","-rd1","-c","IOPlatformExpertDevice"],
                             capture_output=True, text=True).stdout
        import re
        m = re.search(r'IOPlatformUUID" = "([^"]+)"', out)
        uid = m.group(1) if m else "unknown"
    except Exception:
        uid = "unknown"
    return hashlib.sha256(uid.encode()).hexdigest()[:16]

# ── Fetchleaf palette — ZH Motions brand (leaf greens, gold action, ink) ──
# Mirrors ZH Downloader's "Fetchleaf" theme: --leaf-* / --ink / --gold* from the
# brand guide. MAROON/GOLD keys kept (used app-wide) but now hold leaf + gold.
C = {
    "BG":"#f3faef", "SIDEBAR":"#e7f5e0", "HEADER":"#ffffff",   # leaf-050 ground
    "SURF":"#ffffff", "SURF2":"#e7f5e0", "BORDER":"#a5d698",   # leaf-200 border
    "MAROON":"#2b6627", "MAROON2":"#388837",   # primary chrome = deep leaf (leaf-800/600)
    "GOLD":"#c9922f", "GOLD2":"#8f6516",       # action accent = gold / gold-deep
    "TEXT":"#182b17", "MUTED":"#3f5b3c",       # ink / ink-soft
    "GREEN":"#388837", "RED":"#b3402e",
}
ON_GOLD  = "#2a1c02"         # readable ink on a gold fill
UIFONT   = "SF Pro Text"     # body — resolved to Inter (brand text face) at startup
HEADFONT = "SF Pro Display"  # headings — resolved to Bricolage Grotesque (brand display face)
MONO     = "SF Mono"

def _pick_family(prefs, fallback):
    """First installed family out of prefs. Needs a live Tk root, so call it
    after Tk() exists (App.__init__ does, before the UI is built)."""
    try:
        import tkinter.font as _tkfont
        fams = {f.lower() for f in _tkfont.families()}
    except Exception:
        fams = set()
    return next((p for p in prefs if p.lower() in fams), fallback)


def _pbg(w):
    try: return w.cget("bg")
    except Exception: return C["BG"]


class RoundedButton(tk.Canvas):
    """Chunky rounded action button — Tk ships none, and on macOS aqua tk.Button
    ignores -bg entirely (every button rendered as the same grey pill). This
    draws the ZH Downloader look: a rounded face sitting on a solid colour ledge,
    with hover / pressed / disabled states. configure(text=…, state=…) compatible.
        kind: "gold" (primary) · "ghost" (secondary) · "danger"
    """
    # kind -> (face colour key, ledge colour key, text colour)
    _KINDS = {
        "gold":   ("GOLD",  "GOLD2",  ON_GOLD),
        "ghost":  ("SURF2", "BORDER", None),      # None -> C["TEXT"]
        "danger": ("RED",   "RED",    "#ffffff"),
    }

    def __init__(self, parent, text, command, kind="gold",
                 pad=(22, 11), radius=11, plinth=4, font=None, **kw):
        import tkinter.font as _tkfont
        self._text, self._cmd, self._kind = text, command, kind
        self._radius, self._plinth = radius, plinth
        self._font = font or (UIFONT, 11, "bold")
        self._state, self._hover = "normal", False
        f = _tkfont.Font(font=self._font)
        w = f.measure(text) + pad[0] * 2
        h = f.metrics("linespace") + pad[1] * 2 + plinth
        # NB: not self._w/_h — tkinter.Misc already uses self._w for the widget
        # pathname; shadowing it breaks every later Tk call.
        self._bw, self._bh = w, h
        super().__init__(parent, width=w, height=h, highlightthickness=0, bd=0,
                         bg=_pbg(parent), cursor="pointinghand", **kw)
        self.bind("<Button-1>",        self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self._draw()

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2, x2-r, y2,
               x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self, pressed=False):
        self.delete("all")
        face_k, ledge_k, fgc = self._KINDS.get(self._kind, self._KINDS["gold"])
        if self._state == "disabled":
            face, ledge, fg = C["SURF2"], C["BORDER"], C["MUTED"]
        else:
            ledge = C[ledge_k]
            face  = ledge if self._hover else C[face_k]
            fg    = fgc or C["TEXT"]
        drop = self._plinth
        sink = drop if pressed else 0
        self._rrect(1, 1 + sink, self._bw - 1, self._bh - 1, self._radius,
                    fill=ledge, outline=ledge)                       # the ledge
        self._rrect(1, 1 + sink, self._bw - 1, self._bh - 1 - (drop - sink),
                    self._radius, fill=face, outline=face)           # the face
        self.create_text(self._bw / 2,
                         (1 + sink + self._bh - 1 - (drop - sink)) / 2,
                         text=self._text, fill=fg, font=self._font)

    def _set_hover(self, on):
        self._hover = on and self._state != "disabled"
        self._draw()

    def _press(self, _=None):
        if self._state != "disabled": self._draw(pressed=True)

    def _release(self, _=None):
        if self._state == "disabled": return
        self._draw()
        if self._cmd: self._cmd()

    def configure(self, **kw):
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text"); redraw = True
        if "state" in kw:
            self._state = kw.pop("state"); self._hover = False; redraw = True
            try: super().configure(
                cursor="arrow" if self._state == "disabled" else "pointinghand")
            except Exception: pass
        if kw: super().configure(**kw)
        if redraw: self._draw()
    config = configure

    def cget(self, key):
        if key == "text":  return self._text
        if key == "state": return self._state
        return super().cget(key)


def _chip(parent, text, cmd, kind="ghost"):
    """Small inline button as a tk.Label — unlike tk.Button, a Label honours -bg
    on macOS aqua, so row actions actually carry the theme colour."""
    face, fg = {"ghost":  (C["SURF2"], C["TEXT"]),
                "leaf":   (C["MAROON"], "#ffffff"),
                "gold":   (C["GOLD"],  ON_GOLD)}.get(kind, (C["SURF2"], C["TEXT"]))
    lbl = tk.Label(parent, text=f"  {text}  ", bg=face, fg=fg, font=(UIFONT, 10, "bold"),
                   padx=4, pady=4, cursor="pointinghand")
    lbl.bind("<Button-1>", lambda e: cmd())
    lbl.bind("<Enter>", lambda e: lbl.config(bg=C["BORDER"] if kind == "ghost" else C["GOLD2"]))
    lbl.bind("<Leave>", lambda e: lbl.config(bg=face))
    return lbl

# ── Helpers ─────────────────────────────────────────────────────────────
def human(n):
    n = float(n)
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def dir_size(path):
    p = str(path)
    if not os.path.exists(p): return 0
    try:
        out = subprocess.run(["du","-sk",p], capture_output=True, text=True, timeout=90)
        kb = out.stdout.split("\t")[0].strip() or (out.stdout.split() or ["0"])[0]
        return int(kb) * 1024
    except Exception:
        return 0

def _trash_forbidden(rp):
    """Last-ditch guard: paths this app must never send to Trash, no matter which
    code path asked. Catches bugs / bad globs before anything irreversible."""
    home = _rp(HOME)
    if not rp or rp in ("/", home): return True
    if not _under(rp, home): return True                 # anything outside ~
    # never a top-level container itself — only things inside it
    if os.path.dirname(rp) == home and os.path.basename(rp) in (
        "Documents", "Desktop", "Pictures", "Music", "Movies",
        "Downloads", "Library", "Applications", "Public"):
        return True
    return False

def move_to_trash(path):
    """Move a path to Trash. Returns True only if it's actually gone afterwards — so callers can
    report leftovers that couldn't be removed (running app / permission) instead of a false success."""
    p = str(path)
    if _trash_forbidden(_rp(p)):
        return False
    pe = p.replace('"','\\"')
    r = subprocess.run(["osascript","-e",
        f'tell application "Finder" to move (POSIX file "{pe}") to trash'], capture_output=True)
    if r.returncode == 0 and not os.path.exists(p):
        return True
    # Finder refused (locked/permission) → fall back to a plain move into ~/.Trash via mv.
    try:
        trash = HOME/".Trash"
        subprocess.run(["bash","-c",'mv -f "$0" "$1/" 2>/dev/null', p, str(trash)], timeout=30)
    except Exception:
        pass
    return not os.path.exists(p)

# Cache subfolders we must NEVER wipe — they hold Adobe CEP extension data (localStorage),
# where panels like ZH Script Studio keep their license/activation + settings. Wiping
# ~/Library/Caches blindly logs the user out of every CEP extension.
# NOTE: "Adobe" is deliberately NOT here — ~/Library/Caches/Adobe is pure regenerable
# render cache (After Effects disk cache alone can be tens of GB) and holds no license.
# The CEP license lives in the separate CSXS / com.adobe.cep entries, which we keep.
CACHE_PROTECT = ["CSXS", "com.adobe.cep", "com.adobe.csxs", "cep"]

def clear_contents(path, protect=None):
    """Delete a dir's contents with native rm, time-bounded so a busy/locked cache
    (e.g. a running browser) can never freeze the app. `protect` = top-level entry
    names to KEEP (e.g. Adobe CEP extension data → preserves activation keys)."""
    p = str(path)
    if not os.path.isdir(p):
        return
    try:
        if protect:
            # rm every top-level entry EXCEPT the protected names (CEP/Adobe extension data).
            conds = "".join(f' ! -name "{n}"' for n in protect)
            subprocess.run(["bash", "-c",
                f'find "$0" -mindepth 1 -maxdepth 1{conds} -exec rm -rf {{}} + 2>/dev/null', p],
                timeout=180)
        else:
            # remove visible + hidden entries INSIDE the dir, keep the dir itself
            subprocess.run(["bash", "-c", 'rm -rf "$0"/* "$0"/.[!.]* "$0"/..?* 2>/dev/null', p],
                           timeout=180)
    except Exception:
        pass

# ── App uninstaller ─────────────────────────────────────────────────────
APP_DIRS = ["/Applications", str(HOME/"Applications")]
LEFTOVER_DIRS = [
    HOME/"Library/Caches", HOME/"Library/Preferences", HOME/"Library/Application Support",
    HOME/"Library/Containers", HOME/"Library/Group Containers", HOME/"Library/Logs",
    HOME/"Library/Saved Application State", HOME/"Library/LaunchAgents",
    HOME/"Library/Application Scripts", HOME/"Library/HTTPStorages", HOME/"Library/WebKit",
]

def list_apps():
    apps = []
    for d in APP_DIRS:
        if not os.path.isdir(d): continue
        for e in sorted(os.listdir(d)):
            if e.endswith(".app"):
                apps.append((e[:-4], os.path.join(d, e)))
    return apps

def bundle_id(app_path):
    try:
        with open(os.path.join(app_path, "Contents/Info.plist"), "rb") as f:
            return plistlib.load(f).get("CFBundleIdentifier", "")
    except Exception:
        return ""

# Vendor prefixes we must NEVER delete as "leftovers" — protects OS + major apps' prefs/caches.
# (Premiere keyboard shortcuts live in com.adobe.* prefs; CEP cache under Adobe/. A loose
#  substring match used to nuke these when uninstalling an unrelated app.)
PROTECTED_PREFIXES = (
    "com.apple.", "apple", "com.adobe.", "adobe", "com.microsoft.", "microsoft",
    "com.google.", "google", "cep", "crashreporter", "mobilesync",
)

def app_leftovers(app_name, app_path):
    bid = (bundle_id(app_path) or "").lower()
    name_l = app_name.lower().replace(" ", "")
    specific_name = len(name_l) >= 4          # too-short names match everything → skip name matching
    hits = []
    for d in LEFTOVER_DIRS:
        if not d.exists(): continue
        try:
            for e in os.listdir(d):
                el = e.lower()
                ell = el.replace(" ", "")
                # PROTECT: never touch a vendor's files unless THIS app's bundle id IS that vendor.
                if any(ell.startswith(p) for p in PROTECTED_PREFIXES) and not (bid and ell.startswith(bid)):
                    continue
                match = False
                # 1) bundle-id match (reliable + unique): exact, or prefixed (".plist", subfolder, "-")
                if bid and (ell == bid or ell.startswith(bid + ".") or ell.startswith(bid + "-") or ell.startswith(bid + " ")):
                    match = True
                # 2) name match ONLY if the entry starts with the (specific) app name — no loose substring.
                elif specific_name and (ell == name_l or ell.startswith(name_l + ".") or ell.startswith(name_l + "-")):
                    match = True
                if match and not path_protected(d/e, hard=False):
                    hits.append(d/e)
        except OSError:
            pass
    return hits

# ── Duplicate finder ────────────────────────────────────────────────────
def _quickhash(fp):
    h = hashlib.md5()
    try:
        with open(fp, "rb") as f:
            h.update(f.read(65536))           # first 64 KB — fast, good enough
    except OSError:
        return None
    return h.hexdigest()

def find_duplicates(dirs, min_size=1024*1024):
    by_size = {}
    for d in dirs:
        if not os.path.isdir(d): continue
        for root, _, files in os.walk(d, onerror=lambda e: None):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if os.path.islink(fp): continue
                    if path_protected(fp, hard=False): continue
                    sz = os.path.getsize(fp)
                    if sz >= min_size: by_size.setdefault(sz, []).append(fp)
                except OSError:
                    pass
    groups = []
    for sz, paths in by_size.items():
        if len(paths) < 2: continue
        by_hash = {}
        for p in paths:
            hh = _quickhash(p)
            if hh: by_hash.setdefault(hh, []).append(p)
        for hh, ps in by_hash.items():
            if len(ps) > 1: groups.append((sz, ps))
    groups.sort(key=lambda g: g[0]*len(g[1]), reverse=True)
    return groups

# ── Old installers in ~/Downloads ──────────────────────────────────────
def find_old_installers():
    """.dmg/.pkg/.iso in ~/Downloads older than INSTALLER_AGE_D days. Excludes
    anything the user protected. Returns [(path, size, mtime)] newest first."""
    d = HOME/"Downloads"
    if not d.is_dir(): return []
    cutoff = time.time() - INSTALLER_AGE_D*86400
    out = []
    try:
        for e in os.listdir(d):
            if not e.lower().endswith(INSTALLER_EXT): continue
            fp = d/e
            try:
                if fp.is_symlink() or not fp.is_file(): continue
                st = fp.stat()
                if st.st_mtime > cutoff: continue
                if path_protected(fp): continue
                out.append((str(fp), st.st_size, st.st_mtime))
            except OSError:
                pass
    except OSError:
        pass
    out.sort(key=lambda x: x[2], reverse=True)
    return out

# ── iOS / iPadOS device backups ────────────────────────────────────────
def find_ios_backups():
    """Each subfolder of MobileSync/Backup is one device backup. Reads Info.plist
    for the device name + last-backup date. Returns [(path, size, label, when)]."""
    if not IOS_BACKUP_DIR.is_dir(): return []
    out = []
    for e in sorted(os.listdir(IOS_BACKUP_DIR)):
        bp = IOS_BACKUP_DIR/e
        if not bp.is_dir() or path_protected(bp, hard=False): continue
        name, when = "iOS device", ""
        try:
            with open(bp/"Info.plist", "rb") as f:
                info = plistlib.load(f)
            name = info.get("Device Name") or info.get("Product Name") or name
            lbd  = info.get("Last Backup Date")
            if lbd: when = lbd.strftime("%d %b %Y") if hasattr(lbd, "strftime") else str(lbd)
            pt = info.get("Product Type") or ""
            if pt: name = f"{name} ({pt})"
        except Exception:
            pass
        out.append((str(bp), dir_size(bp), name, when))
    return out

def fda_granted():
    """True ONLY if we can actually read a protected TCC database (i.e. Full Disk Access is on).
    Any failure (denied, missing, other) → False so the Enable-FDA banner shows and the user can grant it."""
    for p in (HOME/"Library/Application Support/com.apple.TCC/TCC.db",
              Path("/Library/Application Support/com.apple.TCC/TCC.db")):
        try:
            with open(p, "rb") as f:
                f.read(1)
            return True            # read a protected DB → FDA is granted
        except PermissionError:
            return False           # explicitly denied → not granted → show banner
        except Exception:
            continue               # missing / other on this path → try the next, else fall through
    return False                  # couldn't confirm → show the banner so the user can grant access

def free_mem_bytes():
    """Approx available memory (free + inactive + speculative pages)."""
    try:
        ps = int(subprocess.run(["sysctl","-n","hw.pagesize"], capture_output=True, text=True).stdout.strip() or 16384)
        out = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
        free = inact = spec = 0
        for ln in out.splitlines():
            if "Pages free" in ln: free = int(ln.split(":")[1].strip().rstrip("."))
            elif "Pages inactive" in ln: inact = int(ln.split(":")[1].strip().rstrip("."))
            elif "Pages speculative" in ln: spec = int(ln.split(":")[1].strip().rstrip("."))
        return (free + inact + spec) * ps
    except Exception:
        return 0

def run_admin(shell_cmd):
    """Run a shell command with a macOS admin-password prompt."""
    sc = shell_cmd.replace('"', '\\"')
    r = subprocess.run(["osascript","-e",
        f'do shell script "{sc}" with administrator privileges'], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()

# ── Clean categories ────────────────────────────────────────────────────
CATEGORIES = {
    "system":  ("🧹", "System Junk", "caches · logs",
                [HOME/"Library/Caches", HOME/"Library/Logs"]),
    "browser": ("🌐", "Browser Caches", "Chrome · Safari · Firefox",
                [HOME/"Library/Caches/Google/Chrome", HOME/"Library/Caches/com.apple.Safari",
                 HOME/"Library/Caches/Firefox", HOME/"Library/Caches/BraveSoftware",
                 HOME/"Library/Caches/com.microsoft.edgemac",
                 HOME/"Library/Application Support/Google/Chrome/Default/Cache",
                 HOME/"Library/Application Support/Google/Chrome/Default/Code Cache",
                 HOME/"Library/Application Support/Google/Chrome/Default/GPUCache"]),
    "dev":     ("⚙️", "Developer Junk", "npm · pip · brew · Xcode",
                [HOME/".npm/_cacache", HOME/"Library/Caches/Yarn", HOME/"Library/Caches/pip",
                 HOME/"Library/Caches/Homebrew", HOME/"Library/Caches/CocoaPods",
                 HOME/"Library/Developer/Xcode/DerivedData",
                 HOME/"Library/Developer/CoreSimulator/Caches",
                 HOME/"Library/Developer/Xcode/iOS DeviceSupport"]),
    # App caches for the usual space hogs. Contents only — you stay signed in, the
    # app just re-caches. NOT the app's Application Support (that holds real data).
    "apps":    ("💬", "App Caches", "Slack · Spotify · Zoom · Discord · Teams",
                [HOME/"Library/Caches/com.tinyspeck.slackmacgap",
                 HOME/"Library/Application Support/Slack/Cache",
                 HOME/"Library/Application Support/Slack/Service Worker/CacheStorage",
                 HOME/"Library/Caches/com.spotify.client",
                 HOME/"Library/Application Support/Spotify/PersistentCache",
                 HOME/"Library/Caches/us.zoom.xos",
                 HOME/"Library/Caches/com.hnc.Discord",
                 HOME/"Library/Application Support/discord/Cache",
                 HOME/"Library/Application Support/Microsoft/Teams/Cache",
                 HOME/"Library/Caches/com.microsoft.teams2",
                 HOME/"Library/Caches/com.adobe.acc.AdobeDesktopService",
                 HOME/"Library/Caches/com.apple.dt.Xcode"]),
    # Adobe Premiere/AE media cache (cfa/pek/peak) — big space, regenerates. Does NOT touch
    # Adobe CEP extension data (licenses) — that lives under Adobe/CEP, not Common/Media Cache.
    "adobe":   ("🎬", "Adobe Media Cache", "Premiere · After Effects",
                [HOME/"Library/Application Support/Adobe/Common/Media Cache Files",
                 HOME/"Library/Application Support/Adobe/Common/Media Cache",
                 HOME/"Library/Application Support/Adobe/Common/Peak Files"]),
}
SCAN_DIRS = [HOME/"Downloads", HOME/"Desktop", HOME/"Documents", HOME/"Movies"]
BIG_THRESHOLD = 100 * 1024 * 1024

# ── Old installers in Downloads ─────────────────────────────────────────
INSTALLER_EXT   = (".dmg", ".pkg", ".mpkg", ".iso")
INSTALLER_AGE_D = 14          # only offer ones older than this — recent = probably still needed
IOS_BACKUP_DIR  = HOME/"Library/Application Support/MobileSync/Backup"

# ring segment + card accent per category — leaf→gold ramp (leaf-800/600/400, gold)
SEG = {"system":"#2b6627", "browser":"#388837", "dev":"#4e9a49",
       "apps":"#7bb36f", "adobe":"#c9922f"}

CARD_HELP = {
    "system":  "App caches & log files macOS rebuilds automatically. Safe to delete — frees space, apps just re-cache.",
    "browser": "Cached web data for Chrome/Safari/Firefox. You stay logged in; pages just re-download once.",
    "dev":     "Build caches from npm, pip, Homebrew, Xcode. Safe — they regenerate on next build/install.",
    "apps":    "Cache folders for Slack, Spotify, Zoom, Discord, Teams, Creative Cloud, Xcode. Contents only — you stay signed in, the app re-caches. Never touches their settings or data.",
    "adobe":   "Premiere/After Effects media cache (cfa/pek/peak files). Safe to clear — Adobe rebuilds them on next preview. Does NOT remove your extension licenses or settings.",
}


# ── hover tooltip ───────────────────────────────────────────────────────
class Tip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
    def _show(self, _e):
        if self.tip or not self.text: return
        x = self.w.winfo_rootx() + 24
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.tip = tk.Toplevel(self.w); self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg=C["TEXT"], fg="#ffffff", font=(UIFONT, 10),
                 padx=9, pady=6, justify="left", wraplength=280).pack()
    def _hide(self, _e):
        if self.tip: self.tip.destroy(); self.tip = None


# ════════════════════════════════════════════════════════════════════════
class Cleaner(tk.Tk):
    def __init__(self):
        super().__init__()
        # Brand type is Outfit / Bricolage Grotesque; neither ships with macOS, so
        # Inter (same humanist-geometric feel) comes first, system font backs it up.
        global UIFONT, HEADFONT, MONO
        UIFONT = _pick_family(["Inter", "Outfit", ".AppleSystemUIFont", "SF Pro Text",
                               "SF Pro Display", "Helvetica Neue"], "SF Pro Text")
        HEADFONT = _pick_family(["Bricolage Grotesque", "Outfit", "Inter",
                                 "SF Pro Display", "Helvetica Neue"], UIFONT)
        MONO   = _pick_family(["JetBrains Mono", "SF Mono", "Menlo"], "SF Mono")
        try:
            (Path.home()/".config/zhmaccleaner").mkdir(parents=True, exist_ok=True)
            (Path.home()/".config/zhmaccleaner/fontcheck.txt").write_text(
                f"UIFONT={UIFONT}\nHEADFONT={HEADFONT}\nMONO={MONO}\n")
        except Exception: pass
        self.title("ZH MacCleaner")
        self.geometry("880x760"); self.resizable(False, False)   # fixed size — fits all buttons
        self.configure(bg=C["BG"])
        self.q = queue.Queue()
        self.sizes = {}            # key -> bytes
        self.vars = {}             # key -> BooleanVar
        self.size_lbls = {}        # key -> Label
        self.big_files = []
        self.big_vars = {}
        self.busy = False
        self.logo_img = None
        self.nav_btns = {}
        self.views = {}

        self.lic = {"key": "", "plan": "free", "valid": False, "checked": 0}
        self._load_license()

        self._build()
        self.after(80, self._pump)
        self.after(300, self.scan_all)         # auto-scan on launch
        self.after(2500, lambda: self.check_updates(silent=True))  # quiet update check
        self.after(1500, self._reverify_license)   # refresh Pro status online
        self.after(4000, self._maybe_review)       # ask for a review after a few days
        self._trash_size()

    # ── UI ──
    def _build(self):
        # Header: Z-mark icon + app name
        head = tk.Frame(self, bg=C["HEADER"], height=80); head.pack(fill="x"); head.pack_propagate(False)
        icon_path = APP_DIR/"assets"/"icon.png"
        if icon_path.exists():
            try:  # Tk 9.0 loads PNG natively
                img = tk.PhotoImage(file=str(icon_path))
                self.logo_img = img.subsample(max(1, img.height() // 44), max(1, img.height() // 44))
                tk.Label(head, image=self.logo_img, bg=C["HEADER"]).pack(side="left", padx=(18,12), pady=16)
            except Exception:
                pass
        name = tk.Frame(head, bg=C["HEADER"]); name.pack(side="left")
        titlerow = tk.Frame(name, bg=C["HEADER"]); titlerow.pack(anchor="w")
        tk.Label(titlerow, text="ZH MacCleaner", bg=C["HEADER"], fg=C["MAROON"],
                 font=(HEADFONT, 20, "bold")).pack(side="left")
        tk.Label(titlerow, text=f"  v{APP_VERSION}", bg=C["HEADER"], fg=C["MUTED"],
                 font=(UIFONT, 11, "bold")).pack(side="left", pady=(6,0))
        tk.Label(name, text="keep your Mac clean", bg=C["HEADER"], fg=C["MUTED"],
                 font=(UIFONT, 10)).pack(anchor="w", pady=(1,0))
        # subtle bottom divider
        tk.Frame(self, bg=C["BORDER"], height=1).pack(fill="x")

        body = tk.Frame(self, bg=C["BG"]); body.pack(fill="both", expand=True)

        # Sidebar
        side = tk.Frame(body, bg=C["SIDEBAR"], width=180); side.pack(side="left", fill="y"); side.pack_propagate(False)
        self.active_view = None
        nav = [("cleanup","Cleanup","🧹"), ("large","Large Files","📦"),
               ("installers","Old Installers","📥"), ("iosbackup","iOS Backups","📱"),
               ("uninstall","Uninstaller","🗑️"), ("dupes","Duplicates","👯"),
               ("maint","Maintenance","🛠"), ("settings","Settings","⚙️"),
               ("license","Pro","⭐"), ("help","Help","ℹ️")]
        for key, label, ico in nav:
            b = tk.Label(side, text=f"  {ico}  {label}", bg=C["SIDEBAR"], fg=C["TEXT"],
                         font=(UIFONT, 12), anchor="w", cursor="pointinghand", padx=12, pady=8)
            b.pack(fill="x", padx=8, pady=1)
            b.bind("<Button-1>", lambda e,k=key: self.show_view(k))
            b.bind("<Enter>", lambda e,k=key,w=b: (w.config(bg="#d4f1cb") if k!=self.active_view else None))
            b.bind("<Leave>", lambda e,k=key,w=b: (w.config(bg=C["SIDEBAR"]) if k!=self.active_view else None))
            self.nav_btns[key] = b
        tk.Label(side, text=f"v{APP_VERSION} · safe mode", bg=C["SIDEBAR"], fg=C["BORDER"],
                 font=(UIFONT, 9)).pack(side="bottom", pady=12)

        # Content area
        self.content = tk.Frame(body, bg=C["BG"]); self.content.pack(side="left", fill="both", expand=True)
        self._build_cleanup()
        self._build_large()
        self._build_installers()
        self._build_iosbackup()
        self._build_uninstaller()
        self._build_duplicates()
        self._build_maintenance()
        self._build_settings()
        self._build_license()
        self._build_help()

        # Status bar
        self.status = tk.Label(self, text="Scanning…", bg=C["HEADER"], fg=C["MUTED"],
                               anchor="w", font=(UIFONT, 10), padx=16, pady=6)
        self.status.pack(fill="x", side="bottom")

        self.show_view("cleanup")

    # ── Cleanup view ──
    def _build_cleanup(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["cleanup"] = v

        # Full Disk Access banner (only if not granted)
        if not fda_granted():
            ban = tk.Frame(v, bg=C["SURF2"], highlightbackground=C["MAROON"], highlightthickness=1)
            ban.pack(fill="x", padx=22, pady=(12,0))
            ban.columnconfigure(1, weight=1)
            tk.Label(ban, text="🔒", bg=C["SURF2"], font=(UIFONT, 18)
                     ).grid(row=0, column=0, rowspan=2, padx=(12,6), pady=10)
            tk.Label(ban, text="Enable Full Disk Access", bg=C["SURF2"], fg=C["MAROON"], anchor="w",
                     font=(UIFONT, 12, "bold")).grid(row=0, column=1, sticky="w", pady=(10,0))
            tk.Label(ban, text="Lets ZH MacCleaner read & clear all caches.", bg=C["SURF2"],
                     fg=C["MUTED"], anchor="w", font=(UIFONT, 10)).grid(row=1, column=1, sticky="w", pady=(0,10))
            _chip(ban, "Open Settings", self.open_fda, "leaf").grid(row=0, column=2, rowspan=2, padx=12)

        # ── pack the fixed chrome (buttons, foot) BEFORE the expanding middle, or
        #    Tk's packer squeezes a side="bottom" widget out when content is tall. ──
        bar = tk.Frame(v, bg=C["BG"]); bar.pack(fill="x", padx=22, pady=12, side="bottom")
        self.rescan_btn = self._btn(bar, "↻  Rescan", self.scan_all, "ghost"); self.rescan_btn.pack(side="left")
        self.clean_btn  = self._btn(bar, "✦  Clean Selected", self.clean_sel, "gold"); self.clean_btn.pack(side="left", padx=8)
        self.trash_btn  = self._btn(bar, "🗑  Empty Trash", self.empty_trash, "ghost"); self.trash_btn.pack(side="right")

        foot = tk.Frame(v, bg=C["BG"]); foot.pack(fill="x", padx=22, pady=(4,0), side="bottom")
        self.trash_lbl = tk.Label(foot, text="🗑  Trash: …", bg=C["BG"], fg=C["MUTED"],
                                  font=(UIFONT, 11)); self.trash_lbl.pack(side="left")
        self.stat_lbl = tk.Label(foot, text="", bg=C["BG"], fg=C["MAROON2"],
                                 font=(UIFONT, 11, "bold")); self.stat_lbl.pack(side="right")
        self._refresh_stat()

        # Gauge
        top = tk.Frame(v, bg=C["BG"]); top.pack(fill="x", pady=(6,2))
        self.gauge = tk.Canvas(top, width=150, height=150, bg=C["BG"], highlightthickness=0)
        self.gauge.pack()
        self._draw_gauge()

        # Category cards — in a borderless scroller so they can never push the buttons off-screen
        mid = self._scroller(v, bordered=False, bg=C["BG"])
        for key,(ico,name,sub,paths) in CATEGORIES.items():
            card = tk.Frame(mid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.pack(fill="x", pady=3); card.columnconfigure(3, weight=1)
            tk.Frame(card, bg=SEG[key], width=4).grid(row=0, column=0, rowspan=2, sticky="ns")  # accent bar
            var = tk.BooleanVar(value=True); self.vars[key] = var
            tk.Checkbutton(card, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=1, rowspan=2, padx=(12,4), pady=14)
            tk.Label(card, text=ico, bg=C["SURF"], font=(UIFONT, 18)
                     ).grid(row=0, column=2, rowspan=2, padx=6)
            tk.Label(card, text=name, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(HEADFONT, 14, "bold")).grid(row=0, column=3, sticky="w", pady=(12,0))
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 10)).grid(row=1, column=3, sticky="w", pady=(0,12))
            szl = tk.Label(card, text="…", bg=C["SURF"], fg=C["GOLD"],
                           font=(MONO, 15, "bold")); szl.grid(row=0, column=4, rowspan=2, padx=20)
            self.size_lbls[key] = szl
            Tip(card, CARD_HELP[key])
            for wdg in [card] + list(card.winfo_children()):
                wdg.bind("<Enter>", lambda e,c=card: c.config(highlightbackground=C["MAROON2"]))
                wdg.bind("<Leave>", lambda e,c=card: c.config(highlightbackground=C["BORDER"]))

    # ── Large files view ──
    def _build_large(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["large"] = v
        top = tk.Frame(v, bg=C["BG"]); top.pack(fill="x", padx=22, pady=(18,8))
        tk.Label(top, text="Files > 100 MB in Downloads · Desktop · Documents · Movies",
                 bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 11)).pack(side="left")
        self.find_btn = self._btn(top, "🔍  Find", self.scan_big, "gold"); self.find_btn.pack(side="right")

        wrap = tk.Frame(v, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=22, pady=6)
        self.bcanvas = tk.Canvas(wrap, bg=C["SURF"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.bcanvas.yview)
        self.binner = tk.Frame(self.bcanvas, bg=C["SURF"])
        self.binner.bind("<Configure>", lambda e: self.bcanvas.configure(scrollregion=self.bcanvas.bbox("all")))
        self.bwin = self.bcanvas.create_window((0,0), window=self.binner, anchor="nw")
        self.bcanvas.bind("<Configure>", lambda e: self.bcanvas.itemconfig(self.bwin, width=e.width))
        self.bcanvas.configure(yscrollcommand=sb.set)
        self.bcanvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        self.trash_sel_btn = self._btn(v, "🗑  Move Selected to Trash", self.trash_big, "gold")
        self.trash_sel_btn.pack(anchor="e", padx=22, pady=10)

    def _btn(self, parent, text, cmd, kind="gold"):
        return RoundedButton(parent, text, cmd, kind=kind, font=(UIFONT, 12, "bold"))

    def open_fda(self):
        subprocess.run(["open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
        self.q.put(("status", "Add ZH MacCleaner in the list, then relaunch the app."))

    def _draw_gauge(self, frac=None, total=None):
        g = self.gauge; g.delete("all")
        x0,y0,x1,y1 = 14,14,136,136; cx,cy = 75,75
        g.create_oval(x0,y0,x1,y1, outline="#d4f1cb", width=9)   # track (leaf-100)
        real = sum(self.sizes.values())
        if frac is None:                      # final state — segmented ring
            if real > 0:
                start = 90.0
                for k in CATEGORIES:
                    val = self.sizes.get(k, 0)
                    if val <= 0: continue
                    g.create_arc(x0,y0,x1,y1, start=start, extent=-359.0*(val/real),
                                 style="arc", outline=SEG[k], width=10)
                    start += -359.0*(val/real)
            shown = real
        else:                                 # animating — single growing arc
            if frac > 0:
                g.create_arc(x0,y0,x1,y1, start=90, extent=-359.0*min(frac,1.0),
                             style="arc", outline=C["MAROON"], width=10)
            shown = real if total is None else total
        txt = human(shown) if (real > 0 or total is not None) else "—"
        fs = 20 if len(txt) <= 7 else (16 if len(txt) <= 9 else 14)
        g.create_text(cx, cy-9, text=txt, fill=C["TEXT"], font=(HEADFONT, fs, "bold"))
        g.create_text(cx, cy+15, text="RECLAIMABLE", fill=C["MUTED"], font=(UIFONT, 8, "bold"))

    def _animate_gauge(self):
        target = sum(self.sizes.values())
        if target <= 0:
            self._draw_gauge(); return
        steps = 24
        def step(i=[0]):
            i[0] += 1
            e = 1 - (1 - i[0]/steps)**3          # ease-out
            if i[0] >= steps:
                self._draw_gauge()                # settle to real segmented ring
            else:
                self._draw_gauge(frac=e, total=target*e)
                self.after(20, step)
        step()

    def show_view(self, name):
        # Pro gate
        if name in PRO_FEATURES and not self.is_pro():
            feat = {"uninstall":"App Uninstaller","dupes":"Duplicate Finder","maint":"Maintenance"}.get(name,"This")
            if hasattr(self, "lic_ctx"):
                self.lic_ctx.config(text=f"🔒  {feat} is a Pro feature — activate a license to unlock it.")
            self._refresh_license_ui()
            name = "license"
        elif hasattr(self, "lic_ctx"):
            self.lic_ctx.config(text="")
        self.active_view = name
        for v in self.views.values(): v.pack_forget()
        self.views[name].pack(fill="both", expand=True)
        if name == "uninstall": self.load_apps()
        elif name == "installers" and not self.instl_files and not self.busy: self.scan_installers()
        elif name == "settings": self._render_exclusions()
        for k,b in self.nav_btns.items():
            if k == name: b.config(bg=C["MAROON"], fg="#ffffff", font=(UIFONT, 12, "bold"))
            else:         b.config(bg=C["SIDEBAR"], fg=C["TEXT"], font=(UIFONT, 12))

    # ── queue pump ──
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if   kind == "status": self.status.config(text=payload)
                elif kind == "size":   self._set_size(*payload)
                elif kind == "gauge":  self._draw_gauge()
                elif kind == "trash":  self.trash_lbl.config(text=payload)
                elif kind == "stat":   self._refresh_stat()
                elif kind == "big":    self._render_big(payload)
                elif kind == "busy":   self._set_busy(payload)
                elif kind == "instl":  self._render_installers(payload)
                elif kind == "iosbk":  self._render_iosbackup(payload)
                elif kind == "apps":   self._render_apps(payload)
                elif kind == "uninstall_confirm": self._confirm_uninstall(*payload)
                elif kind == "dupes":  self._render_dupes(payload)
                elif kind == "rescan_dupes": self.scan_dupes()
                elif kind == "update": self._show_update(*payload)
                elif kind == "gauge_anim": self._animate_gauge()
                elif kind == "maint_done": self._maint_done(*payload)
                elif kind == "clean_done":
                    messagebox.showinfo("ZH MacCleaner", f"✅ Cleanup complete.\n\nFreed about {human(payload)}.")
                elif kind == "license_changed": self._refresh_license_ui()
                elif kind == "license_result":
                    ok, msg = payload
                    (messagebox.showinfo if ok else messagebox.showwarning)("ZH MacCleaner — License", msg)
                    self._refresh_license_ui()
                    if ok and self.active_view == "license": self.show_view("cleanup")
        except queue.Empty:
            pass
        self.after(80, self._pump)

    def _set_busy(self, b):
        self.busy = b
        st = "disabled" if b else "normal"
        for x in ("rescan_btn","clean_btn","trash_btn","find_btn","trash_sel_btn",
                  "instl_btn","trash_instl_btn","iosbk_btn","trash_iosbk_btn"):
            try: getattr(self, x).config(state=st)
            except Exception: pass

    def _set_size(self, key, n):
        self.sizes[key] = n
        self.size_lbls[key].config(text=human(n))
        total = sum(self.sizes.values())
        self._draw_gauge()

    def _trash_size(self):
        threading.Thread(target=lambda: self.q.put(("trash", f"🗑  Trash: {human(dir_size(HOME/'.Trash'))}")),
                         daemon=True).start()

    def _refresh_stat(self):
        if not hasattr(self, "stat_lbl"): return
        s = load_stats()
        f = int(s.get("freed", 0))
        self.stat_lbl.config(text=(f"♻  {human(f)} reclaimed all-time · {s.get('runs',0)} runs" if f else ""))

    # ── scan ──
    def scan_all(self):
        if self.busy: return
        for l in self.size_lbls.values(): l.config(text="…")
        self.q.put(("busy", True)); self.q.put(("status","Scanning caches…"))
        def run():
            for key,(ico,name,sub,paths) in CATEGORIES.items():
                tot = sum(dir_size(p) for p in paths if p.exists())
                self.q.put(("size",(key,tot)))
            self.q.put(("gauge_anim", None))     # animated reveal
            self.q.put(("status","Scan complete. Review sizes, then Clean Selected."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def clean_sel(self):
        if self.busy: return
        picks = [k for k,v in self.vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH Cleaner","Nothing selected."); return
        est = sum(self.sizes.get(k,0) for k in picks)
        names = "\n".join("• "+CATEGORIES[k][1] for k in picks)
        if not messagebox.askyesno("Clean these?",
            f"Delete cache/log contents for:\n\n{names}\n\n≈ {human(est)} freed. "
            f"These regenerate automatically.\n\nContinue?"): return
        self.q.put(("busy", True))
        def run():
            freed = 0
            remaining_tot = 0
            try:
                for k in picks:
                    self.q.put(("status", f"Cleaning {CATEGORIES[k][1]}…"))   # live per-category
                    before = sum(dir_size(p) for p in CATEGORIES[k][3] if p.exists())
                    for p in CATEGORIES[k][3]:
                        # System Junk clears ~/Library/Caches → protect Adobe CEP extension data
                        # (licenses) from being wiped. Other categories clear fully.
                        if p.exists(): clear_contents(p, protect=CACHE_PROTECT if k == "system" else None)
                    # Re-measure ACTUAL remaining — never report a fake "0".
                    # What stays = protected Adobe data, files locked by running apps,
                    # or cache an open app rebuilt instantly. Honest numbers only.
                    after = sum(dir_size(p) for p in CATEGORIES[k][3] if p.exists())
                    freed += max(0, before - after)
                    remaining_tot += after
                    self.q.put(("size", (k, after)))
            except Exception as e:
                self.q.put(("status", f"⚠ Clean error: {e}"))
            finally:                                                          # ALWAYS finish
                if remaining_tot > 5 * 1024 * 1024:   # >5 MB still there → explain why (both real causes)
                    if freed < remaining_tot * 0.2:
                        # Barely anything went down. Two real causes: (a) an open app (Chrome/Safari/
                        # Adobe) rebuilds its cache the instant we delete it, or (b) macOS is blocking
                        # deletes without Full Disk Access. State both so the client can fix it.
                        tip = "" if fda_granted() else (" • or grant Full Disk Access: System Settings → "
                              "Privacy & Security → Full Disk Access → + → add ZH MacCleaner, then reopen it")
                        msg = (f"⚠ Freed {human(freed)} — most of it came back. Quit Chrome / Safari / "
                               f"Adobe (they rebuild cache live) & re-clean" + tip + ".")
                    else:
                        msg = (f"✅ Freed {human(freed)}. {human(remaining_tot)} still in use — "
                               f"quit Chrome/Safari/Adobe (they rebuild cache live) & re-clean. "
                               f"Adobe extension data is protected on purpose.")
                else:
                    msg = f"✅ Cleaned. Freed {human(freed)}."
                bump_stats(freed)
                self.q.put(("status", msg))
                self.q.put(("clean_done", freed))
                self.q.put(("busy", False))
                self.q.put(("stat", None))
                self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    def empty_trash(self):
        if self.busy: return
        if not messagebox.askyesno("Empty Trash","Permanently empty the macOS Trash?"): return
        self.q.put(("busy", True))
        def run():
            subprocess.run(["osascript","-e",'tell application "Finder" to empty trash'], capture_output=True)
            self.q.put(("trash","🗑  Trash: 0 B")); self.q.put(("status","✅ Trash emptied."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    # ── large files ──
    def scan_big(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status","Finding large files…"))
        def run():
            dirs = [str(d) for d in SCAN_DIRS if d.exists()]; found = []
            if dirs:
                mb = BIG_THRESHOLD//(1024*1024)
                try:
                    out = subprocess.run(["find"]+dirs+["-type","f","-size",f"+{mb}M"],
                                         capture_output=True, text=True, timeout=120)
                    for fp in out.stdout.splitlines():
                        try:
                            if os.path.islink(fp): continue
                            if path_protected(fp, hard=False): continue   # user's protected folders
                            st = os.stat(fp); found.append((fp, st.st_size, st.st_mtime))
                        except OSError: pass
                except Exception as e:
                    self.q.put(("status", f"find error: {e}"))
            found.sort(key=lambda x:x[1], reverse=True)
            self.q.put(("big", found[:200]))
            self.q.put(("status", f"Found {len(found)} file(s) > 100 MB."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _render_big(self, found):
        for w in self.binner.winfo_children(): w.destroy()
        self.big_files = found; self.big_vars = {}
        if not found:
            tk.Label(self.binner, text="No files > 100 MB found.", bg=C["SURF"], fg=C["MUTED"],
                     font=(UIFONT, 12)).pack(pady=24); return
        now = time.time()
        for fp,sz,mt in found:
            row = tk.Frame(self.binner, bg=C["SURF"]); row.pack(fill="x", padx=6, pady=2)
            row.columnconfigure(1, weight=1)
            var = tk.BooleanVar(value=False); self.big_vars[fp] = var
            tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=0, rowspan=2, sticky="w")
            nm = os.path.basename(fp); disp = (nm[:44]+"…") if len(nm)>45 else nm
            nml = tk.Label(row, text=disp, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 11, "bold")); nml.grid(row=0, column=1, sticky="w", padx=4)
            folder = os.path.dirname(fp); pdisp = ("…"+folder[-58:]) if len(folder)>59 else folder
            tk.Label(row, text=pdisp, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 9)).grid(row=1, column=1, sticky="w", padx=4)
            Tip(nml, fp)   # full path on hover — check before deleting
            tk.Label(row, text=f"{human(sz)} · {int((now-mt)/86400)}d", bg=C["SURF"], fg=C["GOLD"],
                     font=(MONO, 11)).grid(row=0, column=2, rowspan=2, sticky="e", padx=12)
            _chip(row, "↗ Reveal", lambda p=fp: self.reveal_in_finder(p), "ghost"
                  ).grid(row=0, column=3, rowspan=2, padx=(4,8))

    def reveal_in_finder(self, path):
        """Open Finder with the file selected so the user can verify it before deleting."""
        if os.path.exists(path):
            subprocess.run(["open", "-R", path], capture_output=True)
        else:
            self.q.put(("status", "File no longer exists."))

    def trash_big(self):
        if self.busy: return
        picks = [fp for fp,v in self.big_vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH Cleaner","No files selected."); return
        tot = sum(sz for fp,sz,_ in self.big_files if fp in picks)
        if not messagebox.askyesno("Move to Trash?",
            f"Move {len(picks)} file(s) ({human(tot)}) to Trash?\nRecoverable from Trash."): return
        self.q.put(("busy", True))
        def run():
            okp = [fp for fp in picks if not path_protected(fp, hard=False) and move_to_trash(fp)]
            fail = len(picks) - len(okp)
            bump_stats(sum(sz for fp,sz,_ in self.big_files if fp in okp))
            self.q.put(("big", [x for x in self.big_files if x[0] not in okp]))
            self.q.put(("status", f"✅ Moved {len(okp)} file(s) to Trash."
                        + (f" ⚠ {fail} couldn't be moved (in use / permission)." if fail else "")))
            self.q.put(("busy", False)); self.q.put(("stat", None)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ scrollable list helper ══
    def _scroller(self, parent, bordered=True, bg=None):
        bg = bg or C["SURF"]
        wrap = tk.Frame(parent, bg=bg,
                        highlightbackground=C["BORDER"], highlightthickness=1 if bordered else 0)
        wrap.pack(fill="both", expand=True, padx=22, pady=6)
        cv = tk.Canvas(wrap, bg=bg, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=bg)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0,0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        return inner

    def _title(self, parent, text, sub=""):
        f = tk.Frame(parent, bg=C["BG"]); f.pack(fill="x", padx=22, pady=(18,4))
        tk.Label(f, text=text, bg=C["BG"], fg=C["TEXT"], font=(HEADFONT, 17, "bold")).pack(anchor="w")
        if sub: tk.Label(f, text=sub, bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10)).pack(anchor="w")
        return f

    # ══ OLD INSTALLERS (in ~/Downloads) ══
    def _build_installers(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["installers"] = v
        f = self._title(v, "Old Installers",
                        f".dmg / .pkg / .iso in Downloads older than {INSTALLER_AGE_D} days — the app's already installed")
        self.instl_btn = self._btn(f, "🔍  Scan", self.scan_installers, "gold"); self.instl_btn.pack(side="right")
        self.instl_inner = self._scroller(v)
        self.instl_vars = {}
        self.instl_files = []
        self.trash_instl_btn = self._btn(v, "🗑  Move Selected to Trash", self.trash_installers, "gold")
        self.trash_instl_btn.pack(anchor="e", padx=22, pady=10)

    def scan_installers(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status", "Looking for old installers…"))
        threading.Thread(target=lambda: (
            self.q.put(("instl", find_old_installers())),
            self.q.put(("busy", False))), daemon=True).start()

    def _render_installers(self, found):
        for w in self.instl_inner.winfo_children(): w.destroy()
        self.instl_files = found; self.instl_vars = {}
        if not found:
            tk.Label(self.instl_inner, text="No old installers in Downloads. 🎉", bg=C["SURF"],
                     fg=C["MUTED"], font=(UIFONT, 12)).pack(pady=24)
            self.q.put(("status", "No old installers found.")); return
        now = time.time()
        for fp, sz, mt in found:
            row = tk.Frame(self.instl_inner, bg=C["SURF"]); row.pack(fill="x", padx=6, pady=2)
            row.columnconfigure(1, weight=1)
            var = tk.BooleanVar(value=True); self.instl_vars[fp] = var
            tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=0, sticky="w", padx=(4,6))
            nm = os.path.basename(fp); disp = (nm[:46]+"…") if len(nm) > 47 else nm
            nml = tk.Label(row, text=disp, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                           font=(UIFONT, 11)); nml.grid(row=0, column=1, sticky="w")
            Tip(nml, fp)
            tk.Label(row, text=f"{human(sz)} · {int((now-mt)/86400)}d old", bg=C["SURF"], fg=C["GOLD"],
                     font=(MONO, 10)).grid(row=0, column=2, sticky="e", padx=10)
            _chip(row, "↗", lambda p=fp: self.reveal_in_finder(p), "ghost"
                  ).grid(row=0, column=3, padx=(2,8))
        tot = sum(sz for _, sz, _ in found)
        self.q.put(("status", f"{len(found)} old installer(s) · {human(tot)}. Uncheck any you still need."))

    def trash_installers(self):
        if self.busy: return
        picks = [fp for fp, var in self.instl_vars.items() if var.get()]
        if not picks:
            messagebox.showinfo("ZH MacCleaner", "Nothing selected."); return
        tot = sum(sz for fp, sz, _ in self.instl_files if fp in picks)
        if not messagebox.askyesno("Move to Trash?",
            f"Move {len(picks)} installer file(s) ({human(tot)}) to Trash?\n\n"
            f"Only the .dmg/.pkg download is removed — the installed app is untouched. "
            f"Recoverable from Trash."): return
        self.q.put(("busy", True))
        def run():
            ok = [fp for fp in picks if not path_protected(fp) and move_to_trash(fp)]
            bump_stats(sum(sz for fp, sz, _ in self.instl_files if fp in ok))
            self.q.put(("instl", [x for x in self.instl_files if x[0] not in ok]))
            self.q.put(("status", f"✅ Moved {len(ok)} installer(s) to Trash."
                        + (f"  ⚠ {len(picks)-len(ok)} skipped (in use / protected)." if len(ok) != len(picks) else "")))
            self.q.put(("busy", False)); self.q.put(("stat", None)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ iOS / iPadOS BACKUPS ══
    def _build_iosbackup(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["iosbackup"] = v
        f = self._title(v, "iOS Backups",
                        "Local iPhone / iPad backups made by Finder. Often 20–80 GB.")
        self.iosbk_btn = self._btn(f, "🔍  Scan", self.scan_iosbackup, "gold"); self.iosbk_btn.pack(side="right")
        warn = tk.Frame(v, bg=C["SURF2"]); warn.pack(fill="x", padx=22, pady=(0,4))
        tk.Label(warn, text="⚠  Only delete a backup you don't need to restore from. "
                 "iCloud backups are separate and are NOT affected.", bg=C["SURF2"], fg=C["MAROON"],
                 font=(UIFONT, 10), wraplength=560, justify="left", anchor="w").pack(fill="x", padx=10, pady=6)
        self.iosbk_inner = self._scroller(v)
        self.iosbk_vars = {}
        self.iosbk_items = []
        self.trash_iosbk_btn = self._btn(v, "🗑  Move Selected to Trash", self.trash_iosbackup, "gold")
        self.trash_iosbk_btn.pack(anchor="e", padx=22, pady=10)

    def scan_iosbackup(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status", "Measuring iOS backups…"))
        threading.Thread(target=lambda: (
            self.q.put(("iosbk", find_ios_backups())),
            self.q.put(("busy", False))), daemon=True).start()

    def _render_iosbackup(self, items):
        for w in self.iosbk_inner.winfo_children(): w.destroy()
        self.iosbk_items = items; self.iosbk_vars = {}
        if not items:
            tk.Label(self.iosbk_inner, text="No local iOS backups on this Mac.", bg=C["SURF"],
                     fg=C["MUTED"], font=(UIFONT, 12)).pack(pady=24)
            self.q.put(("status", "No iOS backups found.")); return
        for bp, sz, name, when in items:
            row = tk.Frame(self.iosbk_inner, bg=C["SURF"]); row.pack(fill="x", padx=6, pady=3)
            row.columnconfigure(1, weight=1)
            var = tk.BooleanVar(value=False); self.iosbk_vars[bp] = var   # default OFF — precious
            tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=0, rowspan=2, sticky="w", padx=(4,6))
            tk.Label(row, text=name, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 11, "bold")).grid(row=0, column=1, sticky="w")
            tk.Label(row, text=(f"last backup {when}" if when else "date unknown"), bg=C["SURF"],
                     fg=C["MUTED"], anchor="w", font=(UIFONT, 9)).grid(row=1, column=1, sticky="w")
            tk.Label(row, text=human(sz), bg=C["SURF"], fg=C["GOLD"],
                     font=(MONO, 11, "bold")).grid(row=0, column=2, rowspan=2, sticky="e", padx=12)
        tot = sum(sz for _, sz, _, _ in items)
        self.q.put(("status", f"{len(items)} backup(s) · {human(tot)} total."))

    def trash_iosbackup(self):
        if self.busy: return
        picks = [bp for bp, var in self.iosbk_vars.items() if var.get()]
        if not picks:
            messagebox.showinfo("ZH MacCleaner", "Nothing selected."); return
        tot = sum(sz for bp, sz, _, _ in self.iosbk_items if bp in picks)
        names = "\n".join("• " + n for bp, _, n, _ in self.iosbk_items if bp in picks)
        if not messagebox.askyesno("Delete iOS backup?",
            f"Move {len(picks)} device backup(s) to Trash?\n\n{names}\n\n≈ {human(tot)}. "
            f"You will NOT be able to restore this iPhone/iPad from it afterwards "
            f"(unless you recover it from Trash first).\n\nContinue?"): return
        self.q.put(("busy", True))
        def run():
            ok = []
            for bp in picks:
                if bp.rstrip("/").startswith(str(IOS_BACKUP_DIR)) and not path_protected(bp, hard=False) \
                   and move_to_trash(bp):
                    ok.append(bp)
            bump_stats(sum(sz for bp, sz, _, _ in self.iosbk_items if bp in ok))
            self.q.put(("iosbk", [x for x in self.iosbk_items if x[0] not in ok]))
            self.q.put(("status", f"✅ Moved {len(ok)} backup(s) to Trash."
                        + (f"  ⚠ {len(picks)-len(ok)} skipped." if len(ok) != len(picks) else "")))
            self.q.put(("busy", False)); self.q.put(("stat", None)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ SETTINGS (exclusions + stats) ══
    def _build_settings(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["settings"] = v
        self._title(v, "Settings", "Protect folders from every scan")
        inner = self._scroller(v)
        tk.Label(inner, text="Protected folders", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(14,2))
        tk.Label(inner, text="ZH Cleaner will never list or delete anything inside these — "
                 "in Large Files, Duplicates, Installers or any other scan.",
                 bg=C["SURF"], fg=C["MUTED"], anchor="w", justify="left", font=(UIFONT, 10),
                 wraplength=520).pack(fill="x", padx=14, pady=(0,8))
        self.excl_inner = tk.Frame(inner, bg=C["SURF"]); self.excl_inner.pack(fill="x", padx=14)
        _chip(inner, "＋  Add folder…", self._add_exclusion, "leaf").pack(anchor="w", padx=14, pady=12)

        tk.Frame(inner, bg=C["BORDER"], height=1).pack(fill="x", padx=14, pady=8)
        tk.Label(inner, text="Lifetime stats", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(6,2))
        self.stats_detail = tk.Label(inner, text="", bg=C["SURF"], fg=C["MUTED"], anchor="w",
                                     font=(UIFONT, 11), justify="left")
        self.stats_detail.pack(fill="x", padx=14, pady=(0,4))
        _chip(inner, "Reset counter", self._reset_stats, "ghost").pack(anchor="w", padx=14, pady=(4,16))
        self._render_exclusions()

    def _render_exclusions(self):
        for w in self.excl_inner.winfo_children(): w.destroy()
        paths = load_settings().get("exclusions", [])
        if not paths:
            tk.Label(self.excl_inner, text="Nothing protected yet.", bg=C["SURF"], fg=C["MUTED"],
                     font=(UIFONT, 10)).pack(anchor="w", pady=4)
        for p in paths:
            row = tk.Frame(self.excl_inner, bg=C["SURF"]); row.pack(fill="x", pady=2)
            row.columnconfigure(0, weight=1)
            tk.Label(row, text=p.replace(str(HOME), "~"), bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 10)).grid(row=0, column=0, sticky="w")
            _chip(row, "Remove", lambda x=p: self._remove_exclusion(x), "ghost").grid(row=0, column=1)
        s = load_stats()
        if hasattr(self, "stats_detail"):
            self.stats_detail.config(text=f"Space reclaimed:  {human(int(s.get('freed',0)))}\n"
                                          f"Cleanups run:  {s.get('runs',0)}")

    def _add_exclusion(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="Protect a folder from ZH Cleaner", initialdir=str(HOME))
        if not d: return
        s = load_settings(); ex = s.get("exclusions", [])
        if d not in ex: ex.append(d); s["exclusions"] = ex; save_settings(s)
        self._render_exclusions()
        self.q.put(("status", f"Protected: {d.replace(str(HOME),'~')}"))

    def _remove_exclusion(self, p):
        s = load_settings(); ex = [x for x in s.get("exclusions", []) if x != p]
        s["exclusions"] = ex; save_settings(s); self._render_exclusions()

    def _reset_stats(self):
        if not messagebox.askyesno("Reset", "Reset the lifetime space-reclaimed counter to zero?"): return
        try: STATS_FILE.write_text(json.dumps({"freed": 0, "runs": 0}))
        except Exception: pass
        self._render_exclusions(); self._refresh_stat()

    # ══ UNINSTALLER ══
    def _build_uninstaller(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["uninstall"] = v
        self._title(v, "App Uninstaller", "Removes an app + all its leftover files")
        self.uapp_inner = self._scroller(v)
        tk.Label(self.uapp_inner, text="Loading apps…", bg=C["SURF"], fg=C["MUTED"],
                 font=(UIFONT, 11)).pack(pady=16)
        self._loaded_apps = False

    def load_apps(self):
        if getattr(self, "_loaded_apps", False) or self.busy: return
        self._loaded_apps = True
        def run():
            apps = list_apps()
            self.q.put(("apps", apps))
        threading.Thread(target=run, daemon=True).start()

    def _render_apps(self, apps):
        for w in self.uapp_inner.winfo_children(): w.destroy()
        for nm, path in apps:
            row = tk.Frame(self.uapp_inner, bg=C["SURF"]); row.pack(fill="x", padx=8, pady=1)
            row.columnconfigure(0, weight=1)
            tk.Label(row, text=nm, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 12)).grid(row=0, column=0, sticky="w", pady=4)
            _chip(row, "Uninstall", lambda n=nm,p=path: self.uninstall_app(n,p), "ghost"
                  ).grid(row=0, column=1, padx=6)

    def uninstall_app(self, name, path):
        if self.busy: return
        self.q.put(("status", f"Scanning leftovers for {name}…"))
        def run():
            left = app_leftovers(name, path)
            tot = dir_size(path) + sum(dir_size(p) for p in left)
            self.q.put(("uninstall_confirm", (name, path, left, tot)))
        threading.Thread(target=run, daemon=True).start()

    def _confirm_uninstall(self, name, path, left, tot):
        msg = (f"Move “{name}” and {len(left)} leftover item(s) to Trash?\n\n"
               f"≈ {human(tot)} total. Recoverable from Trash.")
        if not messagebox.askyesno("Uninstall app?", msg): return
        self.q.put(("busy", True)); self.q.put(("status", f"Uninstalling {name}…"))
        def run():
            fails = []; freed = 0
            asz = dir_size(path)
            if move_to_trash(path): freed += asz
            else: fails.append(path)
            done = 0
            for p in left:
                if path_protected(str(p), hard=False): continue
                psz = dir_size(p)
                if move_to_trash(str(p)): done += 1; freed += psz
                else: fails.append(str(p))
            bump_stats(freed)
            self.q.put(("stat", None))
            if fails:
                app_stuck = path in fails
                why = ("quit “%s” if it's still running, then retry" % name) if app_stuck \
                      else "some items need admin rights or belong to a running app"
                self.q.put(("status",
                    f"⚠ {name}: removed {done}/{len(left)} leftover(s)"
                    + ("" if app_stuck else " + the app")
                    + f". {len(fails)} couldn't be trashed — {why}."))
            else:
                self.q.put(("status", f"✅ {name} + {len(left)} leftover(s) → Trash."))
            self.q.put(("busy", False)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ DUPLICATES ══
    def _build_duplicates(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["dupes"] = v
        f = self._title(v, "Duplicate Finder", "Finds identical files (>1 MB) in your folders")
        self.dupe_btn = self._btn(f, "🔍  Scan", self.scan_dupes, "gold"); self.dupe_btn.pack(side="right")
        self.dupe_inner = self._scroller(v)
        self.dupe_vars = {}
        self.del_dupe_btn = self._btn(v, "🗑  Delete Selected Copies", self.del_dupes, "gold")
        self.del_dupe_btn.pack(anchor="e", padx=22, pady=10)

    def scan_dupes(self):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status","Hashing files for duplicates…"))
        def run():
            groups = find_duplicates([str(d) for d in SCAN_DIRS])
            self.q.put(("dupes", groups))
            wasted = sum(sz*(len(ps)-1) for sz,ps in groups)
            self.q.put(("status", f"Found {len(groups)} duplicate set(s) · {human(wasted)} wasted."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _render_dupes(self, groups):
        for w in self.dupe_inner.winfo_children(): w.destroy()
        self.dupe_vars = {}
        if not groups:
            tk.Label(self.dupe_inner, text="No duplicates found.", bg=C["SURF"], fg=C["MUTED"],
                     font=(UIFONT, 12)).pack(pady=20); return
        for sz, paths in groups:
            hdr = tk.Frame(self.dupe_inner, bg=C["SURF2"]); hdr.pack(fill="x", padx=4, pady=(8,0))
            tk.Label(hdr, text=f"{len(paths)} copies · {human(sz)} each", bg=C["SURF2"],
                     fg=C["MAROON"], anchor="w", font=(UIFONT, 11, "bold")).pack(anchor="w", padx=8, pady=3)
            for i, p in enumerate(paths):
                row = tk.Frame(self.dupe_inner, bg=C["SURF"]); row.pack(fill="x", padx=10)
                row.columnconfigure(1, weight=1)
                var = tk.BooleanVar(value=(i>0))   # keep first, mark extras
                self.dupe_vars[p] = var
                tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                               activebackground=C["SURF"], bd=0, highlightthickness=0
                               ).grid(row=0, column=0, sticky="w")
                tag = "  (keep)" if i==0 else ""
                tk.Label(row, text=p.replace(str(HOME),"~")+tag, bg=C["SURF"],
                         fg=C["MUTED"] if i==0 else C["TEXT"], anchor="w",
                         font=(UIFONT, 10)).grid(row=0, column=1, sticky="w", padx=4)

    def del_dupes(self):
        if self.busy: return
        picks = [p for p,v in self.dupe_vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH MacCleaner","No copies selected."); return
        if not messagebox.askyesno("Delete copies?",
            f"Move {len(picks)} duplicate file(s) to Trash?\nRecoverable from Trash."): return
        self.q.put(("busy", True))
        def run():
            freed = 0; ok = 0
            for p in picks:
                if path_protected(p, hard=False): continue
                try: sz = os.path.getsize(p)
                except OSError: sz = 0
                if move_to_trash(p): ok += 1; freed += sz
            fail = len(picks) - ok
            bump_stats(freed)
            self.q.put(("status", f"✅ {ok} duplicate(s) → Trash."
                        + (f" ⚠ {fail} couldn't be moved (in use / permission)." if fail else "")))
            self.q.put(("busy", False)); self.q.put(("stat", None)); self._trash_size()
            self.q.put(("rescan_dupes", None))
        threading.Thread(target=run, daemon=True).start()

    # ══ MAINTENANCE ══
    def _build_maintenance(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["maint"] = v
        self._title(v, "Maintenance", "Quick system tune-ups (some ask for your password)")
        grid = tk.Frame(v, bg=C["BG"]); grid.pack(fill="x", padx=22, pady=8)
        tools = [
            ("🧠", "Free Up RAM", "purge inactive memory",
             "Frees inactive memory so apps get more RAM. Use when your Mac feels slow or laggy.",
             lambda: self.maint("/usr/sbin/purge", "Free RAM")),
            ("🌐", "Flush DNS", "reset DNS cache",
             "Clears the DNS cache. Fixes websites that won't load or point to an old/wrong server.",
             lambda: self.maint("/usr/bin/dscacheutil -flushcache; /usr/bin/killall -HUP mDNSResponder", "Flush DNS")),
            ("🔦", "Reindex Spotlight", "rebuild search index",
             "Rebuilds the Spotlight search index. Fixes missing files or wrong results in search. Takes a while in the background.",
             lambda: self.maint("/usr/bin/mdutil -E /", "Reindex Spotlight")),
            ("🚀", "Rebuild Launch DB", "fix Open With duplicates",
             "Rebuilds the app database. Fixes duplicate or wrong entries in the “Open With” menu.",
             lambda: self.maint("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -kill -r -domain local -domain user", "Rebuild Launch Services", admin=False)),
            ("📱", "Clean Simulators", "Xcode: unavailable sims",
             "Removes iOS Simulators for runtimes you no longer have (xcrun simctl delete unavailable) and shuts running ones down. Xcode re-creates any it needs.",
             lambda: self.maint("/usr/bin/xcrun simctl shutdown all; /usr/bin/xcrun simctl delete unavailable", "Clean Simulators", admin=False)),
            ("🧰", "Trim DeviceSupport", "Xcode: old iOS symbols",
             "Moves ~/Library/Developer/Xcode/*DeviceSupport to the Trash (recoverable). Xcode rebuilds it the next time you plug that device in. Often several GB.",
             lambda: self.clean_devicesupport()),
            ("🐳", "Docker Prune", "unused images + build cache",
             "Runs `docker system prune -af` — removes stopped containers, unused images/networks and build cache. Does NOT remove named volumes (your data).",
             lambda: self.docker_prune()),
        ]
        for i,(ico,name,sub,tip,cmd) in enumerate(tools):
            card = tk.Frame(grid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.grid(row=i//2, column=i%2, sticky="nsew", padx=5, pady=4)
            grid.columnconfigure(i%2, weight=1)
            hd = tk.Frame(card, bg=C["SURF"]); hd.pack(fill="x", padx=12, pady=(10,0))
            tk.Label(hd, text=ico, bg=C["SURF"], font=(UIFONT, 16)).pack(side="left")
            tk.Label(hd, text=name, bg=C["SURF"], fg=C["TEXT"], font=(HEADFONT, 12, "bold")).pack(side="left", padx=6)
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], font=(UIFONT, 9), anchor="w").pack(fill="x", padx=12, pady=(1,0))
            self._btn(card, "Run", cmd, "gold").pack(anchor="w", padx=12, pady=8)
            Tip(card, tip)

    def maint(self, cmd, label, admin=True):
        if self.busy: return
        self.q.put(("busy", True)); self.q.put(("status", f"{label}…"))
        def run():
            before = free_mem_bytes() if label == "Free RAM" else None
            if admin: ok, out = run_admin(cmd)
            else:
                r = subprocess.run(["bash","-c",cmd], capture_output=True, text=True)
                ok, out = r.returncode == 0, (r.stderr or r.stdout).strip()
            if label == "Free RAM" and ok:
                after = free_mem_bytes()
                gained = after - (before or 0)
                detail = (f"✅ RAM freed.\n\nAvailable memory now: {human(after)}"
                          + (f"\nReclaimed: ~{human(gained)}" if gained > 0 else ""))
            elif ok:
                detail = f"✅ {label} completed successfully."
            else:
                low = (out or "").lower()
                if "cancel" in low or "-128" in low:
                    detail = "Cancelled — password not entered."
                else:
                    detail = f"⚠ {label} failed.\n\n{out[:160] or 'Unknown error.'}"
            self.q.put(("maint_done", (label, ok, detail)))
            self.q.put(("status", f"{'✅' if ok else '⚠'} {label}: {'done' if ok else 'failed'}"))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    def _maint_done(self, label, ok, detail):
        (messagebox.showinfo if ok else messagebox.showwarning)("ZH MacCleaner — " + label, detail)

    def clean_devicesupport(self):
        if self.busy: return
        dirs = [HOME/"Library/Developer/Xcode/iOS DeviceSupport",
                HOME/"Library/Developer/Xcode/watchOS DeviceSupport",
                HOME/"Library/Developer/Xcode/tvOS DeviceSupport"]
        entries = []
        for d in dirs:
            if d.is_dir():
                for e in os.listdir(d): entries.append(d/e)
        if not entries:
            messagebox.showinfo("ZH MacCleaner", "No Xcode DeviceSupport folders found."); return
        tot = sum(dir_size(p) for p in entries)
        if not messagebox.askyesno("Trim DeviceSupport?",
            f"Move {len(entries)} DeviceSupport folder(s) ({human(tot)}) to Trash?\n\n"
            f"Recoverable. Xcode rebuilds each one the next time you connect that device."): return
        self.q.put(("busy", True)); self.q.put(("status", "Trimming Xcode DeviceSupport…"))
        def run():
            freed = 0
            for p in entries:
                sp = str(p)
                if not sp.startswith(str(HOME/"Library/Developer/Xcode")): continue
                sz = dir_size(p)
                if move_to_trash(sp): freed += sz
            bump_stats(freed)
            self.q.put(("maint_done", ("Trim DeviceSupport", True, f"✅ Moved {human(freed)} to Trash.")))
            self.q.put(("status", f"✅ DeviceSupport trimmed — {human(freed)}."))
            self.q.put(("busy", False)); self.q.put(("stat", None)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    def docker_prune(self):
        if self.busy: return
        if not messagebox.askyesno("Docker Prune?",
            "Run `docker system prune -af`?\n\nRemoves stopped containers, unused images, "
            "unused networks and all build cache. Named volumes (your data) are kept.\n\n"
            "Docker Desktop must be running."): return
        self.q.put(("busy", True)); self.q.put(("status", "Pruning Docker…"))
        def run():
            docker = shutil.which("docker") or next(
                (p for p in ("/usr/local/bin/docker", "/opt/homebrew/bin/docker",
                             "/Applications/Docker.app/Contents/Resources/bin/docker") if os.path.exists(p)), None)
            if not docker:
                self.q.put(("maint_done", ("Docker Prune", False, "docker command not found — is Docker installed?")))
                self.q.put(("status", "⚠ Docker not found.")); self.q.put(("busy", False)); return
            r = subprocess.run([docker, "system", "prune", "-af"], capture_output=True, text=True)
            ok = r.returncode == 0
            out = (r.stdout or r.stderr).strip()
            m = re.search(r"Total reclaimed space:\s*(.+)", out)
            detail = (f"✅ {m.group(1).strip()} reclaimed." if (ok and m)
                      else (f"✅ Done.\n\n{out[-200:]}" if ok else f"⚠ Failed.\n\n{out[-200:] or 'Is Docker Desktop running?'}"))
            self.q.put(("maint_done", ("Docker Prune", ok, detail)))
            self.q.put(("status", f"{'✅' if ok else '⚠'} Docker prune {'done' if ok else 'failed'}."))
            self.q.put(("busy", False))
        threading.Thread(target=run, daemon=True).start()

    # ══ LICENSE / PRO ══
    def is_pro(self):
        return bool(self.lic.get("valid")) and self.lic.get("plan") == "pro"

    def _load_license(self):
        try:
            d = json.loads(LIC_FILE.read_text())
            self.lic.update(d)
            if self.lic.get("valid") and (time.time() - self.lic.get("checked", 0)) > GRACE_DAYS*86400:
                self.lic["valid"] = False      # grace expired, needs re-check
        except Exception:
            pass

    def _save_license(self):
        try:
            LIC_FILE.parent.mkdir(parents=True, exist_ok=True)
            LIC_FILE.write_text(json.dumps(self.lic))
        except Exception:
            pass

    def _verify_online(self, key):
        # Direct can 403: the host firewall blocks Python's TLS fingerprint / serves an HTML
        # challenge. Alternate direct → Worker relay (clean IP, /api/ is on its allowlist).
        last_err = ""
        relay = LICENSE_URL.replace("https://zhmotions.com", "https://api-relay-2.zhmotionspanel.workers.dev")
        for attempt, url in enumerate((LICENSE_URL, relay, relay)):
            try:
                body = urllib.parse.urlencode({
                    "key": key, "app": "maccleaner", "device": device_id(), "v": APP_VERSION}).encode()
                req = urllib.request.Request(url, data=body, headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded"})   # so PHP fills $_POST
                raw = urllib.request.urlopen(req, timeout=20, context=SSL_CTX).read().decode()
                data = json.loads(raw)   # bot-challenge HTML -> JSONDecodeError -> next source
                return bool(data.get("valid")), (data.get("plan") or "pro"), (data.get("message") or "")
            except Exception as e:
                last_err = str(e)
                if attempt < 2:
                    time.sleep(1.2)
        return None, None, last_err           # None = couldn't reach after retries

    def _reverify_license(self):
        key = self.lic.get("key")
        if not key: return
        def run():
            ok, plan, _ = self._verify_online(key)
            if ok is None: return               # offline → keep cached within grace
            self.lic.update({"valid": bool(ok), "plan": plan or "free", "checked": time.time()})
            self._save_license(); self.q.put(("license_changed", None))
        threading.Thread(target=run, daemon=True).start()

    # ── In-app review prompt ────────────────────────────────────────────
    def _review_state(self):
        try: return json.loads(REVIEW_FILE.read_text())
        except Exception: return {}

    def _review_save(self, d):
        try:
            REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
            REVIEW_FILE.write_text(json.dumps(d))
        except Exception: pass

    def _maybe_review(self):
        st = self._review_state()
        now = time.time()
        if not st.get("first_run"):
            st["first_run"] = now; self._review_save(st); return    # start the clock on first launch
        if st.get("status") == "done": return
        if now < st.get("snooze_until", 0): return
        if now - st.get("first_run", now) < REVIEW_AFTER_DAYS * 86400: return
        try: self._show_review()
        except Exception: pass

    def _show_review(self):
        win = tk.Toplevel(self); win.title("Enjoying ZH MacCleaner?")
        win.configure(bg=C["BG"]); win.resizable(False, False)
        try: win.transient(self)
        except Exception: pass
        W, H = 400, 360
        try:
            x = self.winfo_rootx() + (self.winfo_width() - W)//2
            y = self.winfo_rooty() + (self.winfo_height() - H)//3
            win.geometry(f"{W}x{H}+{max(0,x)}+{max(0,y)}")
        except Exception: win.geometry(f"{W}x{H}")

        tk.Label(win, text="Enjoying ZH MacCleaner?", bg=C["BG"], fg=C["TEXT"],
                 font=(UIFONT, 16, "bold")).pack(anchor="w", padx=22, pady=(20, 2))
        tk.Label(win, text="Tap the stars and leave a quick review — it really helps.",
                 bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10), wraplength=356, justify="left").pack(anchor="w", padx=22)

        state = {"rating": 0}
        stars_row = tk.Frame(win, bg=C["BG"]); stars_row.pack(anchor="w", padx=20, pady=(12, 6))
        star_lbls = []
        def paint(n):
            for i, s in enumerate(star_lbls):
                s.config(fg=(C["GOLD"] if i < n else C["BORDER"]))
        def pick(n):
            state["rating"] = n; paint(n)
        for i in range(5):
            s = tk.Label(stars_row, text="★", bg=C["BG"], fg=C["BORDER"], font=(UIFONT, 30), cursor="pointinghand")
            s.pack(side="left", padx=2); s.bind("<Button-1>", lambda e, n=i+1: pick(n))
            star_lbls.append(s)

        tk.Label(win, text="Your name", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 9)).pack(anchor="w", padx=22)
        name_e = tk.Entry(win, font=(UIFONT, 12), bg=C["SURF"], fg=C["TEXT"], relief="flat",
                          highlightthickness=1, highlightbackground=C["BORDER"], highlightcolor=C["GOLD"])
        name_e.pack(fill="x", padx=22, ipady=5, pady=(2, 8))
        cmt = tk.Text(win, height=3, font=(UIFONT, 11), bg=C["SURF"], fg=C["TEXT"], relief="flat",
                      highlightthickness=1, highlightbackground=C["BORDER"], highlightcolor=C["GOLD"], wrap="word")
        cmt.pack(fill="x", padx=22, pady=(0, 4))
        msg = tk.Label(win, text="", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 9)); msg.pack(anchor="w", padx=22)

        btns = tk.Frame(win, bg=C["BG"]); btns.pack(fill="x", padx=20, pady=(6, 16), side="bottom")
        def later():
            st = self._review_state(); st["snooze_until"] = time.time() + 3*86400; self._review_save(st); win.destroy()
        def never():
            st = self._review_state(); st["status"] = "done"; self._review_save(st); win.destroy()
        def submit():
            name = name_e.get().strip(); comment = cmt.get("1.0", "end").strip(); rating = state["rating"]
            if rating < 1: msg.config(text="Please tap the stars to rate.", fg=C["GOLD"]); return
            if len(name) < 2: msg.config(text="Please enter your name.", fg=C["GOLD"]); return
            msg.config(text="Sending…", fg=C["MUTED"])
            def run():
                ok = False; err = "Couldn't send — check your internet and retry."
                body = urllib.parse.urlencode({"app": APP_SLUG, "name": name, "rating": rating, "comment": comment}).encode()
                # Direct first; if the host firewall serves its HTML challenge (JSON parse fails /
                # HTTPError), retry through the clean-IP Worker relay.
                for url in (REVIEW_URL, REVIEW_URL_FALLBACK):
                    try:
                        req = urllib.request.Request(url, data=body,
                              headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"})
                        data = json.loads(urllib.request.urlopen(req, timeout=15, context=SSL_CTX).read().decode())
                        ok = (data.get("status") == "success")
                        if not ok and data.get("message"): err = str(data.get("message"))   # real reason (e.g. already reviewed), not a fake network error
                        break                       # got a JSON answer (success OR rejection) → stop
                    except Exception:
                        ok = False                  # challenge/HTML/network → try the relay next
                def done():
                    if ok:
                        st = self._review_state(); st["status"] = "done"; self._review_save(st)
                        msg.config(text="Thank you! ★", fg=C["GOLD"]); win.after(900, win.destroy)
                    else:
                        msg.config(text=err, fg=C["RED"])
                self.after(0, done)
            threading.Thread(target=run, daemon=True).start()

        tk.Label(btns, text="Maybe later", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10),
                 cursor="pointinghand").pack(side="left")
        tk.Label(btns, text="No thanks", bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10),
                 cursor="pointinghand").pack(side="left", padx=14)
        send = tk.Label(btns, text="  Post review  ", bg=C["GOLD"], fg=ON_GOLD, font=(UIFONT, 11, "bold"),
                        cursor="pointinghand", padx=6, pady=7); send.pack(side="right")
        send.bind("<Button-1>", lambda e: submit())
        btns.winfo_children()[0].bind("<Button-1>", lambda e: later())
        btns.winfo_children()[1].bind("<Button-1>", lambda e: never())

    def activate_license(self, key):
        key = key.strip()
        if not key: messagebox.showinfo("License", "Enter your license key first."); return
        self.q.put(("status", "Verifying license…"))
        def run():
            ok, plan, msg = self._verify_online(key)
            if ok is None:
                self.q.put(("license_result", (False, "Couldn't reach the license server. Check your internet.")))
            elif ok:
                self.lic.update({"key": key, "valid": True, "plan": plan or "pro", "checked": time.time()})
                self._save_license()
                self.q.put(("license_result", (True, "✅ Pro unlocked. Thank you for supporting ZH Motions!")))
            else:
                self.q.put(("license_result", (False, msg or "Invalid or inactive key.")))
        threading.Thread(target=run, daemon=True).start()

    def _build_license(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["license"] = v
        inner = self._scroller(v)
        self.lic_ctx = tk.Label(inner, text="", bg=C["SURF"], fg=C["MAROON"], anchor="w",
                                font=(UIFONT, 12, "bold"), wraplength=520, justify="left")
        self.lic_ctx.pack(fill="x", padx=14, pady=(14,0))
        tk.Label(inner, text="ZH MacCleaner Pro", bg=C["SURF"], fg=C["TEXT"],
                 font=(HEADFONT, 18, "bold")).pack(anchor="w", padx=14, pady=(8,2))
        self.lic_status = tk.Label(inner, text="", bg=C["SURF"], anchor="w", font=(UIFONT, 12, "bold"))
        self.lic_status.pack(fill="x", padx=14, pady=(0,8))

        tk.Label(inner, text="Pro unlocks:", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(6,2))
        for t in ("🗑️  App Uninstaller — remove apps + leftovers",
                  "👯  Duplicate Finder — reclaim wasted space",
                  "🛠  Maintenance — free RAM, flush DNS, reindex",
                  "↻  Priority updates from zhmotions.com"):
            tk.Label(inner, text="   "+t, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 11)).pack(fill="x", padx=14)

        # ── FREE: enter a key ──
        self.key_section = tk.Frame(inner, bg=C["SURF"])
        self.key_section.pack(fill="x")
        tk.Label(self.key_section, text="License key", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(14,2))
        row = tk.Frame(self.key_section, bg=C["SURF"]); row.pack(fill="x", padx=14)
        self.key_entry = tk.Entry(row, font=(MONO, 12), relief="flat",
                                  bg=C["BG"], fg=C["TEXT"], insertbackground=C["TEXT"])
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,8))
        self._btn(row, "Activate", lambda: self.activate_license(self.key_entry.get()), "gold").pack(side="right")
        buy = tk.Label(self.key_section, text="Get a license at zhmotions.com/maccleaner", bg=C["SURF"],
                       fg=C["MAROON2"], font=(UIFONT, 11, "underline"), cursor="pointinghand")
        buy.pack(anchor="w", padx=14, pady=14)
        buy.bind("<Button-1>", lambda e: subprocess.run(["open", SITE+"/maccleaner"]))

        # ── PRO: manage / control (shown when activated) ──
        self.pro_section = tk.Frame(inner, bg=C["SURF"])
        self.lic_keylbl = tk.Label(self.pro_section, text="", bg=C["SURF"], fg=C["MUTED"],
                                   anchor="w", font=(MONO, 12))
        self.lic_keylbl.pack(fill="x", padx=14, pady=(14,8))
        mrow = tk.Frame(self.pro_section, bg=C["SURF"]); mrow.pack(fill="x", padx=14, pady=(0,12))
        self._btn(mrow, "Change key", self._change_key, "ghost").pack(side="left")
        self._btn(mrow, "Deactivate", self.deactivate_license, "ghost").pack(side="left", padx=8)

        self._refresh_license_ui()

    def _change_key(self):
        # show the entry again without losing Pro until a new key is activated
        self.pro_section.pack_forget(); self.key_section.pack(fill="x")
        self.key_entry.delete(0, "end"); self.key_entry.focus_set()

    def deactivate_license(self):
        if not messagebox.askyesno("Deactivate", "Remove the license from this Mac? Pro features will lock."):
            return
        self.lic = {"key": "", "plan": "free", "valid": False, "checked": 0}
        try: LIC_FILE.unlink()
        except Exception: pass
        self._save_license()
        self._refresh_license_ui()
        self.q.put(("status", "License removed. Pro locked."))

    def _refresh_license_ui(self):
        if not hasattr(self, "lic_status"): return
        if self.is_pro():
            self.lic_status.config(text="● PRO — active ✓", fg=C["GREEN"])
            # hide the key entry, show the manage box (masked key + controls)
            self.key_section.pack_forget()
            self.pro_section.pack(fill="x")
            k = self.lic.get("key", "")
            masked = (k[:9] + "••••-" + k[-4:]) if len(k) > 13 else k
            self.lic_keylbl.config(text="Licensed key:  " + masked)
        else:
            self.lic_status.config(text="○ Free version", fg=C["MUTED"])
            self.pro_section.pack_forget()
            self.key_section.pack(fill="x")
        if "license" in self.nav_btns:
            self.nav_btns["license"].config(text="   ⭐   " + ("Pro ✓" if self.is_pro() else "Pro"))

    # ══ HELP & ABOUT ══
    def _build_help(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["help"] = v
        inner = self._scroller(v)
        def section(title, body):
            tk.Label(inner, text=title, bg=C["SURF"], fg=C["MAROON"], anchor="w",
                     font=(HEADFONT, 13, "bold")).pack(fill="x", padx=14, pady=(12,2))
            tk.Label(inner, text=body, bg=C["SURF"], fg=C["TEXT"], anchor="w", justify="left",
                     font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14, pady=(0,4))

        tk.Label(inner, text="What is ZH MacCleaner?", bg=C["SURF"], fg=C["TEXT"],
                 font=(HEADFONT, 16, "bold")).pack(anchor="w", padx=14, pady=(14,2))
        tk.Label(inner, text="A safe, simple Mac cleaner. It frees disk space by removing junk that "
                 "your Mac rebuilds automatically — and never touches system files.",
                 bg=C["SURF"], fg=C["MUTED"], anchor="w", justify="left",
                 font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14)

        section("🧹  Cleanup", "Deletes app caches, logs and browser caches. These regenerate on their "
                "own — safe to remove. Tick what you want and press “Clean Selected”.")
        section("📦  Large Files", "Finds files over 100 MB in Downloads, Desktop, Documents & Movies. "
                "Pick the ones you don't need — they go to the Trash (recoverable).")
        section("📥  Old Installers", "Finds .dmg / .pkg / .iso in Downloads older than two weeks — "
                "the app is already installed, so the download is just wasted space. Only the installer "
                "file is trashed, never the installed app.")
        section("📱  iOS Backups", "Local iPhone / iPad backups made by Finder — often tens of GB. "
                "Delete one only if you won't need to restore that device from it. iCloud backups are separate.")
        section("🗑️  Uninstaller", "Removes an app AND its leftover files (caches, preferences, support "
                "folders) that normally stay behind when you drag an app to the Trash.")
        section("👯  Duplicates", "Finds identical files (same content). Keeps the first copy, lets you "
                "trash the extras to reclaim space.")
        section("🛠  Maintenance — what each tool does",
                "•  Free Up RAM — purges inactive memory so apps get more free RAM. Use when your Mac feels slow.\n"
                "•  Flush DNS — clears the DNS cache. Fixes sites that won't load or point to an old server.\n"
                "•  Reindex Spotlight — rebuilds the search index. Fixes Spotlight missing files or wrong results.\n"
                "•  Rebuild Launch DB — fixes duplicate or wrong “Open With” app entries.\n"
                "•  Clean Simulators — removes Xcode iOS Simulators for runtimes you no longer have.\n"
                "•  Trim DeviceSupport — trashes old Xcode iOS symbol folders (rebuilt on next device connect).\n"
                "•  Docker Prune — `docker system prune -af`: unused images, stopped containers, build cache. Volumes kept.\n\n"
                "Some ask for your Mac password (normal for system tasks). You get a popup with the result.")
        section("⚙️  Settings", "Add any folder to “Protected folders” and ZH Cleaner will never list or "
                "delete anything inside it — in any scan. The Settings screen also shows how much space "
                "you've reclaimed over the life of the app.")

        section("🔒  Is it safe?", "Yes. ZH MacCleaner only touches a fixed list of safe user folders, plus "
                "the pattern-matched scans (installers, backups) which each show you every item and let you "
                "uncheck it before anything moves. Your own files go to the Trash so you can restore them. "
                "It never deletes documents, photos, iCloud Drive or system files — and anything you add to "
                "Protected folders is skipped everywhere.")
        section("💡  Seeing small cache sizes?", "Grant Full Disk Access so it can read all caches: "
                "System Settings → Privacy & Security → Full Disk Access → + → add ZH MacCleaner.")

        # Branding footer
        brand = tk.Frame(inner, bg=C["SURF"]); brand.pack(fill="x", padx=14, pady=18)
        if self.logo_img:
            tk.Label(brand, image=self.logo_img, bg=C["SURF"]).pack(side="left", padx=(0,10))
        col = tk.Frame(brand, bg=C["SURF"]); col.pack(side="left")
        tk.Label(col, text=f"ZH MacCleaner  ·  v{APP_VERSION}", bg=C["SURF"], fg=C["MAROON"],
                 font=(HEADFONT, 12, "bold")).pack(anchor="w")
        tk.Label(col, text="Made by ZH Motions", bg=C["SURF"], fg=C["MUTED"],
                 font=(UIFONT, 10)).pack(anchor="w")
        link = tk.Label(col, text="zhmotions.com", bg=C["SURF"], fg=C["MAROON2"],
                        font=(UIFONT, 10, "underline"), cursor="pointinghand")
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: subprocess.run(["open", SITE]))
        self._btn(col, "↻  Check for Updates", lambda: self.check_updates(False), "gold").pack(anchor="w", pady=(8,0))

    def check_updates(self, silent=True):
        if not silent: self.q.put(("status", "Checking zhmotions.com for updates…"))
        def run():
            for name, url, kind in UPDATE_SOURCES:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": UA})
                    data = json.loads(urllib.request.urlopen(req, timeout=8, context=SSL_CTX).read().decode())
                    if kind == "zhm":
                        latest = str(data.get("version", "")).strip().lstrip("v")
                        dl     = data.get("download_url") or SITE
                        notes  = data.get("notes", "")
                    else:  # github releases/latest
                        latest = str(data.get("tag_name", "")).strip().lstrip("v")
                        dl     = data.get("html_url") or SITE
                        notes  = (data.get("body") or "")[:200]
                    if not latest:
                        continue
                    if self._is_newer(latest, APP_VERSION):
                        # Self-installing update: download the pkg silently NOW; the next
                        # app start opens the installer automatically.
                        self._auto_update_fetch(latest, dl, notes)
                    elif not silent:
                        self.q.put(("status", f"✅ You're on the latest (v{APP_VERSION})."))
                    return  # first source that answered wins
                except Exception:
                    continue
            if not silent:
                self.q.put(("status", "⚠ Update check failed (no internet or site offline)."))
        threading.Thread(target=run, daemon=True).start()

    def _auto_update_fetch(self, latest, dl, notes):
        """Runs in the update-check thread. Background pkg download → recorded in
        UPD_STATE; _pending_update_install() opens it on the next start. Any failure
        falls back to the old ask-dialog so updates are never lost."""
        try:
            try:
                st = json.loads(UPD_STATE.read_text()) if UPD_STATE.exists() else {}
            except Exception:
                st = {}
            if st.get("ver") == latest and Path(st.get("path", "")).exists():
                self.q.put(("status", f"⬇ Update v{latest} ready — restart the app to install"))
                return
            UPD_DIR.mkdir(parents=True, exist_ok=True)
            dest = UPD_DIR / f"ZH-MacCleaner-{latest}.pkg"
            url = f"{MAC_DL}?v={latest}"   # download.php serves the newest pkg; ?v busts caches
            self.q.put(("status", f"⬇ Downloading update v{latest} in the background…"))
            p = subprocess.run(["curl", "-fSL", "--retry", "2", "-m", "600", "-A", UA,
                                "-o", str(dest), url], capture_output=True)
            if p.returncode != 0 or not dest.exists() or dest.stat().st_size < 2_000_000:
                try: dest.unlink(missing_ok=True)
                except Exception: pass
                self.q.put(("update", (latest, dl, notes)))   # manual fallback
                return
            UPD_STATE.write_text(json.dumps({"ver": latest, "path": str(dest), "launched": False}))
            self.q.put(("status", f"⬇ Update v{latest} ready — restart the app and it installs itself"))
        except Exception:
            self.q.put(("update", (latest, dl, notes)))

    @staticmethod
    def _is_newer(a, b):
        def parts(v): return [int(x) for x in v.split(".") if x.isdigit()]
        return parts(a) > parts(b)

    def _show_update(self, latest, url, notes):
        if messagebox.askyesno("Update available",
            f"ZH MacCleaner v{latest} is available (you have v{APP_VERSION}).\n\n"
            f"{notes}\n\nDownload from zhmotions.com now?"):
            subprocess.run(["open", url])


def _pending_update_install():
    """Before the UI: an update downloaded on a previous run? Open its installer now and
    exit — 'restart the app = the update installs itself'. One attempt per version."""
    try:
        st = json.loads(UPD_STATE.read_text()) if UPD_STATE.exists() else {}
        ver, path = str(st.get("ver", "")), str(st.get("path", ""))
        if not ver or not path:
            return
        def vt(v): return [int(x) for x in v.split(".") if x.isdigit()]
        if vt(ver) <= vt(APP_VERSION) or not Path(path).exists():
            try: Path(path).unlink(missing_ok=True)
            except Exception: pass
            try: UPD_STATE.unlink(missing_ok=True)
            except Exception: pass
            return
        if st.get("launched"):
            return   # user cancelled the installer once — don't nag every start
        st["launched"] = True
        UPD_STATE.write_text(json.dumps(st))
        subprocess.Popen(["open", path])
        sys.exit(0)
    except SystemExit:
        raise
    except Exception:
        pass


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("ZH Cleaner is built for macOS.")
    _pending_update_install()
    Cleaner().mainloop()
