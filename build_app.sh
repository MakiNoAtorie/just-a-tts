#!/bin/sh
# Builds LocalTTS.app (menu bar app wrapping tts_daemon.py) into /Applications, or the folder given as $1.
# The app carries its own code and packages; it still relies on uv's Python install and the
# Hugging Face model cache in your home folder.
set -e
cd "$(dirname "$0")"
APP="${1:-/Applications}/LocalTTS.app"
RES="$APP/Contents/Resources"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"
[ -f icon.icns ] || .venv/bin/python make_icon.py
cp tts_daemon.py icon.icns "$RES/"
uv venv -q --relocatable --python 3.13 "$RES/venv"
UV_PROJECT_ENVIRONMENT="$RES/venv" uv sync -q --frozen

cat > "$APP/Contents/MacOS/LocalTTS" <<'SH'
#!/bin/sh
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$R/venv/bin/python" "$R/tts_daemon.py" "$@" >>"$HOME/Library/Logs/localtts.log" 2>&1
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
  <key>CFBundleShortVersionString</key><string>1.0</string>
</dict>
</plist>
PLIST
echo "built $APP"
