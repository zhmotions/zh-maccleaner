#!/usr/bin/env bash
# ───────────────────────────────────────────────
# Build "ZH MacCleaner.app" with PyInstaller — embeds Python + Tk 9.0.
# Result: a real Mach-O app (own binary) → shows as "ZH MacCleaner" in
# Full Disk Access, and needs NO Python installed on the user's Mac.
# ───────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# 1. Build venv. Prefer python.org's universal2 Python (Intel+Apple Silicon in one
#    binary — what buyers actually need); homebrew's python3.12 is arm64-only and
#    crashes on Intel Macs. python.org's installer puts it at this fixed path and
#    bundles a universal2 Tcl/Tk 8.6 alongside it — confirmed via `lipo -info` on
#    the interpreter, libtcl/libtk, and _tkinter.so before trusting it here.
PY_ORG=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
TARGET_ARCH=""
if [ -x "$PY_ORG" ] && lipo -info "$PY_ORG" 2>/dev/null | grep -q "x86_64 arm64\|arm64 x86_64"; then
  VENV=.build-venv-u2
  if [ ! -d "$VENV" ]; then
    echo "▸ Creating universal2 build venv (python.org)…"
    "$PY_ORG" -m venv "$VENV"
  fi
  TARGET_ARCH="--target-arch universal2"
else
  echo "⚠️  python.org universal2 Python not found at $PY_ORG — falling back to"
  echo "    homebrew (arm64-only; Intel-Mac buyers will crash on launch). Install"
  echo "    the python.org 3.12 universal2 .pkg from python.org/downloads/macos/"
  echo "    and re-run this script for a real release build."
  VENV=.build-venv
  if [ ! -d "$VENV" ]; then
    echo "▸ Creating build venv…"
    /opt/homebrew/bin/python3.12 -m venv "$VENV"
  fi
fi
source "$VENV/bin/activate"
pip install -q --upgrade pip pyinstaller certifi >/dev/null 2>&1 || true

# 2. App icon (.icns) from the Z-mark
echo "▸ Icon…"
/usr/bin/python3 - <<'PY'
from PIL import Image; import os
sz=1024; icon=Image.new("RGBA",(sz,sz),(0,0,0,0))
m=Image.open("assets/icon.png").convert("RGBA"); h=m.height
icon.alpha_composite(m.crop((0,0,h,h)).resize((sz,sz),Image.LANCZOS))
os.makedirs("icon.iconset",exist_ok=True)
for s in [16,32,64,128,256,512,1024]:
    icon.resize((s,s),Image.LANCZOS).save(f"icon.iconset/icon_{s}x{s}.png")
    icon.resize((s,s),Image.LANCZOS).save(f"icon.iconset/icon_{s//2}x{s//2}@2x.png")
PY
iconutil -c icns icon.iconset -o AppIcon.icns && rm -rf icon.iconset

# 3. Bundle
echo "▸ PyInstaller bundle…"
rm -rf build "dist/ZH MacCleaner.app" "ZH MacCleaner.spec"
pyinstaller --noconfirm --windowed --name "ZH MacCleaner" $TARGET_ARCH \
  --icon AppIcon.icns --osx-bundle-identifier com.zhmo.maccleaner \
  --hidden-import certifi --collect-data certifi \
  --add-data "assets:assets" zh_cleaner.py >/dev/null

APP="dist/ZH MacCleaner.app"

# 4. Ad-hoc codesign — CRITICAL for Apple Silicon.
#    Without any signature, macOS calls a downloaded app "damaged" with no easy bypass.
#    An ad-hoc signature turns that into the normal "unidentified developer" prompt,
#    which buyers can clear with a one-time right-click → Open.
#    NB: this repo lives on an iCloud-synced Desktop, which stamps every file with a
#    `com.apple.fileprovider.fpfs#P` xattr + FinderInfo that `xattr -c` cannot remove
#    and `codesign` rejects ("resource fork … not allowed"). So sign a copy made OFF
#    the synced tree with `ditto --norsrc --noextattr --noacl`, then ditto it back.
VER="$(/usr/bin/python3 -c "import re;print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"',open('zh_cleaner.py').read()).group(1))" 2>/dev/null || echo 1.0)"

# PyInstaller leaves CFBundleShortVersionString at 0.0.0 — stamp the real version
# into Info.plist BEFORE signing so "About this app" / the updater see it.
# plutil -replace adds-or-replaces (idempotent), unlike PlistBuddy Set/Add.
plutil -replace CFBundleShortVersionString -string "$VER" "$APP/Contents/Info.plist"
plutil -replace CFBundleVersion            -string "$VER" "$APP/Contents/Info.plist"

# Everything from signing on happens in a STAGE dir off the iCloud tree, then only
# the finished .app / .dmg / .pkg are dittoed back into dist/.
STAGE="$(mktemp -d)"
SAPP="$STAGE/ZH MacCleaner.app"

echo "▸ Ad-hoc signing (staged off iCloud)…"
ditto --norsrc --noextattr --noacl "$APP" "$SAPP"
codesign --force --deep --sign - "$SAPP"
codesign --verify --deep --strict "$SAPP" >/dev/null 2>&1 && echo "  signed ✓" || echo "  ⚠ verify failed"

echo "▸ DMG…"
hdiutil create -volname "ZH MacCleaner" -srcfolder "$SAPP" -ov -format UDZO "$STAGE/ZH-MacCleaner.dmg" >/dev/null

echo "▸ PKG…"
mkdir -p "$STAGE/root/Applications" "$STAGE/scripts"
ditto "$SAPP" "$STAGE/root/Applications/ZH MacCleaner.app"
cat > "$STAGE/scripts/postinstall" <<'POST'
#!/bin/sh
/usr/bin/xattr -cr "/Applications/ZH MacCleaner.app" 2>/dev/null || true
exit 0
POST
chmod +x "$STAGE/scripts/postinstall"
pkgbuild --root "$STAGE/root" --scripts "$STAGE/scripts" --identifier com.zhmo.maccleaner \
  --version "$VER" --install-location / "$STAGE/ZH-MacCleaner.pkg" >/dev/null

# publish the finished artifacts back into dist/
rm -rf "$APP" "dist/ZH-MacCleaner.dmg" "dist/ZH-MacCleaner.pkg"
ditto "$SAPP" "$APP"
cp "$STAGE/ZH-MacCleaner.dmg" "$STAGE/ZH-MacCleaner.pkg" dist/
rm -rf "$STAGE"

ARCH_LABEL="arm64-only (Intel Macs will crash — see the warning above)"
[ -n "$TARGET_ARCH" ] && ARCH_LABEL="universal2 (Intel + Apple Silicon)"
echo "✅ App : $HERE/$APP  ($ARCH_LABEL, ad-hoc signed)"
echo "✅ DMG : $HERE/dist/ZH-MacCleaner.dmg  → upload to zhmotions.com/maccleaner/"
echo "✅ PKG : $HERE/dist/ZH-MacCleaner.pkg  (v$VER, installs to /Applications)"
echo ""
echo "ℹ️  Local install:  sudo installer -pkg \"$HERE/dist/ZH-MacCleaner.pkg\" -target /"
echo "ℹ️  First open:      right-click the app → Open → Open (one time)"
