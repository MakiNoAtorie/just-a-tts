# LocalTTS: review report

Date: 2026-10-12
Scope: every file in this folder (`tts_daemon.py`, `extension/`, `install_services.py`, `build_app.sh`, `make_icon.py`, `pyproject.toml`, `todo.md`), plus the mlx-audio 0.5.8 source installed in `.venv`, to check how the model is really being driven.

This was a static review. The shell I had is not macOS, so I could not run the app. Anything about live behaviour is marked **verify**.

---

## 0. The short version

1. **The model you chose has no Spanish voice.** The CustomVoice checkpoint ships 9 speakers: 2 English, 1 Japanese, 1 Korean and 5 Chinese. Any Spanish read is spoken by an English or Chinese speaker, so it comes out accented. This is the biggest gap against the goal of natural English *and* Spanish.
2. **Language is global and defaults to `auto`.** It should be detected for each passage and passed explicitly to the model.
3. **If the model fails to load, the app hangs forever** on "Getting the voice ready…" and shows no error.
4. **There is no Edit menu**, so Cmd+V, Cmd+C and Cmd+A do not work in the Style box or the reading panel.
5. **There is no text normalisation.** Numbers, CLP prices (`$12.990`), dates, abbreviations and URLs reach the model raw. Spanish number formats are the worst case.
6. **The app has no way to start a read itself.** There is no "read clipboard", no "open file" and no text box. Everything depends on Services or Chrome.
7. **Back and forward regenerate the audio.** Nothing is cached, and long documents have no resume position.
8. **Generation speed against real time is never measured or handled.** If generation is slower than real time, playback stutters mid-passage.
9. **The Chrome extension cannot start the app**, but the Services menu can. This inconsistency is the source of the "isn't running" dead end.
10. **Project hygiene:** there is no git repo and no README with setup steps, and everything lives in one 600-line file. This is a large part of why it keeps feeling like "issue after issue".

---

## 1. Bugs (concrete, with locations)

| # | Where | What happens | Fix direction |
|---|---|---|---|
| B1 | `tts_daemon.py:176` | `load_model()` sits outside any `try`. A first-run download failure, being offline or a bad cache kills the generator thread. `ready` never becomes True, so the UI says "Getting the voice ready…" forever and reads queue up silently. | Catch the error, store it, show it in the status line and the menu bar, and offer Retry. |
| B2 | `tts_daemon.py:49-51` | A corrupt `config.json` (for example a half-written file) raises at import, so the app dies before any UI appears. | Validate on load and fall back to defaults. Write atomically (temp file, then rename). |
| B3 | `tts_daemon.py:498-513`, `341-342` | Unknown values in the config, such as a voice that does not exist after a model change, raise `KeyError` in the 0.25 s timer and `StopIteration` in `by_label`. The UI stops updating. | Sanitise config values against the current lists at startup. |
| B4 | `tts_daemon.py:544-549` | The custom main menu has no Edit menu, so copy, paste, select-all and undo key equivalents never reach text fields. You cannot paste a custom style. | Add a standard Edit menu (Undo, Cut, Copy, Paste, Select All) and a Window menu. |
| B5 | `tts_daemon.py:226-235` | WSOLA keeps a frame of audio buffered. `flush_to()` is never called, so at any speed other than 1.0 the last ~40-60 ms of each read is lost and the final word gets clipped. | Flush the TSM into the writer when the end-of-read marker arrives. |
| B6 | `tts_daemon.py:214` | The comment says a newly plugged-in output is picked up on the next open. PortAudio enumerates devices once at init, so the stream most likely keeps going to the old default device. **Verify:** launch, connect headphones, then read. | Re-initialise PortAudio (`sd._terminate(); sd._initialize()`) before reopening after idle, or listen for CoreAudio default-device changes. |
| B7 | `tts_daemon.py:46` | `SAMPLE_RATE = 24000` is hard-coded. That is fine for Qwen but breaks the moment a second engine is added. | Use `model.sample_rate`. |
| B8 | `tts_daemon.py:144` | The hyphen rejoin only matches `[a-z]`. Spanish words split before an accented letter or ñ stay broken (`compa-\nñía` becomes "compa ñía"). | Use a Unicode lowercase class, e.g. `[a-záéíóúüñ]`. |
| B9 | `tts_daemon.py:70` | The sentence split needs punctuation *immediately* before whitespace. Endings like `."`, `?”`, `.)` or `!»` never split, so dialogue-heavy text (fiction) forms chunks far above 250 characters. | Allow closing quotes and brackets after the terminal mark. |
| B10 | `tts_daemon.py:70-71` | Lines without terminal punctuation, such as headings, list items and table cells, are joined to the next sentence with a single space. "Chapter 3 The storm began…" is read with no pause. | Treat a line break after a line with no terminal punctuation as a sentence end, and add a longer pause there (see A7). |
| B11 | `tts_daemon.py:105-111`, `293-294` | Empty text still creates a read id. The Chrome bar flashes and vanishes, and Services "Read Aloud" with nothing selected does nothing. | Reject empty text with a message. |
| B12 | `tts_daemon.py:599` | If port 8765 is taken, for example by running `python tts_daemon.py` while the app is open, `ThreadingHTTPServer` raises before the UI starts and the app dies silently. | Detect it and show "LocalTTS is already running" (single-instance guard). |
| B13 | `tts_daemon.py:409` | Style is saved only on Enter or on picking from the list. Typing a style and clicking elsewhere loses it. | Save on end-editing as well. |
| B14 | `tts_daemon.py:198-200` | Any generation exception is printed to the log and the read just ends, with no explanation to you. | Keep the last error and show it in the status line, the Chrome bar and the panel. |
| B15 | `build_app.sh` (launcher) | `>>localtts.log` grows forever. | Truncate or rotate on launch. |
| B16 | `install_services.py:1` | The docstring says to run `pbs -update`, but the script never does. The shortcuts that `content.js` says it mirrors ("same keys as the macOS Services shortcuts") are not set anywhere. | Run `pbs -update` at the end and set or document the shortcuts (see S1). |
| B17 | `extension/content.js:342`, `todo.md` | `todo.md` says "Right-click actions start the app if it is not running". That is true for Services only; Chrome shows "isn't running, open it from Applications". | See E1. |

