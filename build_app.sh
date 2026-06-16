#!/usr/bin/env bash
# ───────────────────────────────────────────────
# Build "ZH MacCleaner.app" with PyInstaller — embeds Python + Tk 9.0.
# Result: a real Mach-O app (own binary) → shows as "ZH MacCleaner" in
# Full Disk Access, and needs NO Python installed on the user's Mac.
# ───────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# 1. Build venv with a modern-Tk Python + PyInstaller (first run only)
if [ ! -d .build-venv ]; then
  echo "▸ Creating build venv…"
  /opt/homebrew/bin/python3.12 -m venv .build-venv
fi
source .build-venv/bin/activate
pip install -q --upgrade pip pyinstaller >/dev/null 2>&1 || true

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
pyinstaller --noconfirm --windowed --name "ZH MacCleaner" \
  --icon AppIcon.icns --osx-bundle-identifier com.zhmo.maccleaner \
  --add-data "assets:assets" zh_cleaner.py >/dev/null

APP="dist/ZH MacCleaner.app"

# 4. Ad-hoc codesign — CRITICAL for Apple Silicon.
#    Without any signature, macOS calls a downloaded app "damaged" with no easy bypass.
#    An ad-hoc signature turns that into the normal "unidentified developer" prompt,
#    which buyers can clear with a one-time right-click → Open.
echo "▸ Ad-hoc signing…"
xattr -cr "$APP" 2>/dev/null || true
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP" >/dev/null 2>&1 && echo "  signed ✓" || echo "  ⚠ verify failed"

# 5. DMG (what buyers download).
echo "▸ DMG…"
rm -f "dist/ZH-MacCleaner.dmg"
hdiutil create -volname "ZH MacCleaner" -srcfolder "$APP" -ov -format UDZO "dist/ZH-MacCleaner.dmg" >/dev/null
xattr -cr "dist/ZH-MacCleaner.dmg" 2>/dev/null || true

# 6. .pkg installer — double-click installs straight to /Applications (no drag).
echo "▸ PKG…"
VER="$(/usr/bin/python3 -c "import re,io;print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"',open('zh_cleaner.py').read()).group(1))" 2>/dev/null || echo 1.0)"
PKGROOT="$(mktemp -d)/root"
mkdir -p "$PKGROOT/Applications"
cp -R "$APP" "$PKGROOT/Applications/"
pkgbuild --root "$PKGROOT" --identifier com.zhmo.maccleaner --version "$VER" --install-location / "dist/ZH-MacCleaner.pkg" >/dev/null
xattr -cr "dist/ZH-MacCleaner.pkg" 2>/dev/null || true

echo "✅ Built: $HERE/$APP"
echo "✅ DMG  : $HERE/dist/ZH-MacCleaner.dmg  → upload to zhmotions.com/maccleaner/"
echo "✅ PKG  : $HERE/dist/ZH-MacCleaner.pkg  (v$VER, auto-installs to /Applications)"
echo ""
echo "ℹ️  First-open for buyers: right-click the app (or .pkg) → Open → Open (one time)."
echo "    Or in Terminal: xattr -cr \"/Applications/ZH MacCleaner.app\""
