#!/usr/bin/env python3
"""
ZH Cleaner — a safe Mac cleaner (pro UI, ZH Motions theme)

Cleans: System junk (caches/logs), Browser caches, Dev junk, Large/old files.
Safety:
  • Only a hard-coded whitelist of known-safe user paths.
  • Cache/log contents are deleted (OS/apps regenerate them).
  • Your own files (large-file finder) move to the macOS Trash (recoverable).
  • Auto-scans on launch and shows sizes BEFORE you clean.
"""

import os, sys, threading, queue, time, subprocess, shutil, hashlib, plistlib, json
import urllib.request, urllib.parse
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

APP_VERSION = "1.0"
SITE        = "https://www.zhmotions.com"
# Same update system as ZH Downloader: zhmotions.com FIRST, GitHub as fallback.
#   version.json -> {"version":"1.1","download_url":"https://.../ZH-MacCleaner.dmg","notes":"..."}
UPDATE_SOURCES = [
    ("zhmotions", "https://zhmotions.com/maccleaner/version.json", "zhm"),
]   # zhmotions.com only — no third-party

# ── Licensing: free app, Pro features unlocked by a key (self-hosted) ──
LICENSE_URL   = "https://zhmotions.com/api/license/verify"   # non-www + no .php (server strips it; redirects drop POST body)
LIC_FILE      = HOME/".config/zhmaccleaner/license.json"
PRO_FEATURES  = {"uninstall", "dupes", "maint"}     # locked until Pro
GRACE_DAYS    = 14                                  # offline grace after last good check

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

# ── Monochromatic palette — every tone is a shade of ONE maroon hue ──
C = {
    "BG":"#f7f1f2", "SIDEBAR":"#ead9dd", "HEADER":"#fdfbfb",
    "SURF":"#ffffff", "SURF2":"#ecdade", "BORDER":"#dfc8cd",
    "MAROON":"#7A1F2B", "MAROON2":"#9c2a3a",
    "GOLD":"#7A1F2B", "GOLD2":"#9c2a3a",   # accents = maroon
    "TEXT":"#2c1014", "MUTED":"#9a767c",   # darkest maroon / muted maroon
    "GREEN":"#7A1F2B", "RED":"#9c2a3a",
}
UIFONT = "SF Pro Text"
MONO   = "SF Mono"

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

def move_to_trash(path):
    p = str(path).replace('"','\\"')
    subprocess.run(["osascript","-e",
        f'tell application "Finder" to move (POSIX file "{p}") to trash'], capture_output=True)

def clear_contents(path):
    freed = 0
    if not os.path.isdir(path): return 0
    for entry in os.listdir(path):
        fp = os.path.join(path, entry)
        try:
            sz = dir_size(fp) if os.path.isdir(fp) and not os.path.islink(fp) else os.path.getsize(fp)
        except OSError:
            sz = 0
        try:
            if os.path.islink(fp) or os.path.isfile(fp): os.remove(fp); freed += sz
            elif os.path.isdir(fp): shutil.rmtree(fp, ignore_errors=True); freed += sz
        except OSError:
            pass
    return freed

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

def app_leftovers(app_name, app_path):
    bid = bundle_id(app_path)
    name_l = app_name.lower().replace(" ", "")
    keys = [k for k in (bid, app_name) if k]
    hits = []
    for d in LEFTOVER_DIRS:
        if not d.exists(): continue
        try:
            for e in os.listdir(d):
                el = e.lower()
                if (bid and bid.lower() in el) or (name_l and name_l in el.replace(" ", "")):
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

def fda_granted():
    """True if the app has Full Disk Access (can read the TCC database)."""
    try:
        with open(HOME/"Library/Application Support/com.apple.TCC/TCC.db", "rb") as f:
            f.read(1)
        return True
    except PermissionError:
        return False
    except Exception:
        return True   # file missing / other — don't nag

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
                 HOME/"Library/Developer/Xcode/iOS DeviceSupport"]),
}
SCAN_DIRS = [HOME/"Downloads", HOME/"Desktop", HOME/"Documents", HOME/"Movies"]
BIG_THRESHOLD = 100 * 1024 * 1024