---

## 2. The voice problem (English + Spanish)

The CustomVoice model (`mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit`) can *speak* Spanish, but none of its preset speakers is a Spanish speaker. That is why Spanish sounds off no matter which voice you pick.

Your installed mlx-audio 0.5.8 already contains several other engines, so you can try them **without adding dependencies**:

| Option | Spanish voice | Notes |
|---|---|---|
| **Qwen3-TTS 1.7B Base (voice cloning)** | Any. You provide a ~10 s clean clip plus its transcript, one per language, with whatever accent you want (Chilean, neutral Latin American, etc.). | Same engine and same code path you already use. mlx-audio caches the encoded reference (`qwen3_tts.py:640`) and forces repetition_penalty ≥ 1.5 in clone mode (`:1241`). **The most memory-efficient fix: one model, two clips.** |
| Qwen3-TTS 1.7B VoiceDesign | Described in words ("a warm Latin American Spanish male narrator") | Community reports call VoiceDesign the weakest of the three Qwen variants, and a described voice is less consistent between passages. |
| Voxtral 4B (`voxtral_tts`) | Presets `es_male` and `es_female` | CC-BY-NC, which is fine for personal use. The bf16 weights are ~8 GB, which is heavy on 16 GB. **Verify** speed. |
| Chatterbox Multilingual v3 (`chatterbox`) | Needs a reference clip; separate Spain and Latin American variants exist | Adds a watermark. Its earlier version had known Castilian pronunciation issues. |
| Kokoro (`kokoro`) | `ef_` and `em_` voices | Very fast but clearly the weakest Spanish. Good as a "fast mode" or fallback only. |

**Gap to fill: voice profiles.** A profile is engine + model + speaker *or* reference clip + language. Keep one profile for English and one for Spanish, and choose between them per passage by detected language (section 3). With Qwen Base, both profiles share one loaded model.

**Decision for you:** listen to the same Spanish paragraph from Qwen Base (cloned from a clip you like), Voxtral `es_*` and Chatterbox v3 before building anything around one of them.

---

## 3. Language handling

- The default is `auto` (line 49), and the language is one global setting for every passage. The Qwen docs recommend passing the language explicitly.
- Mixed documents, such as Spanish text with English terms or quotes, or a bilingual page, need **per-passage** language.
- **Gap:** a tiny en/es detector. A stopword-ratio check has no dependency and needs effectively no memory, and is enough for two languages. Pass the result as `lang_code` and use it to pick the voice profile.
- **UI change:** replace the single Language popup with "English voice: … / Spanish voice: …" and add a per-read override ("Force language for this read").

---

## 4. Text pipeline gaps

### 4.1 Normalisation (language-aware)
Nothing is normalised today. Listen first and build the rules from the failures you actually hear, because the model handles some cases itself. The usual offenders are:

