#!/usr/bin/env bash
# ───────────────────────────────────────────────
# Build "ZH MacCleaner.app" — real double-click Mac app (no PyInstaller).
# A .app = folder with Info.plist + launcher that runs python3 (modern Tk).
# ───────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

APP="ZH MacCleaner.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/assets"

# 1. Source + assets (icon + logo)
cp zh_cleaner.py "$APP/Contents/Resources/zh_cleaner.py"
cp assets/*.png "$APP/Contents/Resources/assets/" 2>/dev/null || true

# 2. Launcher (Finder-launched → no Terminal window). Prefer modern Tk 8.6/9.
cat > "$APP/Contents/MacOS/ZH MacCleaner" <<'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
for PY in /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$PY" ]; then
    TKV=$("$PY" -c 'import tkinter;print(tkinter.TkVersion)' 2>/dev/null || echo 0)
    case "$TKV" in 8.6|9.0|9.*) exec "$PY" "$DIR/zh_cleaner.py";; esac
  fi
done
exec /usr/bin/python3 "$DIR/zh_cleaner.py"
LAUNCH
chmod +x "$APP/Contents/MacOS/ZH MacCleaner"

# 3. App icon (.icns) from the Z-mark
ICON_LINE=""
if /usr/bin/python3 - <<'PY'
import sys, os
try: from PIL import Image
except Exception: sys.exit(1)
sz=1024; icon=Image.new("RGBA",(sz,sz),(0,0,0,0))
src="assets/icon.png" if os.path.exists("assets/icon.png") else "assets/logo.png"
if os.path.exists(src):
    m=Image.open(src).convert("RGBA"); h=m.height
    icon.alpha_composite(m.crop((0,0,h,h)).resize((sz,sz),Image.LANCZOS))
os.makedirs("icon.iconset",exist_ok=True)
for s in [16,32,64,128,256,512,1024]:
    icon.resize((s,s),Image.LANCZOS).save(f"icon.iconset/icon_{s}x{s}.png")
    icon.resize((s,s),Image.LANCZOS).save(f"icon.iconset/icon_{s//2}x{s//2}@2x.png")
print("ok")
PY
then
  iconutil -c icns icon.iconset -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null \
    && ICON_LINE="<key>CFBundleIconFile</key><string>AppIcon</string>" || true
  rm -rf icon.iconset
fi

# 4. Info.plist
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ZH MacCleaner</string>
  <key>CFBundleDisplayName</key><string>ZH MacCleaner</string>
  <key>CFBundleExecutable</key><string>ZH MacCleaner</string>
  <key>CFBundleIdentifier</key><string>com.zhmo.maccleaner</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  ${ICON_LINE}
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
  <key>NSAppleEventsUsageDescription</key><string>ZH MacCleaner moves files to Trash and empties Trash via Finder.</string>
</dict>
</plist>
PLIST

echo "✅ Built: $HERE/$APP"
