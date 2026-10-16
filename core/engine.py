"""Settings, reading state, and the threads that turn passages into audio and play it."""

import importlib
import json
import os
import queue
import re
import threading
import traceback
from bisect import bisect_right
from pathlib import Path

from core.extract import file_text
from core.text import STOP, detect, pieces, utf16_ranges

# Picking a voice picks its model; the other model is unloaded, as two do not fit in 16 GB together.
MODELS = {
    "qwen": {
        "name": "Qwen",
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
        "voices": {  # model speaker id -> label shown to the user
            "ryan": "Ryan — English, male",
            "aiden": "Aiden — English, male",
            "ono_anna": "Anna — Japanese, female",
            "sohee": "Sohee — Korean, female",
            "serena": "Serena — Chinese, female",
            "vivian": "Vivian — Chinese, female",
            "uncle_fu": "Uncle Fu — Chinese, male",
            "dylan": "Dylan — Beijing Chinese, male",
            "eric": "Eric — Sichuan Chinese, male",
        },
    },
    # Speaks each voice's own language, and ignores the Style box. Weights are CC BY-NC: personal use only.
    # ponytail: 0.8x real time on an M4 (measured), so long reads stall; meant for a faster machine, see PLAN.md.
    "voxtral": {
        "name": "Voxtral",
        "repo": "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit",
        "voices": {
            "casual_male": "Casual — English, male",
            "casual_female": "Casual — English, female",
            "cheerful_female": "Cheerful — English, female",
            "neutral_male": "Neutral — English, male",
            "neutral_female": "Neutral — English, female",
            "es_male": "Spanish, male",
            "es_female": "Spanish, female",
            "fr_male": "French, male",
            "fr_female": "French, female",
            "de_male": "German, male",
            "de_female": "German, female",
            "it_male": "Italian, male",
            "it_female": "Italian, female",
            "pt_male": "Portuguese, male",
            "pt_female": "Portuguese, female",
        },
    },
}
VOICES = {f"{m}/{v}": f"{model['name']}: {label}" for m, model in MODELS.items() for v, label in model["voices"].items()}
LANGUAGES = {"auto": "Auto-detect"} | {
    l: l.capitalize()
    for l in ["english", "spanish", "japanese", "chinese", "korean", "german", "french", "italian", "portuguese", "russian"]
}
# Tone instructions for the model; the Style box also accepts anything typed in. Empty means neutral.
STYLES = [
    "Speak in a lively, upbeat tone at a brisk pace.",
    "Speak clearly and confidently, like a news presenter.",
    "Speak warmly and expressively, like an audiobook narrator.",
    "Speak calmly and gently.",
]
SPEEDS = [0.8, 1.0, 1.1, 1.15, 1.2, 1.25, 1.5, 2.0]
TEMPERATURES = [0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1]  # how freely the model varies its delivery; 0.9 is its own default
SAMPLE_TEXT = "Hi! This is how I sound with the current settings. Select some text anywhere and I will read it to you."
HINT = (
    "Open a PDF or another document with File → Open (⌘O), drop one on the Dock icon, "
    "or read the clipboard (⇧⌘V).\n"
    "In any app: select text, right-click, Services → Read Aloud.\n"
    "In Chrome: right-click a page or a selection → Read aloud.\n"
    "Click anywhere in the text to read from that point."
)
PAUSE_SECONDS = 0.45  # silence added between passages; the model leaves almost none itself
SAMPLE_RATE = 24000  # replaced by the model's own rate once it has loaded
CONFIG = Path.home() / "Library/Application Support/LocalTTS/config.json"

# seed: digits make the same passage sound the same every time; empty is a new take each time.
# recent: file path -> passage reached, so opening a file again carries on from there.
DEFAULTS = {
    "voice": "qwen/ryan",
    "language": "auto",
    "style": "",
    "speed": 1.0,
    "temperature": 0.9,
    "seed": "",
    "show_text": True,
    "recent": {},
}
cfg = dict(DEFAULTS)
try:  # a missing, damaged or outdated file falls back to the defaults instead of stopping the app
    cfg.update({k: v for k, v in json.loads(CONFIG.read_text()).items() if k in cfg and type(v) is type(cfg[k])})
except (OSError, ValueError, AttributeError):
    pass
cfg["voice"] = cfg["voice"] if "/" in cfg["voice"] else "qwen/" + cfg["voice"]  # saved before there were two models
for key, valid in (("voice", VOICES), ("language", LANGUAGES), ("speed", SPEEDS), ("temperature", TEMPERATURES)):
    if cfg[key] not in valid:
        cfg[key] = DEFAULTS[key]