- Number formats differ by language. In Spanish `1,5` and `12.990` mean 1.5 and 12990; in English the separators are reversed.
- Currency: `$12.990` is CLP in Chile and should be read as "doce mil novecientos noventa pesos". `US$`, `€` and `%` need the same treatment.
- Dates and times: `12/10/2026` is dd/mm in Spanish context. `10:30`, `14 h`.
- Ordinals: `1.º`, `1.ª`, `2do`, `1st`. Roman numerals in titles: "Capítulo IV", "Henry VIII".
- Units: `km`, `kg`, `m²`, `°C`.
- Abbreviations: `Sr.`, `Sra.`, `Dr.`, `pág.`, `etc.`, `e.g.`, `i.e.`, `vs.`. These also cause false sentence splits.
- Symbols: `&` becomes "y" or "and"; `→`, `/`, `|`.
- URLs and emails: skip them, or say "link".
- Emoji: drop. Markdown markers (`**`, `#`, backticks): strip. Code blocks: skip or announce.
- Academic citations like "(Smith et al., 2020)": option to skip.
- **Pronunciation dictionary** (a user-editable word → respelling list for each language) covers invented names in fiction, brand names and acronyms that should be spelled out or read as a word.

### 4.2 Chunking
- B9 and B10 above (closing quotes, headings).
- Sentences over 250 characters go to the model whole (a known ceiling). Long autoregressive runs drift in quality, and they delay audio after a skip. Split them at `;`, `,` and conjunctions as a second pass.
- **Make the first passage short** (one sentence) so a read starts fast. Later passages can be longer.
- Numbered items like `3. Results` can split off as a lone "three."

### 4.3 PDF
- PDFKit `string()` includes running headers, footers and page numbers on every page, plus footnotes, figure captions and sometimes two-column interleaving.
- Paragraph breaks: PDFKit rarely produces blank lines, and line 145 joins every single newline. Many PDFs therefore become one giant paragraph, which breaks heading detection and the panel display.
- Scanned PDFs: today you get only "It may be a scan." macOS Vision OCR (`VNRecognizeTextRequest` via `pyobjc-framework-Vision`) is local and good, so you could OCR instead.
- No page range or "start at page N", and no table of contents navigation.

### 4.4 Web pages
- `trafilatura.extract` drops the title by default, so reads start at the first paragraph. Read the title first.
- Tables, code, image alt text and figure captions get no special handling.
- The `/page` fallback fetch has no cookies and a fake user agent, so it often gets consent walls or 403s and reads "Accept cookies…".
- Google Docs renders to a canvas, so DOM-based reading does not work there. Note it in the help.

### 4.5 Inputs that are missing entirely
- Clipboard.
- Local files: `.txt`, `.md`, `.docx`, `.epub`, `.rtf`, `.pdf` through Open, drag-and-drop or Open With.

---

## 5. Audio and engine

- **A1. Real-time factor is unknown.** Log one row per passage (chars, audio seconds, generation seconds, RTF) to a CSV. Streaming chunks are played the moment they arrive, so if RTF approaches 1 playback underruns and stutters. Options: pre-buffer N seconds before playing, use a smaller or lower-bit model for live reading, and keep the 1.7B for export. **Verify** on the M4.
- **A2. Chunk seams.** `streaming_interval=1.0` and the decoded pieces are concatenated with no crossfade. Listen for clicks at roughly one-second intervals. **Verify.**
- **A3. No audio cache.** Back and forward regenerate audio, which is slow, and the result sounds *different* because of sampling (see A4). Cache generated audio for each passage of the current read; at 24 kHz float32 mono that is ~96 KB per second of audio. Back becomes instant and identical, and "replay sentence" comes for free.
- **A4. Consistency.** `temperature` is not passed, so it stays at mlx-audio's default of 0.9, with no seed. Prosody and character drift between passages. Try ~0.6-0.7 and/or a fixed seed per passage.
- **A5. Warm-up.** After loading, run one silent short generation so the first real read does not pay the Metal compile cost.
- **A6. Memory.** The model stays resident forever and the MLX buffer cache grows during long reads. Add "unload after N minutes idle", `mx.clear_cache()` after each read and a cache limit. This matters on 16 GB.
- **A7. Pauses.** The 0.45 s gap is the same everywhere, and it falls at chunk boundaries rather than at text structure. Use boundary-aware pauses (sentence < paragraph < heading) and leave chunk boundaries silent.
- **A8. Voice, language or style changes wait for the next passage** (a known ceiling). Easy fix: if a read is active, restart the current passage with `start(passages, current)` after the change.
- **A9. Speed.** WSOLA is fine up to ~1.5×, and 2× has audible artefacts. Add finer steps (1.1, 1.15, 1.2), which is where narration usually sits, and keys for speed up and down.
- **A10. Media keys and Now Playing.** Use `MPRemoteCommandCenter` and `MPNowPlayingInfoCenter` so the keyboard play/pause key and AirPods taps control reading, and Control Center shows what is being read.
- **A11. Export to audio file** (m4a with chapter markers). Useful for listening away from the Mac and for proofreading your own writing by ear.

