# Plan

Written 2026-10-16. Chosen direction: **a full Windows app** for the PC with the RTX 5060 Ti (16 GB VRAM), alongside the Mac app.

## 0. Where things stand

- The voice picker now covers two models. Picking a voice loads its model and unloads the other one (`MODELS` in `tts_daemon.py`).
  - **Qwen3-TTS 1.7B (8-bit)** is the default. It has 9 voices and follows the Style box.
  - **Voxtral TTS 4B (4-bit)** is from Mistral. It has 15 voices (English, Spanish, French, German, Italian, Portuguese). Each voice speaks its own language, and the model ignores the Style box. Its weights are licensed **CC BY-NC**, so personal use only.
- Generation speed measured on the M4 (16 GB), as × real time (above 1 keeps up with playback):

  | Model | × real time | Smooth for long reads? |
  |---|---|---|
  | Qwen3-TTS 1.7B 8-bit | 1.6–1.7 | Yes |
  | Voxtral 4B 6-bit | 0.71 | No |
  | Voxtral 4B 4-bit | 0.80–0.84 | No: playback stops and waits for audio |

- [ ] **First, by ear:** compare Voxtral `Spanish, female/male` with Qwen on the same Spanish paragraphs, with a fixed seed. The result decides which model the Windows app starts with.

## 1. Windows app (full)

### 1.1 Split the code first (on the Mac, no behaviour change)

`tts_daemon.py` is a single 1,100-line file with AppKit throughout. Split it so both platforms share one core:

| File | Contents | Platform |
|---|---|---|
| `core/text.py` | Splitting into passages, language detection, sentence and heading rules | any |
| `core/pdf.py` | Header/footer/footnote removal, paragraph rebuilding, hyphenation (works on a list of lines with positions, not on PDFKit objects) | any |
| `core/engine.py` | Generator thread, player, speed control, silent-stall recovery, the `MODELS` table | any |
| `core/server.py` | HTTP API on 127.0.0.1:8765 (the Chrome extension keeps working unchanged) | any |
| `mac/` | rumps menu bar, AppKit windows, PDFKit line extraction, Vision OCR, Services, `build_app.sh` | macOS |
| `win/` | Tray icon, window, PDF lines, OCR, packaging | Windows |

Done when `test_pdf.py` passes and the Mac app behaves exactly as before. **Done 2026-10-16:** self-check and `test_pdf.py` pass, PDF text is identical to before on two real PDFs, and the app reads through the HTTP API. The engine takes the voice backend by module name (`mac.voice` here; a CUDA one on Windows), and the platform hooks are `engine.notify`, `extract.pdf_text` and `extract.rich_text`.

### 1.2 Windows pieces

| Mac piece | Windows replacement |
|---|---|
| `mlx-audio` (MLX) | PyTorch + CUDA (cu128 wheels, needed for RTX 50-series). Qwen3-TTS: `qwen-tts` package. Voxtral: `transformers` or vLLM-omni (vLLM needs WSL2) |
| `rumps` menu bar | `pystray` tray icon with the same menu |
| AppKit reader/settings windows | `tkinter` (stdlib; `Text` widget tags for the highlight). If that looks too dated, a page served by the daemon in an app-mode Edge window |
| PDFKit text + positions | `pypdfium2` (same line positions, so `core/pdf.py` rules carry over) |
| Vision OCR for scans | Windows' built-in OCR (`winrt` Windows.Media.Ocr, Spanish + English packs) |
| Services (right-click → Read Aloud) | Global hotkey to read the selection (copy, then read), plus an Explorer "Send to" shortcut for files |
| `~/Library/...` | `%APPDATA%\LocalTTS` (config), `%LOCALAPPDATA%\LocalTTS` (cache, log) |
| `.app` bundle, `build_app.sh` | PyInstaller one-folder build + Start-menu shortcut; `uv run` for development |
| Chrome extension | Unchanged (same port and API) |

### 1.3 Models to run on the 5060 Ti

Ranks are from the Artificial Analysis open-weights board (October 2026). Check each licence before anything other than personal use.

| Model | Size | Spanish | Notes |
|---|---|---|---|
| Fish Audio Drama 3 Preview | 35B MoE | yes (83 languages) | Ranked #1. Probably too big for 16 GB even at 4-bit; check the quantised sizes |
| Fish Audio S2 Pro | — | yes | Ranked #3. Needs a commercial licence for business use |
| Voxtral TTS 4B | 4B | yes | Ranked #5. bf16 (~8 GB) fits; expect well above real time |
| Higgs Audio v3 | 4B | yes (100 languages) | Ranked #10. Voice cloning |
| Qwen3-TTS 1.7B | 1.7B | yes | Current model; bf16 on the GPU, and its Base variant for cloning (see 3.2) |

- [ ] Measure × real time and VRAM use for each on the PC before adding it to `MODELS`.

### 1.4 Order of work

1. Split the code (1.1) on the Mac.
2. On the PC: engine + HTTP server with Qwen3-TTS on CUDA, driven from the Chrome extension. This is the first useful milestone.
3. Tray icon and settings.
4. Reader window with highlighting and click-to-read.
5. PDF (pypdfium2) and OCR.
6. Hotkey, Send to, and packaging.
7. Add the bigger models (1.3).

## 2. Review this app and study the competition

### 2.1 Improvements to this app

