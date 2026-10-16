# LocalTTS

Reads documents, web pages and selected text aloud on this Mac with a local model (MLX): Qwen3-TTS by default, or Mistral's Voxtral TTS, picked in the Voice menu. See `PLAN.md` for models and the Windows plan. Nothing leaves the machine.

## Set up

```bash
uv sync                      # Python packages into .venv
./build_app.sh               # builds /Applications/LocalTTS.app and registers it with macOS
.venv/bin/python install_services.py   # right-click Services; rerun after editing it
```

Then, once:

1. Open LocalTTS. The first run downloads the voice (~2 GB), so it needs the internet.
2. Shortcuts for the Services: System Settings → Keyboard → Keyboard Shortcuts → Services → Text / General. `extension/content.js` uses Control+Option+R (read), +P (pause) and +. (stop) in Chrome; give the Services the same keys.
3. Chrome or Brave: `chrome://extensions` → Developer mode → Load unpacked → the `extension/` folder. Reload it there after any change to the extension.
4. "Read Page Aloud" in Safari or Chrome (the Service, not the extension) needs Develop → Allow JavaScript from Apple Events. The first use asks for permission to control the browser.

## Use

- **The app.** File → Open (⌘O), or drop a file on the Dock icon, or Open With: PDF (scans are read by text recognition), text, Markdown, HTML, Word, RTF. Read Clipboard is ⇧⌘V.
- **Click anywhere in the text** to read from that point. Drag to select and copy as usual. For a PDF, type a page number in the Page box and press Return.
- A file opened again carries on from where you left it.
- **Any app:** select text → right-click → Services → Read Aloud.
- **Chrome:** right-click a page or a selection → Read aloud. Controls and highlighting appear on the page; the app must be running.
- Voice, language, speed and style: Settings (⌘,) or the menu bar item. Language "Auto-detect" picks English or Spanish paragraph by paragraph.
- Settings also has Temperature (lower is steadier, higher is livelier; default 0.9) and Seed (a number makes the same paragraph sound the same every time; empty, the default, is a new take each time).

## When something is wrong

- The status line in the window, the ⚠️ in the menu bar and a notification say what failed.
- Log: `~/Library/Logs/localtts.log` (previous run: `localtts.log.1`). Each passage logs its generation speed; below 1× real time, playback will stutter.
- `.venv/bin/python tts_daemon.py test` runs the self-check.
- The app uses uv's Python and the Hugging Face cache in your home folder. If either is removed, run `./build_app.sh` again.

## Files

- `tts_daemon.py`: the whole app (text extraction, model, player, HTTP server on 127.0.0.1:8765, windows).
- `extension/`: Chrome extension, a thin client of the HTTP server.
- `install_services.py`, `build_app.sh`, `make_icon.py`: installers.
- `todo.md`: what is done and what is left. `REVIEW_REPORT.md`: the review most of it came from.
