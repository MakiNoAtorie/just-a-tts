#!/bin/sh
# Builds LocalTTS.app (menu bar app wrapping tts_daemon.py) into /Applications, or the folder given as $1.
# The app carries its own code and packages; it still relies on uv's Python install and the
# Hugging Face model cache in your home folder.
set -e
cd "$(dirname "$0")"
APP="${1:-/Applications}/LocalTTS.app"
RES="$APP/Contents/Resources"
pkill -f "$RES/tts_daemon.py" || true  # the running app would otherwise keep using the files replaced below
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"
[ -f icon.icns ] || .venv/bin/python make_icon.py
cp tts_daemon.py icon.icns "$RES/"
cp -R core mac "$RES/"
uv venv -q --relocatable --python 3.13 "$RES/venv"
UV_PROJECT_ENVIRONMENT="$RES/venv" uv sync -q --frozen

cat > "$APP/Contents/MacOS/LocalTTS" <<'SH'
#!/bin/sh
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
L="$HOME/Library/Logs/localtts.log"
[ -f "$L" ] && mv -f "$L" "$L.1"  # keep this run and the one before; the log cannot grow without end
exec "$R/venv/bin/python" "$R/tts_daemon.py" "$@" >>"$L" 2>&1
SH
chmod +x "$APP/Contents/MacOS/LocalTTS"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>LocalTTS</string>
  <key>CFBundleIdentifier</key><string>com.localtts.app</string>
  <key>CFBundleExecutable</key><string>LocalTTS</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.1</string>
  <key>CFBundleVersion</key><string>1.1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <!-- The files LocalTTS reads: lets you drop them on its Dock icon or use Open With. -->
  <key>CFBundleDocumentTypes</key>
  <array><dict>
    <key>CFBundleTypeName</key><string>Document</string>
    <key>CFBundleTypeRole</key><string>Viewer</string>
    <key>LSHandlerRank</key><string>Alternate</string>
    <key>LSItemContentTypes</key>
    <array>
      <string>com.adobe.pdf</string>
      <string>public.plain-text</string>
      <string>net.daringfireball.markdown</string>
      <string>public.html</string>
      <string>public.rtf</string>
      <string>org.openxmlformats.wordprocessingml.document</string>
      <string>com.microsoft.word.doc</string>
      <string>org.oasis-open.opendocument.text</string>
    </array>
  </dict></array>
</dict>
</plist>
PLIST
# Tell macOS about the rebuilt app now, so "open -a LocalTTS" and Open With find it without a Finder visit.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
echo "built $APP"