---

## 6. UX decisions to make (with my recommendation)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Dock icon *and* menu bar item are both always present (`ActivationPolicyRegular`, line 540), even when launched with `--background` from Services. | Menu bar only (`LSUIElement`). Show a Dock icon only while a window is open by switching the activation policy at runtime. |
| D2 | The menu bar icon uses emoji (⏳🔈🔊⏸). | Template SF Symbols (`speaker.wave.2`, `pause.fill`, `hourglass`), which follow light and dark mode like every other menu bar item. |
| D3 | There is no way to start a read from inside the app. | Menu items "Read clipboard" and "Open file…", drag a file onto the icon or window, and a paste box in the reader. |
| D4 | The main window mixes settings, transport controls and help. | Split it into a **Reader** window (text, highlight, transport, progress) and a **Settings** window (voice profiles, speed, style, pronunciation, startup). The floating panel becomes the Reader. |
| D5 | The panel shows the *chunked* text (`"\n\n".join(parts)`, line 525), so paragraphs are re-broken every ~250 characters. | Show the original text with its original paragraphs, and map passage ranges onto it. |
| D6 | No seeking. | Click a sentence, in the panel or on the web page, to start reading from there. |
| D7 | Any new read, including "Play a sample", silently replaces the current one and loses your place. | The sample plays and then returns to the paused read. Keep "Recent reads" with a resume position. |
| D8 | Long documents show "passage 37 of 412" and nothing more. | Add a progress bar, time remaining and bookmarks or resume. |
| D9 | The default style is "lively, upbeat tone at a brisk pace" (`STYLES[0]`). | Default to neutral (empty) or the narrator style; "lively and brisk" gets tiring over a long document. |
| D10 | The voice list shows 9 voices, and 7 of them do not matter for English and Spanish. | Show voice profiles grouped by language (section 2). |
| D11 | Some settings apply at the next passage and some immediately. | Everything applies immediately (A8). |
| D12 | Failures are silent everywhere (Services `curl -sf … >/dev/null`, `catch {}` in the extension, B14). | One visible status or error line in the app, plus a macOS notification when a Service fails. |
| D13 | The first run downloads ~2 GB with no progress. | A first-run screen showing size, progress and an offline message (ties into B1). |
| D14 | No launch at login. | A toggle backed by `SMAppService`. |
| D15 | Shortcuts depend on you binding Services shortcuts by hand in System Settings. | Global hotkeys owned by the app for read selection, read clipboard, pause, stop, back and forward, and speed up and down, configurable in Settings. |
| D16 | Chrome shortcuts are a `keydown` listener (top frame only, can clash with page shortcuts, no effect when focus is in the omnibox). | Use `chrome.commands`, which you can rebind at `chrome://extensions/shortcuts` and which works regardless of page focus. |
| D17 | On-page controls exist only in the tab that started the read (a known ceiling). | Keep that, but add a toolbar **popup** with status and transport so you can control a read from any tab. |

---

## 7. Browser extension

- **E1. It cannot launch the app.** Register a URL scheme (`localtts://`, `CFBundleURLTypes` in Info.plist). When the daemon fetch fails, the extension opens that URL and retries for ~20 s, the same way the Services do. Native Messaging is the heavier alternative.
- **E2. The Origin check accepts *any* `chrome-extension://` origin** (`tts_daemon.py:255`). Pin it to your extension's ID; add a `"key"` to the manifest so the ID stays stable when loaded unpacked.
- **E3. `all_frames` is not set**, so selections inside iframes are not seen, and neither are keypresses while focus is in an iframe (noted in a comment).
- **E4. PDF fallback in `background.js:149`.** Whether the service-worker `fetch(tab.url)` carries the site's login cookies is **unverified**. `file://` PDFs need "Allow access to file URLs" enabled. If either fails, the error is swallowed silently.
- **E5. `catch {}` swallows every error** in `background.js`. Route failures to the on-page bar or a popup.
- **E6. The manifest has no icons**, so the toolbar shows a generic letter or puzzle icon.
- **E7. Version handshake.** The extension is "3.0" and the app is "1.0". Return the app version from `/state` so a mismatch can be reported.