gen = 0  # bumped on every start, skip and stop; work tagged with an older value is dropped
read_id = 0  # bumped only when new text is submitted, so callers can tell their read from a later one
gen_lock = threading.RLock()
jobs = queue.Queue()
audio = queue.Queue(maxsize=30)  # ~30 s of audio; stops generation running far ahead of playback
unpaused = threading.Event()
unpaused.set()
ready = False  # model loaded
active = False  # a read is in progress, playing or paused
passages = []  # spoken text of the document in the reader; kept after the read ends, so a click reads it again
current = 0  # index into passages of the one being played, or where reading stopped
# What the reader window shows: the text as submitted, where each passage is in it (UTF-16 start and length,
# and the starts alone), each passage's page for a PDF, and the file it came from.
doc = {"text": "", "ranges": [], "starts": [], "pages": [], "npages": 0, "path": None}
own_panel = True  # False when the caller (the browser extension) shows the read-along itself
error = ""  # why the last read failed or the voice did not load; cleared by the next read
busy = ""  # what is taking time before a read can start: opening a file, recognising a scan
notify = None  # set by the platform: shows a message to the user


def fail(message):
    """Record why a read failed, so every status display can show it, and tell the user now."""
    global error
    error = message
    print(message, flush=True)
    if own_panel and notify:  # the browser extension shows the error on the page instead
        notify(message)


def start(parts, at=0):
    """Read parts from index `at`, replacing whatever is being read. Empty parts means stop."""
    global gen, passages, current, active
    with gen_lock:
        gen += 1
        unpaused.set()
        active = bool(parts)
        if parts:
            passages, current = parts, at
            jobs.put((gen, parts, at))


def submit(text, page_starts=(), panel=True, blocks=False, path=None):
    """Put text in the reader and read it; a file opened before carries on from where it was left."""
    global read_id, own_panel, error, doc
    with gen_lock:
        read_id += 1
        own_panel, error = panel, ""
        found = pieces(text, blocks=blocks)
        if not found:
            start([])
            fail("There is no text to read.")
            return read_id
        ranges = utf16_ranges(text, found)
        doc = {
            "text": text,
            "ranges": ranges,
            "starts": [r[0] for r in ranges],
            "pages": [bisect_right(page_starts, p[0]) - 1 for p in found] if page_starts else [],
            "npages": len(page_starts),
            "path": path,
        }
        start([p[2] for p in found], min(cfg["recent"].get(path, 0), len(found) - 1))
        return read_id


def goto(index):
    """Read the document in the reader from passage `index`, whether or not it is being read now."""
    with gen_lock:
        if passages:
            start(passages, max(0, min(len(passages) - 1, index)))


def seek(location):
    """Read from the passage at a UTF-16 position in the reader's text: where the user clicked."""
    goto(bisect_right(doc["starts"], location) - 1)


def skip(by):
    with gen_lock:
        if active:
            goto(current + by)


def toggle_pause():
    if unpaused.is_set():
        unpaused.clear()
    else:
        unpaused.set()


def status():
    if not active:
        return "idle"
    return "loading" if not ready else "reading" if unpaused.is_set() else "paused"


def set_cfg(key, value):
    with gen_lock:
        if cfg[key] == value:
            return
        cfg[key] = value
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg))
        os.replace(tmp, CONFIG)  # a crash mid-write cannot leave half a file
        if active and key in ("voice", "language", "style", "temperature", "seed"):  # say the passage again, the new way
            paused = not unpaused.is_set()
            start(passages, current)
            if paused:
                unpaused.clear()


def remember(index):
    """Keep the place in the file being read (the last 20 files)."""
    path = doc["path"]
    if path:
        others = [(k, v) for k, v in cfg["recent"].items() if k != path][-19:]
        set_cfg("recent", dict(others) | {path: index})


def open_file(path):
    """Read a file aloud. Getting its text can take a while (a scanned PDF), so that happens off the main thread."""

    def work():
        global busy
        busy = f"Opening {Path(path).name}…"
        try:
            submit(*file_text(path), blocks=True, path=str(path))
        except Exception as e:
            traceback.print_exc()
            fail(f"Could not open {Path(path).name}: {e}")
        busy = ""

    threading.Thread(target=work, daemon=True).start()