- [ ] Close the open items under "Check by ear or by hand" in `todo.md` (headphones switching, clicks at streaming seams, generation speed on long passages).
- [ ] Run an over-engineering and bug audit on the split code once 1.1 is done, while it is fresh.
- [ ] Look at the log's "× real time" and "playback ran dry" lines from real use, and fix what stalls.

### 2.2 The competition (to study, then decide)

| App | What it does well that we lack |
|---|---|
| Speechify | Word-by-word highlighting; skips citations, headers and footnotes; OCR from the phone camera; up to 4.5× speed |
| ElevenReader | Most natural voices; turns a document into a podcast-style summary (GenFM); exports audio |
| NaturalReader | Handles many file formats and scans; pronunciation editor |
| Voice Dream Reader | Offline, accessibility-first; strong navigation (by sentence, paragraph, page, heading, bookmark) |

- [ ] Try each free tier on the same three documents: a scanned Spanish report, a two-column technical paper and a web article. Note what they get right that we don't.

Candidate features, roughly by value for effort:

1. **Navigation by heading and section**, plus bookmarks.
2. **Export to audio** (MP3 / M4B with chapters) for listening away from the computer (`todo.md` A11).
3. **Word-level highlighting**: get word timings by aligning the generated audio to its text (a small CTC or Whisper aligner; fast on the GPU).
4. **Pronunciation dictionary** and Spanish text normalisation: numbers, `$12.990`, dates, abbreviations, units.
5. **Summary or "listen to the key points" mode**, using a local LLM on the 5060 Ti.

## 3. New features

### 3.1 Smarter full-PDF processing

Today a PDF is extracted with hand-written rules and then read top to bottom. The smarter version prepares the whole document first and reads it as a structured document.

- [ ] **Layout model on the PC:** Docling, Marker or MinerU (pick by testing on our own PDFs). Each gives headings, reading order across columns, tables, figures and footnotes as structure, not as guesses. Keep the current rules as the fallback and on the Mac.
- [ ] **Decide what is spoken:** headings announced, tables summarised or skipped ("Table 3, five rows: skipped"), references and appendices skipped unless chosen, figure captions on request.
- [ ] **Optional LLM clean-up pass** (local, on the GPU): expand abbreviations, write numbers and units as spoken Spanish or English, describe a table in one sentence. Keep it optional and cached, since it changes the text.
- [ ] **Generate ahead and cache:** synthesise the whole document in the background into a per-passage audio cache (`todo.md` A3). Seeking and Back become instant and identical, a passage that comes out badly can be regenerated alone, and exporting the audiobook becomes a file copy.
- [ ] **Sections list** in the reader window from the detected headings and the PDF outline.

### 3.2 Same voice from paragraph to paragraph

Each passage is a separate generation, so the voice can change slightly between paragraphs in pitch, pace, loudness or mood. Ways to fix it, cheapest first:

1. **Fixed seed and lower temperature** (already in Settings). Measure how much they help before building more.
2. **Even out loudness and pace after generation:** normalise each passage to the same loudness, and measure its pace in characters per second. A passage far from the document's average gets regenerated or time-stretched, which `audiotsm` already does.
3. **Anchor the voice with a reference clip** (the strongest fix): Qwen3-TTS Base and Higgs Audio clone a voice from a few seconds of audio. Make one good clip of the chosen voice once, then generate every passage from that same clip, so the voice cannot drift. This also gives a custom Spanish voice. It is the "voice profiles" item in `todo.md`.
4. **Carry context across passages:** give the model the end of the previous passage, its text plus its last ~5 s of audio, as the prompt for the next one. This is what ElevenLabs' "request stitching" does. It gives smoother joins, but drift can build up, so combine it with the fixed reference in 3.
5. **Longer passages** where the model allows it (currently one paragraph, up to 1,400 characters), so there are fewer joins.

- [ ] Build a small check: generate the same 10 paragraphs, measure pitch, loudness and pace per passage, and report how far they spread. Use it to compare 1–4 with numbers, then confirm by ear.

## Not doing yet

- A PC-as-voice-server mode for the Mac. The full Windows app was chosen instead; the HTTP API makes it easy to add later.
- Running two models at once: they don't fit in 16 GB together.
- Pre-buffering so Voxtral runs smoothly on the Mac: generating ahead (3.1) covers this.

## Sources

- Speed figures measured in this repo on an M4 with 16 GB (October 2026).
- [Artificial Analysis open-weights TTS leaderboard](https://artificialanalysis.ai/text-to-speech/leaderboard/provider-voice/open-weights)
- [mlx-audio supported models](https://github.com/Blaizzy/mlx-audio)
- Competitor overviews (all written by vendors): [ElevenLabs: best read-aloud apps](https://elevenlabs.io/blog/the-best-read-aloud-text-to-speech-apps), [Audeus: Speechify alternatives](https://www.audeus.com/product-reviews/speechify-alternatives), [Audeus: NaturalReader alternatives](https://www.audeus.com/product-reviews/naturalreader-alternatives)
- Voice consistency: [ElevenLabs request stitching](https://elevenlabs.io/docs/cookbooks/text-to-speech/request-stitching), [BotTalk chunking strategy](https://docs.bottalk.io/docs/text-to-speech/automation-rules/audio-chunks-strategy), [ContextSpeech (arXiv 2307.00782)](https://arxiv.org/abs/2307.00782v1)