---

## 8. macOS Services (`install_services.py`)

- **S1.** Installing is a manual script run, and `pbs -update` is not run (B16). Keyboard shortcuts are never set. Add an "Install right-click actions" button in Settings that writes the services, runs `pbs -update` and sets the shortcuts (the `pbs` `NSServicesStatus` defaults key), plus an uninstall option.
- **S2.** "Read Page Aloud" needs "Allow JavaScript from Apple Events" in the browser's Developer menu. That is documented only in a code comment. The first run also triggers an Automation permission prompt that names the Services runner, which is confusing without a note.
- **S3.** In browsers that do not support those AppleScript calls (e.g. Firefox), the page action does nothing and gives no message. Detect this and notify.
- **S4.** Every failure is silent (`curl -sf -o /dev/null`). Add `osascript -e 'display notification …'` on failure.

---

## 9. Packaging and build

- **P1.** The app depends on uv's Python in `~/.local/share/uv` and on the Hugging Face cache in your home folder (the script admits it). `uv python uninstall`, an upgrade or a cache clean breaks the app with no message. Either copy the Python runtime into the bundle or check for it at launch and say what is missing.
- **P2.** `Info.plist` is missing `CFBundleVersion`, `LSMinimumSystemVersion`, `NSHighResolutionCapable`, `LSUIElement` (if you take D1), `CFBundleDocumentTypes` (needed for Open With and drag-to-Dock with pdf/txt/md/epub/docx) and `CFBundleURLTypes` (needed for E1).
- **P3.** The executable is a shell script that `exec`s Python, so Activity Monitor and permission prompts say "python3.13". This gets worse once you need Accessibility or Input Monitoring for global hotkeys. A tiny compiled launcher plus ad-hoc signing (`codesign -s -`) keeps the identity stable, so permissions survive rebuilds.
- **P4.** `build_app.sh` deletes and rebuilds the app while it may be running. Quit it first (`osascript -e 'quit app "LocalTTS"'`).
- **P5.** The model id is hard-coded (`MODEL`, line 19). It should come from the voice profiles (section 2).

---

## 10. Project hygiene (the "issue after issue" part)

- **No git repository.** This is the first thing to fix: `git init`, plus a `.gitignore` for `.venv`, `__pycache__` and build output. Commit before every change so any regression is one `git diff` away.
- **No README.** Setup knowledge is scattered across comments. The real sequence is: `uv sync` → `build_app.sh` → `install_services.py` + `pbs -update` → set shortcuts → load the unpacked extension → enable "Allow JavaScript from Apple Events". Write it down once.
- **One 601-line file with six concerns:** extraction and text, engine, player, HTTP server, menu bar, and the AppKit windows. Splitting it keeps each problem small when you hand it to a coding agent:
  - `text.py` (extract, normalise, chunk, language detect)
  - `engine.py` (load, voice profiles, generate, cache)
  - `player.py`
  - `server.py`
  - `ui/` (menu bar, settings, reader)
- `selftest()` lives inside the app and mutates global state. Move it next to the split modules.
- `todo.md` is good. Fold this report's items into it so there is a single list.

---

## 11. Not verifiable from here (check on the Mac)

- RTF of the 1.7B 8-bit model on the M4 16 GB (A1)
- Whether output follows a newly connected headphone or AirPods device (B6)
- Clicks at the 1 s streaming seams (A2)
- Spanish quality for each candidate engine (section 2)
- Whether the Chrome background fetch of a PDF carries login cookies (E4)
- Memory footprint after a long read, before and after `mx.clear_cache()` (A6)
- Whether the rumps 0.25 s timer keeps firing while a popup menu is open (cosmetic)

---

## 12. Suggested order

1. **Stability:** git and README, then B1-B4 and B11-B14, and make errors visible (D12).
2. **The actual goal (natural Spanish):** run the voice comparison (section 2), then add voice profiles and per-passage language (section 3), then basic normalisation (numbers, currency, abbreviations) and the pronunciation dictionary (4.1).
3. **Reading long material:** audio cache (A3), resume and progress (D7, D8), clipboard and open-file input (D3), the reader window with the original layout and click-to-seek (D4-D6), and PDF cleanup (4.3).
4. **Polish:** menu-bar-only (D1, D2), global hotkeys and media keys (D15, A10), launch at login (D14), launching the app from the extension (E1), extension popup and commands (D16, D17), export to audio (A11), and packaging (section 9).
