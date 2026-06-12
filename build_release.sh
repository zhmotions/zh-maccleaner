#!/usr/bin/env bash
# ───────────────────────────────────────────────
# Package ZH MacCleaner for a GitHub Release.
# Produces:  dist/ZH-MacCleaner-<ver>-macOS.zip  and  .dmg
# ───────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

VER="1.0"
APP="ZH MacCleaner.app"
mkdir -p dist

echo "▸ Building app…"
bash build_app.sh >/dev/null

echo "▸ Zipping (ditto, keeps app bundle intact)…"
rm -f "dist/ZH-MacCleaner-${VER}-macOS.zip"
ditto -c -k --sequesterRsrc --keepParent "$APP" "dist/ZH-MacCleaner-${VER}-macOS.zip"

echo "▸ Building DMG…"
rm -f "dist/ZH-MacCleaner-${VER}.dmg"
TMP="$(mktemp -d)"
cp -R "$APP" "$TMP/"
ln -s /Applications "$TMP/Applications"          # drag-to-install target
hdiutil create -volname "ZH MacCleaner" -srcfolder "$TMP" -ov -format UDZO \
  "dist/ZH-MacCleaner-${VER}.dmg" >/dev/null
rm -rf "$TMP"

echo ""
echo "✅ Release artifacts in dist/:"
ls -lh dist/ | awk 'NR>1{print "   "$9"  ("$5")"}'
echo ""
echo "Next: create a GitHub release and upload them, e.g.:"
echo "   gh release create v${VER} dist/* --title \"ZH MacCleaner ${VER}\" --notes-file RELEASE-NOTES.md"