def generator(backend):
    """backend: the name of the platform's module with load(repo) -> model, seed(n) and free(). Imported here,
    as it is slow to import, and the model wants to be used on the thread that loaded it (MLX)."""
    global ready, SAMPLE_RATE
    import numpy as np

    voice = importlib.import_module(backend)

    model = loaded = job = None
    while True:
        repo = MODELS[cfg["voice"].split("/")[0]]["repo"]
        if loaded != repo:
            ready, model, loaded = False, None, None
            voice.free()  # free the other model first
            try:
                model = voice.load(repo)
                loaded, SAMPLE_RATE = repo, model.sample_rate
                ready = True
                print(f"model ready: {repo}", flush=True)
            except Exception:
                traceback.print_exc()
                start([])  # drop what was waiting, so nothing claims to be loading
                fail("The voice did not load. The first run needs the internet. See ~/Library/Logs/localtts.log.")
                job = jobs.get()  # the next read tries again
                continue
        gid, parts, at = job or jobs.get()
        job = None
        if MODELS[cfg["voice"].split("/")[0]]["repo"] != loaded:  # a voice of the other model was picked meanwhile
            job = gid, parts, at
            continue
        lang = None
        pace = 15  # chars spoken per second of audio, from the last passage that finished
        try:
            for i in range(at, len(parts)):
                if gid != gen:
                    break
                # Auto: English or Spanish by the passage's own words; one with no clue (a title) keeps the last.
                lang = detect(parts[i]) or lang
                text, attempt = parts[i], 0
                while text and gid == gen:
                    if cfg["seed"]:  # before every passage, so each one can be repeated exactly
                        voice.seed(int(cfg["seed"]) + attempt)
                    seconds = voiced = took = 0
                    quiet = []  # silent seconds held back until speech follows them
                    for r in model.generate(
                        text=text,
                        voice=cfg["voice"].split("/")[1],
                        instruct=cfg["style"] or None,
                        temperature=cfg["temperature"],
                        lang_code=cfg["language"] if cfg["language"] != "auto" else lang or "auto",
                        max_tokens=min(4096, int(12.5 * (10 + len(text) / 4))),  # 12.5 a second; no passage is this slow
                        stream=True,
                        streaming_interval=1.0,
                    ):
                        if gid != gen:
                            break
                        samples = np.asarray(r.audio, dtype=np.float32)
                        seconds += r.samples / SAMPLE_RATE
                        took += r.processing_time_seconds
                        if np.sqrt((samples**2).mean()) < 0.01:
                            quiet.append(samples)
                            if len(quiet) == 3:  # three silent seconds: see below
                                break
                            continue
                        for held in quiet + [samples]:  # a pause the model meant
                            audio.put((gid, i, held))
                        quiet, voiced = [], seconds
                    if gid != gen:
                        break
                    speed = seconds / (took or 1)  # below 1× real time, playback outruns generation and stutters
                    if len(quiet) < 3 or attempt == 2:
                        print(f"passage {i + 1}: {len(text)} chars, {seconds:.1f} s of audio, {speed:.1f}× real time", flush=True)
                        pace = len(text) / voiced if voiced and not attempt else pace
                        break
                    # The model sometimes stops speaking part-way (seen at temperature 0.3) and makes silence
                    # until its limit, minutes later. It stops at the end of a sentence: find which one from the
                    # time it spoke, and carry on from the next, with another seed so it does not stall again.
                    # The silence itself was held back, so the listener hears no gap.
                    stops = [m.end() for m in re.finditer(rf".*?{STOP}(?=\s)|.+", text, re.S)]
                    done = min([0] + stops, key=lambda stop: abs(stop - voiced * pace))
                    print(f"passage {i + 1}: went silent after {voiced:.0f} s; carrying on from character {done}", flush=True)
                    text, attempt = text[done:].strip(), attempt + 1
                # After the last passage too: at speeds other than 1 the time-stretcher holds back the final
                # few milliseconds, which are then this silence and not the end of the last word.
                audio.put((gid, i, np.zeros(int(PAUSE_SECONDS * SAMPLE_RATE), dtype=np.float32)))
        except Exception as e:
            traceback.print_exc()
            fail(f"Reading stopped: {e}")
        audio.put((gid, None, None))  # end of read


def player():
    global current
    import sounddevice as sd
    from audiotsm import wsola
    from audiotsm.io.array import ArrayReader, ArrayWriter

    out, tsm, tsm_gid, gaps = None, wsola(1), 0, 0
    while True:
        try:
            gid, i, samples = audio.get(timeout=1)
        except queue.Empty:
            if out:  # idle: release the device
                out.close()
                out = None
                if gaps > 1:  # one is normal: the wait for the first audio. More is stutter the listener heard.
                    print(f"playback ran dry {gaps} times", flush=True)
                gaps = 0
            continue
        if gid != gen:
            continue
        if samples is None:
            with gen_lock:
                if gid == gen:
                    start([])
                    current = 0  # finished: reading it again starts from the top
                    remember(0)
            continue
        current = i
        remember(i)
        if cfg["speed"] != 1.0:  # the model has no speed control, so time-stretch without changing pitch
            if tsm_gid != gid:
                tsm.clear()
                tsm_gid = gid
            tsm.set_speed(cfg["speed"])
            reader, writer = ArrayReader(samples[None, :]), ArrayWriter(1)
            while not reader.empty:
                tsm.read_from(reader)
                tsm.write_to(writer)
            samples = writer.data[0]
        for j in range(0, len(samples), SAMPLE_RATE // 10):  # 0.1 s slices so stop and pause are prompt
            if not unpaused.is_set():
                if out:
                    out.close()
                    out = None
                unpaused.wait()
            if gid != gen:
                break
            if out is None:
                # Do not restart PortAudio here to pick up newly connected headphones: each restart halves the
                # device's buffer, and after three every write underruns (measured), which sounds robotic.
                out = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
                out.start()
            gaps += bool(out.write(samples[j : j + SAMPLE_RATE // 10]))  # True when the device ran out of audio
