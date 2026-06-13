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

echo "✅ Built: $HERE/dist/ZH MacCleaner.app"