# ring segment + card accent per category — monochromatic maroon shades
SEG = {"system":"#5E1622", "browser":"#8A2A38", "dev":"#B5606A"}

CARD_HELP = {
    "system":  "App caches & log files macOS rebuilds automatically. Safe to delete — frees space, apps just re-cache.",
    "browser": "Cached web data for Chrome/Safari/Firefox. You stay logged in; pages just re-download once.",
    "dev":     "Build caches from npm, pip, Homebrew, Xcode. Safe — they regenerate on next build/install.",
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
        tk.Label(self.tip, text=self.text, bg="#2c1014", fg="#ffffff", font=(UIFONT, 10),
                 padx=9, pady=6, justify="left", wraplength=280).pack()
    def _hide(self, _e):
        if self.tip: self.tip.destroy(); self.tip = None


# ════════════════════════════════════════════════════════════════════════
class Cleaner(tk.Tk):
    def __init__(self):
        super().__init__()
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
        tk.Label(name, text="ZH MacCleaner", bg=C["HEADER"], fg=C["MAROON"],
                 font=(UIFONT, 19, "bold")).pack(anchor="w")
        tk.Label(name, text="keep your Mac clean", bg=C["HEADER"], fg=C["MUTED"],
                 font=(UIFONT, 10)).pack(anchor="w", pady=(1,0))
        # subtle bottom divider
        tk.Frame(self, bg=C["BORDER"], height=1).pack(fill="x")

        body = tk.Frame(self, bg=C["BG"]); body.pack(fill="both", expand=True)

        # Sidebar
        side = tk.Frame(body, bg=C["SIDEBAR"], width=180); side.pack(side="left", fill="y"); side.pack_propagate(False)
        self.active_view = None
        nav = [("cleanup","Cleanup","🧹"), ("large","Large Files","📦"),
               ("uninstall","Uninstaller","🗑️"), ("dupes","Duplicates","👯"),
               ("maint","Maintenance","🛠"), ("license","Pro","⭐"), ("help","Help & About","ℹ️")]
        for key, label, ico in nav:
            b = tk.Label(side, text=f"   {ico}   {label}", bg=C["SIDEBAR"], fg=C["TEXT"],
                         font=(UIFONT, 13), anchor="w", cursor="pointinghand", padx=12, pady=11)
            b.pack(fill="x", padx=8, pady=2)
            b.bind("<Button-1>", lambda e,k=key: self.show_view(k))
            b.bind("<Enter>", lambda e,k=key,w=b: (w.config(bg="#e3d0d4") if k!=self.active_view else None))
            b.bind("<Leave>", lambda e,k=key,w=b: (w.config(bg=C["SIDEBAR"]) if k!=self.active_view else None))
            self.nav_btns[key] = b
        tk.Label(side, text="v1.0 · safe mode", bg=C["SIDEBAR"], fg=C["BORDER"],
                 font=(UIFONT, 9)).pack(side="bottom", pady=12)

        # Content area
        self.content = tk.Frame(body, bg=C["BG"]); self.content.pack(side="left", fill="both", expand=True)
        self._build_cleanup()
        self._build_large()
        self._build_uninstaller()
        self._build_duplicates()
        self._build_maintenance()
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
            tk.Button(ban, text="Open Settings", command=self.open_fda, highlightbackground=C["SURF2"],
                      fg=C["MAROON"], relief="flat", bd=0, padx=12, pady=5, cursor="pointinghand",
                      font=(UIFONT, 11, "bold")).grid(row=0, column=2, rowspan=2, padx=12)

        # Gauge
        top = tk.Frame(v, bg=C["BG"]); top.pack(fill="x", pady=(12,4))
        self.gauge = tk.Canvas(top, width=176, height=176, bg=C["BG"], highlightthickness=0)
        self.gauge.pack()
        self._draw_gauge()

        # Category cards
        mid = tk.Frame(v, bg=C["BG"]); mid.pack(fill="both", expand=True, padx=22)
        for key,(ico,name,sub,paths) in CATEGORIES.items():
            card = tk.Frame(mid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.pack(fill="x", pady=5); card.columnconfigure(3, weight=1)
            tk.Frame(card, bg=SEG[key], width=4).grid(row=0, column=0, rowspan=2, sticky="ns")  # accent bar
            var = tk.BooleanVar(value=True); self.vars[key] = var
            tk.Checkbutton(card, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=1, rowspan=2, padx=(12,4), pady=14)
            tk.Label(card, text=ico, bg=C["SURF"], font=(UIFONT, 18)
                     ).grid(row=0, column=2, rowspan=2, padx=6)
            tk.Label(card, text=name, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 14, "bold")).grid(row=0, column=3, sticky="w", pady=(12,0))
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], anchor="w",
                     font=(UIFONT, 10)).grid(row=1, column=3, sticky="w", pady=(0,12))
            szl = tk.Label(card, text="…", bg=C["SURF"], fg=C["GOLD"],
                           font=(MONO, 15, "bold")); szl.grid(row=0, column=4, rowspan=2, padx=20)
            self.size_lbls[key] = szl
            Tip(card, CARD_HELP[key])
            for wdg in [card] + list(card.winfo_children()):
                wdg.bind("<Enter>", lambda e,c=card: c.config(highlightbackground=C["MAROON2"]))
                wdg.bind("<Leave>", lambda e,c=card: c.config(highlightbackground=C["BORDER"]))

        self.trash_lbl = tk.Label(mid, text="🗑  Trash: …", bg=C["BG"], fg=C["MUTED"],
                                  font=(UIFONT, 11)); self.trash_lbl.pack(anchor="w", pady=(8,0))

        # Buttons
        bar = tk.Frame(v, bg=C["BG"]); bar.pack(fill="x", padx=22, pady=14, side="bottom")
        self.rescan_btn = self._btn(bar, "↻  Rescan", self.scan_all, "ghost"); self.rescan_btn.pack(side="left")
        self.clean_btn  = self._btn(bar, "✦  Clean Selected", self.clean_sel, "gold"); self.clean_btn.pack(side="left", padx=8)
        self.trash_btn  = self._btn(bar, "🗑  Empty Trash", self.empty_trash, "ghost"); self.trash_btn.pack(side="right")

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
        styles = {"gold":(C["GOLD"], "#3a2410"), "ghost":(C["SURF2"], C["TEXT"]),
                  "danger":(C["RED"], "#fff")}
        bg, fg = styles[kind]
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
                         padx=16, pady=9, cursor="pointinghand", activebackground=C["GOLD2"],
                         font=(UIFONT, 12, "bold"))

    def open_fda(self):
        subprocess.run(["open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"])
        self.q.put(("status", "Add ZH MacCleaner in the list, then relaunch the app."))

    def _draw_gauge(self, frac=None, total=None):
        g = self.gauge; g.delete("all")
        x0,y0,x1,y1 = 16,16,160,160; cx,cy = 88,88
        g.create_oval(x0,y0,x1,y1, outline=C["SURF2"], width=10)   # track
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
        fs = 23 if len(txt) <= 7 else (19 if len(txt) <= 9 else 16)
        g.create_text(cx, cy-11, text=txt, fill=C["TEXT"], font=(UIFONT, fs, "bold"))
        g.create_text(cx, cy+17, text="RECLAIMABLE", fill=C["MUTED"], font=(UIFONT, 9, "bold"))

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
        for k,b in self.nav_btns.items():
            if k == name: b.config(bg=C["MAROON"], fg="#ffffff", font=(UIFONT, 13, "bold"))
            else:         b.config(bg=C["SIDEBAR"], fg=C["TEXT"], font=(UIFONT, 13))

    # ── queue pump ──
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if   kind == "status": self.status.config(text=payload)
                elif kind == "size":   self._set_size(*payload)
                elif kind == "gauge":  self._draw_gauge()
                elif kind == "trash":  self.trash_lbl.config(text=payload)
                elif kind == "big":    self._render_big(payload)
                elif kind == "busy":   self._set_busy(payload)
                elif kind == "apps":   self._render_apps(payload)
                elif kind == "uninstall_confirm": self._confirm_uninstall(*payload)
                elif kind == "dupes":  self._render_dupes(payload)
                elif kind == "rescan_dupes": self.scan_dupes()
                elif kind == "update": self._show_update(*payload)
                elif kind == "gauge_anim": self._animate_gauge()
                elif kind == "maint_done": self._maint_done(*payload)
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
        for x in ("rescan_btn","clean_btn","trash_btn","find_btn","trash_sel_btn"):
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
        self.q.put(("busy", True)); self.q.put(("status","Cleaning…"))
        def run():
            freed = 0
            for k in picks:
                for p in CATEGORIES[k][3]:
                    if p.exists(): freed += clear_contents(p)
                self.q.put(("size",(k,0)))
            self.q.put(("status", f"✅ Freed {human(freed)}."))
            self.q.put(("busy", False))
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
            row = tk.Frame(self.binner, bg=C["SURF"]); row.pack(fill="x", padx=6, pady=1)
            row.columnconfigure(1, weight=1)
            var = tk.BooleanVar(value=False); self.big_vars[fp] = var
            tk.Checkbutton(row, variable=var, bg=C["SURF"], selectcolor=C["MAROON"],
                           activebackground=C["SURF"], bd=0, highlightthickness=0
                           ).grid(row=0, column=0, sticky="w")
            nm = os.path.basename(fp); disp = (nm[:44]+"…") if len(nm)>45 else nm
            tk.Label(row, text=disp, bg=C["SURF"], fg=C["TEXT"], anchor="w",
                     font=(UIFONT, 11)).grid(row=0, column=1, sticky="w", padx=4)
            tk.Label(row, text=f"{human(sz)} · {int((now-mt)/86400)}d", bg=C["SURF"], fg=C["GOLD"],
                     font=(MONO, 11)).grid(row=0, column=2, sticky="e", padx=12)

    def trash_big(self):
        if self.busy: return
        picks = [fp for fp,v in self.big_vars.items() if v.get()]
        if not picks: messagebox.showinfo("ZH Cleaner","No files selected."); return
        tot = sum(sz for fp,sz,_ in self.big_files if fp in picks)
        if not messagebox.askyesno("Move to Trash?",
            f"Move {len(picks)} file(s) ({human(tot)}) to Trash?\nRecoverable from Trash."): return
        self.q.put(("busy", True))
        def run():
            for fp in picks: move_to_trash(fp)
            self.q.put(("big", [x for x in self.big_files if x[0] not in picks]))
            self.q.put(("status", f"✅ Moved {len(picks)} file(s) to Trash."))
            self.q.put(("busy", False)); self._trash_size()
        threading.Thread(target=run, daemon=True).start()

    # ══ scrollable list helper ══
    def _scroller(self, parent):
        wrap = tk.Frame(parent, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=22, pady=6)
        cv = tk.Canvas(wrap, bg=C["SURF"], highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=C["SURF"])
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0,0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        return inner

    def _title(self, parent, text, sub=""):
        f = tk.Frame(parent, bg=C["BG"]); f.pack(fill="x", padx=22, pady=(18,4))
        tk.Label(f, text=text, bg=C["BG"], fg=C["TEXT"], font=(UIFONT, 16, "bold")).pack(anchor="w")
        if sub: tk.Label(f, text=sub, bg=C["BG"], fg=C["MUTED"], font=(UIFONT, 10)).pack(anchor="w")
        return f

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
            tk.Button(row, text="Uninstall", command=lambda n=nm,p=path: self.uninstall_app(n,p),
                      bg=C["SURF2"], fg=C["MAROON"], relief="flat", bd=0, padx=10, pady=3,
                      cursor="pointinghand", font=(UIFONT, 10, "bold")).grid(row=0, column=1, padx=6)

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
            move_to_trash(path)
            for p in left: move_to_trash(str(p))
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
            for p in picks: move_to_trash(p)
            self.q.put(("status", f"✅ {len(picks)} duplicate(s) → Trash."))
            self.q.put(("busy", False)); self._trash_size()
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
        ]
        for i,(ico,name,sub,tip,cmd) in enumerate(tools):
            card = tk.Frame(grid, bg=C["SURF"], highlightbackground=C["BORDER"], highlightthickness=1)
            card.grid(row=i//2, column=i%2, sticky="nsew", padx=6, pady=6)
            grid.columnconfigure(i%2, weight=1)
            tk.Label(card, text=ico, bg=C["SURF"], font=(UIFONT, 22)).pack(pady=(12,2))
            tk.Label(card, text=name, bg=C["SURF"], fg=C["TEXT"], font=(UIFONT, 13, "bold")).pack()
            tk.Label(card, text=sub, bg=C["SURF"], fg=C["MUTED"], font=(UIFONT, 9)).pack()
            self._btn(card, "Run", cmd, "gold").pack(pady=10)
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
        try:
            body = urllib.parse.urlencode({
                "key": key, "app": "maccleaner", "device": device_id(), "v": APP_VERSION}).encode()
            req = urllib.request.Request(LICENSE_URL, data=body, headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded"})   # so PHP fills $_POST
            data = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
            return bool(data.get("valid")), (data.get("plan") or "pro"), (data.get("message") or "")
        except Exception as e:
            return None, None, str(e)           # None = couldn't reach server

    def _reverify_license(self):
        key = self.lic.get("key")
        if not key: return
        def run():
            ok, plan, _ = self._verify_online(key)
            if ok is None: return               # offline → keep cached within grace
            self.lic.update({"valid": bool(ok), "plan": plan or "free", "checked": time.time()})
            self._save_license(); self.q.put(("license_changed", None))
        threading.Thread(target=run, daemon=True).start()

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
                 font=(UIFONT, 18, "bold")).pack(anchor="w", padx=14, pady=(8,2))
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

        tk.Label(inner, text="License key", bg=C["SURF"], fg=C["TEXT"], anchor="w",
                 font=(UIFONT, 12, "bold")).pack(fill="x", padx=14, pady=(14,2))
        row = tk.Frame(inner, bg=C["SURF"]); row.pack(fill="x", padx=14)
        self.key_entry = tk.Entry(row, font=(MONO, 12), relief="flat",
                                  bg=C["BG"], fg=C["TEXT"], insertbackground=C["TEXT"])
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0,8))
        self._btn(row, "Activate", lambda: self.activate_license(self.key_entry.get()), "gold").pack(side="right")

        buy = tk.Label(inner, text="Get a license at zhmotions.com/maccleaner", bg=C["SURF"],
                       fg=C["MAROON2"], font=(UIFONT, 11, "underline"), cursor="pointinghand")
        buy.pack(anchor="w", padx=14, pady=14)
        buy.bind("<Button-1>", lambda e: subprocess.run(["open", SITE+"/maccleaner"]))
        self._refresh_license_ui()

    def _refresh_license_ui(self):
        if not hasattr(self, "lic_status"): return
        if self.is_pro():
            self.lic_status.config(text="● PRO — active ✓", fg=C["GREEN"])
            self.key_entry.delete(0, "end"); self.key_entry.insert(0, self.lic.get("key",""))
        else:
            self.lic_status.config(text="○ Free version", fg=C["MUTED"])
        # nav star reflects status
        if "license" in self.nav_btns:
            self.nav_btns["license"].config(text="   ⭐   " + ("Pro ✓" if self.is_pro() else "Pro"))

    # ══ HELP & ABOUT ══
    def _build_help(self):
        v = tk.Frame(self.content, bg=C["BG"]); self.views["help"] = v
        inner = self._scroller(v)
        def section(title, body):
            tk.Label(inner, text=title, bg=C["SURF"], fg=C["MAROON"], anchor="w",
                     font=(UIFONT, 13, "bold")).pack(fill="x", padx=14, pady=(12,2))
            tk.Label(inner, text=body, bg=C["SURF"], fg=C["TEXT"], anchor="w", justify="left",
                     font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14, pady=(0,4))

        tk.Label(inner, text="What is ZH MacCleaner?", bg=C["SURF"], fg=C["TEXT"],
                 font=(UIFONT, 16, "bold")).pack(anchor="w", padx=14, pady=(14,2))
        tk.Label(inner, text="A safe, simple Mac cleaner. It frees disk space by removing junk that "
                 "your Mac rebuilds automatically — and never touches system files.",
                 bg=C["SURF"], fg=C["MUTED"], anchor="w", justify="left",
                 font=(UIFONT, 11), wraplength=520).pack(fill="x", padx=14)

        section("🧹  Cleanup", "Deletes app caches, logs and browser caches. These regenerate on their "
                "own — safe to remove. Tick what you want and press “Clean Selected”.")
        section("📦  Large Files", "Finds files over 100 MB in Downloads, Desktop, Documents & Movies. "
                "Pick the ones you don't need — they go to the Trash (recoverable).")
        section("🗑️  Uninstaller", "Removes an app AND its leftover files (caches, preferences, support "
                "folders) that normally stay behind when you drag an app to the Trash.")
        section("👯  Duplicates", "Finds identical files (same content). Keeps the first copy, lets you "
                "trash the extras to reclaim space.")
        section("🛠  Maintenance — what each tool does",
                "•  Free Up RAM — purges inactive memory so apps get more free RAM. Use when your Mac feels slow.\n"
                "•  Flush DNS — clears the DNS cache. Fixes sites that won't load or point to an old server.\n"
                "•  Reindex Spotlight — rebuilds the search index. Fixes Spotlight missing files or wrong results.\n"
                "•  Rebuild Launch DB — fixes duplicate or wrong “Open With” app entries.\n\n"
                "Some ask for your Mac password (normal for system tasks). You get a popup with the result.")

        section("🔒  Is it safe?", "Yes. ZH MacCleaner only touches a fixed list of safe user folders. "
                "Caches/logs are rebuilt by macOS; your own files go to the Trash so you can restore them. "
                "It never deletes documents, photos or system files.")
        section("💡  Seeing small cache sizes?", "Grant Full Disk Access so it can read all caches: "
                "System Settings → Privacy & Security → Full Disk Access → + → add ZH MacCleaner.")

        # Branding footer
        brand = tk.Frame(inner, bg=C["SURF"]); brand.pack(fill="x", padx=14, pady=18)
        if self.logo_img:
            tk.Label(brand, image=self.logo_img, bg=C["SURF"]).pack(side="left", padx=(0,10))
        col = tk.Frame(brand, bg=C["SURF"]); col.pack(side="left")
        tk.Label(col, text="ZH MacCleaner  ·  v1.0", bg=C["SURF"], fg=C["MAROON"],
                 font=(UIFONT, 12, "bold")).pack(anchor="w")
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
                    data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
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
                        self.q.put(("update", (latest, dl, notes)))
                    elif not silent:
                        self.q.put(("status", f"✅ You're on the latest (v{APP_VERSION})."))
                    return  # first source that answered wins
                except Exception:
                    continue
            if not silent:
                self.q.put(("status", "⚠ Update check failed (no internet or site offline)."))
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _is_newer(a, b):
        def parts(v): return [int(x) for x in v.split(".") if x.isdigit()]
        return parts(a) > parts(b)

    def _show_update(self, latest, url, notes):
        if messagebox.askyesno("Update available",
            f"ZH MacCleaner v{latest} is available (you have v{APP_VERSION}).\n\n"
            f"{notes}\n\nDownload from zhmotions.com now?"):
            subprocess.run(["open", url])


if __name__ == "__main__":
    if sys.platform != "darwin":
        print("ZH Cleaner is built for macOS.")
    Cleaner().mainloop()
