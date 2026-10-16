# Plan: better voices, and a Windows version for the GPU machine

Written 2026-10-16.

## Where things stand

- The voice picker now covers two models. Picking a voice loads its model and unloads the other one (`MODELS` in `tts_daemon.py`).
  - **Qwen3-TTS 1.7B (8-bit)** is the default. It has 9 voices and follows the Style box.
  - **Voxtral TTS 4B (4-bit)** is from Mistral. It has 15 voices (English, Spanish, French, German, Italian, Portuguese). Each voice speaks its own language, and the model ignores the Style box. Its weights are licensed **CC BY-NC**, so personal use only.
- Generation speed measured on the M4 (16 GB), as × real time (above 1 keeps up with playback):

  | Model | × real time | Smooth for long reads? |
  |---|---|---|
  | Qwen3-TTS 1.7B 8-bit | 1.6–1.7 | Yes |
  | Voxtral 4B 6-bit | 0.71 | No |
  | Voxtral 4B 4-bit | 0.80–0.84 | No: playback stops and waits for audio |

  On the Mac, Voxtral is fine for short selections and for judging how it sounds. For whole documents it needs the GPU machine.

## Step 1: listen before building more (Mac, now)

- [ ] Compare Voxtral `Spanish, female/male` with Qwen on the same Spanish paragraphs. Use a fixed seed.
- [ ] Decide on Voxtral's Spanish. If it is clearly better, the Windows work below is worth doing. If not, try Chatterbox Multilingual v3 or Higgs Audio v3 next. Both are already in mlx-audio, so each is one more entry in `MODELS`.

## Models worth running on the RTX 5060 Ti (16 GB VRAM)

Ranks are from the Artificial Analysis open-weights board (October 2026). Check each licence before anything other than personal use.

| Model | Size | Spanish | Notes |
|---|---|---|---|
| Fish Audio Drama 3 Preview | 35B MoE | yes (83 languages) | Ranked #1. Probably too big for 16 GB even at 4-bit; check the quantised sizes |
| Fish Audio S2 Pro | — | yes | Ranked #3. Needs a commercial licence for business use |
| Voxtral TTS 4B | 4B | yes | Ranked #5. Should run well above real time on CUDA |
| Higgs Audio v3 | 4B | yes (100 languages) | Ranked #10. Voice cloning |
| Qwen3-TTS 1.7B | 1.7B | yes | The current model, unquantised (bf16) on the GPU |

## Step 2: the Windows version (two ways to do it)

### Option A (recommended first): the PC as a voice server, the Mac app unchanged

The Mac keeps everything it already does well: windows, PDF extraction (PDFKit), OCR (Vision), Services and the Chrome extension. Only the generator calls the PC instead of MLX.

1. On the PC: a small Python HTTP server (stdlib `http.server`, like the daemon) that takes `{text, voice, temperature, seed}` and streams back 24 kHz float32 PCM chunks. It runs the models with PyTorch + CUDA (`transformers` / the model's own package; Voxtral also runs on vLLM-omni, which needs WSL2 on Windows).
2. On the Mac: a `"remote"` entry in `MODELS` with the PC's address. Its `generate()` reads the chunks over HTTP and yields them like an mlx-audio result, so the rest of the generator, the player, speed control and the seams code stay as they are.
3. Fall back to the local Qwen model, with a clear error, when the PC is off.

This is about 150 lines, and it gives every Mac feature the big models straight away.

### Option B: a full Windows app

Only worth it if you want to read on the PC itself. What has to change:

| Mac piece | Windows replacement |
|---|---|
| `mlx-audio` | PyTorch + CUDA (same models, different packages) |
| `rumps` menu bar, AppKit windows | `pystray` tray icon + a `tkinter` window, or a page served by the daemon itself |
| PDFKit text extraction | `pypdfium2` or PyMuPDF (`test_pdf.py` checks the rules still hold) |
| Vision OCR for scans | Windows' built-in OCR (`winrt` Windows.Media.Ocr) or Tesseract |
| macOS Services (right-click → Read Aloud) | "Send to" shortcut or a global hotkey |
| `build_app.sh` / `.app` bundle | PyInstaller `.exe`, or `uv run` + a Start-menu shortcut |
| `~/Library/...` paths | `%APPDATA%\LocalTTS`, `%LOCALAPPDATA%\LocalTTS` |

Unchanged: the HTTP API, the Chrome extension (it talks to `127.0.0.1:8765`), text splitting, language detection, `sounddevice` playback, `audiotsm` speed control.

Order of work: split `tts_daemon.py` into a platform-free core (text, generation, player, HTTP) and a `mac_ui.py`. Then write `win_ui.py` and a CUDA generator against the core. That split is already on the todo list.

## Not doing yet

- Running two models at once: they don't fit in 16 GB of unified memory together.
- Pre-buffering so Voxtral runs smoothly on the Mac: it would mean pausing between paragraphs (about 0.2 s of wait per second of speech). Option A fixes this properly.
- Picking the voice automatically by detected language (an English voice for English, a Spanish one for Spanish): this is the "voice profiles" item in `todo.md`. Do it once the Spanish voice is chosen.
