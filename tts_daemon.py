"""LocalTTS: reads text aloud with a local Qwen3-TTS model. Menu bar item, settings window, HTTP control.

POST text to /speak, a URL to /page, page HTML to /html, PDF bytes to /pdf (add ?ui=browser when the caller shows
its own read-along). POST /pause toggles, /stop stops, /skip with body 1 or -1 moves a passage.
GET /state reports what is being read.
"""

import json
import queue
import re
import sys
import threading
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
VOICES = {  # model speaker id -> label shown to the user
    "ryan": "Ryan — English, male",
    "aiden": "Aiden — English, male",
    "ono_anna": "Anna — Japanese, female",
    "sohee": "Sohee — Korean, female",
    "serena": "Serena — Chinese, female",
    "vivian": "Vivian — Chinese, female",
    "uncle_fu": "Uncle Fu — Chinese, male",
    "dylan": "Dylan — Beijing Chinese, male",
    "eric": "Eric — Sichuan Chinese, male",
}
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
SPEEDS = [0.8, 1.0, 1.25, 1.5, 2.0]
SAMPLE_TEXT = "Hi! This is how I sound with the current settings. Select some text anywhere and I will read it to you."
PAUSE_SECONDS = 0.45  # silence added between passages; the model leaves almost none itself
PORT = 8765
SAMPLE_RATE = 24000
CONFIG = Path.home() / "Library/Application Support/LocalTTS/config.json"

cfg = {"voice": "ryan", "language": "auto", "style": STYLES[0], "speed": 1.0, "show_text": True}
if CONFIG.exists():
    cfg.update(json.loads(CONFIG.read_text()))

gen = 0  # bumped on every start, skip and stop; work tagged with an older value is dropped
read_id = 0  # bumped only when new text is submitted, so callers can tell their read from a later one
gen_lock = threading.RLock()
jobs = queue.Queue()
audio = queue.Queue(maxsize=30)  # ~30 s of audio; stops generation running far ahead of playback
unpaused = threading.Event()
unpaused.set()
ready = False  # model loaded
passages = []  # text of the read in progress, empty when idle
current = -1  # index into passages of the one being played
own_panel = True  # False when the caller (the browser extension) shows the read-along itself


def chunks(text, limit=250):
    """Group sentences into pieces of about `limit` chars so each generate call stays short."""
    # ponytail: a single sentence longer than limit is passed whole; split on commas if one ever gets cut off
    out, cur = [], ""
    for part in re.split(r"(?<=[.!?。！？;:])\s+|\n+", text):
        part = " ".join(re.sub(r"^\s*[-•*]\s+", "", part).split())  # drop list bullets, tidy spaces
        if not part:
            continue
        if cur and len(cur) + len(part) + 1 > limit:
            out.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        out.append(cur)
    return out


def utf16_ranges(parts, sep="\n\n"):
    """(start, length) of each part inside sep.join(parts), in UTF-16 units as AppKit counts them."""
    n = lambda s: len(s.encode("utf-16-le")) // 2
    out, pos = [], 0
    for p in parts:
        out.append((pos, n(p)))
        pos += n(p) + n(sep)
    return out


def start(parts, at=0):
    """Read parts from index `at`, replacing whatever is being read. Empty parts means stop."""
    global gen, passages, current
    with gen_lock:
        gen += 1
        passages, current = parts, at if parts else -1
        unpaused.set()
        if parts:
            jobs.put((gen, parts, at))


def submit(text, panel=True):
    global read_id, own_panel
    with gen_lock:
        read_id += 1
        own_panel = panel
        start(chunks(text))
        return read_id


def skip(by):
    with gen_lock:
        if passages:
            start(passages, max(0, min(len(passages) - 1, current + by)))


def toggle_pause():
    if unpaused.is_set():
        unpaused.clear()
    else:
        unpaused.set()


def status():
    if not passages:
        return "idle"
    return "loading" if not ready else "reading" if unpaused.is_set() else "paused"


def set_cfg(key, value):
    cfg[key] = value
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg))


def pdf_text(data):
    import Quartz

    doc = Quartz.PDFDocument.alloc().initWithData_(Quartz.NSData.dataWithBytes_length_(data, len(data)))
    text = (doc and doc.string()) or ""
    text = re.sub(r"-\n(?=[a-z])", "", text)  # rejoin words hyphenated across lines
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text)  # PDF lines end mid-sentence; only blank lines are breaks


def page_text(url=None, html=None, pdf=None):
    """Readable text of a web page or PDF, given its address, its HTML, or the PDF's bytes."""
    import trafilatura

    if url is not None:
        if not re.match(r"(https?|file)://", url):
            return "That is not a web page."
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) LocalTTS"})
            html = urllib.request.urlopen(request, timeout=20).read(50_000_000)
        except Exception:
            html = None
        if html and html.lstrip()[:5] == b"%PDF-":
            pdf, html = html, None
    if pdf is not None:
        return pdf_text(pdf) or "I could not find any text in this PDF. It may be a scan."
    # Article extraction first; pages that are not articles (apps, feeds, short pages) fall back to all visible text.
    text = html and (trafilatura.extract(html) or trafilatura.extract(html, favor_recall=True) or trafilatura.html2txt(html))
    text = text or "I could not get the text of this page."
    return re.sub(r"\[(\d+|edit|citation needed)\]", "", text)  # footnote and edit markers are noise read aloud


def generator():
    # MLX wants the model used on the thread that loaded it
    global ready
    import numpy as np
    from mlx_audio.tts.utils import load_model

    model = load_model(MODEL)
    ready = True
    print("model ready", flush=True)
    while True:
        gid, parts, at = jobs.get()
        try:
            for i in range(at, len(parts)):
                for r in model.generate(
                    text=parts[i],
                    voice=cfg["voice"],
                    instruct=cfg["style"] or None,
                    lang_code=cfg["language"],
                    stream=True,
                    streaming_interval=1.0,
                ):
                    if gid != gen:
                        break
                    audio.put((gid, i, np.asarray(r.audio, dtype=np.float32)))
                if gid != gen:
                    break
                if i < len(parts) - 1:
                    audio.put((gid, i, np.zeros(int(PAUSE_SECONDS * SAMPLE_RATE), dtype=np.float32)))
        except Exception:
            traceback.print_exc()
        audio.put((gid, None, None))  # end of read


def player():
    global current
    import sounddevice as sd
    from audiotsm import wsola
    from audiotsm.io.array import ArrayReader, ArrayWriter

    out, tsm, tsm_gid = None, wsola(1), 0
    while True:
        try:
            gid, i, samples = audio.get(timeout=1)
        except queue.Empty:
            if out:  # idle: release the device so a newly plugged-in output is picked up next time
                out.close()
                out = None
            continue
        if gid != gen:
            continue
        if samples is None:
            with gen_lock:
                if gid == gen:
                    start([])
            continue
        current = i
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
                out = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32")
                out.start()
            out.write(samples[j : j + SAMPLE_RATE // 10])


class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        # Browsers send Origin on cross-site requests; curl does not. Stops web pages driving the daemon.
        # Our own browser extension is let through.
        origin = self.headers.get("Origin")
        if origin and not origin.startswith("chrome-extension://"):
            self.send_error(403)
            return False
        return True

    def reply(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlsplit(self.path)
        if not self.allowed():
            return
        if url.path != "/state":
            return self.send_error(404)
        state = {"read": read_id, "status": status(), "index": current, "total": len(passages)}
        if parse_qs(url.query).get("have") != [str(read_id)]:  # the text itself only when the caller lacks it
            state["passages"] = passages
        self.reply(state)

    def do_POST(self):
        url = urlsplit(self.path)
        if not self.allowed():
            return
        length = min(int(self.headers.get("Content-Length") or 0), 20_000_000)
        raw = self.rfile.read(length)
        body = raw.decode("utf-8", "replace").strip()
        panel = parse_qs(url.query).get("ui") != ["browser"]
        if url.path == "/stop":
            start([])
        elif url.path == "/pause":
            toggle_pause()
        elif url.path == "/skip":
            skip(-1 if body.startswith("-") else 1)
        elif url.path == "/speak":
            submit(body, panel)
        elif url.path == "/page":
            submit(page_text(url=body), panel)
        elif url.path == "/html":
            submit(page_text(html=body), panel)
        elif url.path == "/pdf":
            submit(page_text(pdf=raw), panel)
        else:
            return self.send_error(404)
        self.reply({"read": read_id})

    def log_message(self, *args):
        pass


def run_app(show_window):
    import AppKit
    import objc
    import rumps

    def text_panel():
        mask = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskUtilityWindow
            | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 440, 280), mask, AppKit.NSBackingStoreBuffered, False
        )
        panel.setTitle_("Reading")
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        panel.setHidesOnDeactivate_(False)
        panel.setReleasedWhenClosed_(False)
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(panel.contentView().bounds())
        scroll.setHasVerticalScroller_(True)
        view = AppKit.NSTextView.alloc().initWithFrame_(scroll.bounds())
        view.setEditable_(False)
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        view.setTextContainerInset_((12, 12))
        scroll.setDocumentView_(view)
        panel.setContentView_(scroll)
        panel.center()
        panel.setFrameAutosaveName_("localtts")  # remembers where the user put it
        return panel, view

    def by_label(table, label):
        return next(key for key, value in table.items() if value == label)

    class Controls(AppKit.NSObject):
        def voice_(self, sender):
            set_cfg("voice", by_label(VOICES, sender.titleOfSelectedItem()))

        def language_(self, sender):
            set_cfg("language", by_label(LANGUAGES, sender.titleOfSelectedItem()))

        def style_(self, sender):
            set_cfg("style", sender.stringValue().strip())

        def speed_(self, sender):
            set_cfg("speed", float(sender.titleOfSelectedItem()[:-1]))

        def show_(self, sender):
            set_cfg("show_text", bool(sender.state()))

        def test_(self, sender):
            submit(SAMPLE_TEXT)

        def back_(self, sender):
            skip(-1)

        def pause_(self, sender):
            toggle_pause()

        def forward_(self, sender):
            skip(1)

        def stop_(self, sender):
            start([])

    def settings_window(target):
        def popup(titles, action):
            button = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSMakeRect(0, 0, 220, 26), False)
            button.addItemsWithTitles_(list(titles))
            button.setTarget_(target)
            button.setAction_(action)
            return button

        def button(title, action):
            return AppKit.NSButton.buttonWithTitle_target_action_(title, target, action)

        def small(text):
            field = AppKit.NSTextField.wrappingLabelWithString_(text)
            field.setPreferredMaxLayoutWidth_(330)
            field.setTextColor_(AppKit.NSColor.secondaryLabelColor())
            field.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
            return field

        def heading(text):
            field = AppKit.NSTextField.labelWithString_(text)
            field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
            return field

        def row(text, control):
            label = AppKit.NSTextField.labelWithString_(text)
            label.setAlignment_(AppKit.NSTextAlignmentRight)
            label.widthAnchor().constraintEqualToConstant_(70).setActive_(True)
            return AppKit.NSStackView.stackViewWithViews_([label, control])

        style = AppKit.NSComboBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 250, 26))
        style.addItemsWithObjectValues_(STYLES)
        style.setPlaceholderString_("Neutral — pick one or type your own")
        style.setStringValue_(cfg["style"])  # set once; syncing it on the timer would fight typing
        style.setTarget_(target)
        style.setAction_("style:")
        style.widthAnchor().constraintEqualToConstant_(250).setActive_(True)
        status = AppKit.NSTextField.labelWithString_("")
        status.setFont_(AppKit.NSFont.boldSystemFontOfSize_(15))
        w = {
            "status": status,
            "voice": popup(VOICES.values(), "voice:"),
            "language": popup(LANGUAGES.values(), "language:"),
            "speed": popup([f"{s}×" for s in SPEEDS], "speed:"),
            "show": AppKit.NSButton.checkboxWithTitle_target_action_(
                "Show the text in a floating window while reading", target, "show:"
            ),
            "back": button("⏮", "back:"),
            "pause": button("Pause", "pause:"),
            "forward": button("⏭", "forward:"),
            "stop": button("Stop", "stop:"),
        }
        w["back"].setToolTip_("Back one passage")
        w["forward"].setToolTip_("Forward one passage")
        stack = AppKit.NSStackView.stackViewWithViews_(
            [
                w["status"],
                AppKit.NSStackView.stackViewWithViews_([w["back"], w["pause"], w["forward"], w["stop"]]),
                heading("Voice"),
                row("Voice", w["voice"]),
                row("Language", w["language"]),
                row("Speed", w["speed"]),
                row("Style", style),
                AppKit.NSStackView.stackViewWithViews_([button("Play a sample", "test:")]),
                w["show"],
                heading("How to use it"),
                small(
                    "In any app: select text, right-click, Services → Read Aloud.\n"
                    "In Chrome: right-click a page or a selection → Read aloud. Controls appear on the page.\n"
                    "You can close this window; LocalTTS keeps running. Click it in the Dock to come back."
                ),
            ]
        )
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        stack.setSpacing_(12)
        stack.setEdgeInsets_((20, 20, 20, 20))
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 370, 300),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("LocalTTS")
        win.setReleasedWhenClosed_(False)
        win.setContentView_(stack)
        win.center()
        w["window"] = win
        return w

    class App(rumps.App):
        def __init__(self):
            super().__init__("localtts", title="⏳", quit_button="Quit")
            self.pause_item = rumps.MenuItem("Pause", callback=lambda _: toggle_pause())
            self.stop_item = rumps.MenuItem("Stop", callback=lambda _: start([]))
            self.voice_menu = rumps.MenuItem("Voice")
            for label in VOICES.values():
                self.voice_menu.add(rumps.MenuItem(label, callback=lambda item: set_cfg("voice", by_label(VOICES, item.title))))
            self.language_menu = rumps.MenuItem("Language")
            for label in LANGUAGES.values():
                self.language_menu.add(
                    rumps.MenuItem(label, callback=lambda item: set_cfg("language", by_label(LANGUAGES, item.title)))
                )
            self.speed_menu = rumps.MenuItem("Speed")
            for s in SPEEDS:
                self.speed_menu.add(rumps.MenuItem(f"{s}×", callback=lambda item: set_cfg("speed", float(item.title[:-1]))))
            self.controls = Controls.alloc().init()
            self.win = settings_window(self.controls)
            settings = rumps.MenuItem("Open LocalTTS…", callback=lambda _: self.show_settings())
            self.menu = [self.pause_item, self.stop_item, None, self.voice_menu, self.language_menu, self.speed_menu, settings, None]
            self.panel, self.view = text_panel()
            self.shown = (0, -1)  # (read, passage) the panel currently displays

        def show_settings(self):
            self.win["window"].makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

        @rumps.timer(0.25)
        def tick(self, _):
            parts, idx, rid, state = passages, current, read_id, status()
            reading = state != "idle"
            self.title = "⏳" if not ready else {"idle": "🔈", "reading": "🔊", "paused": "⏸"}[state]
            self.pause_item.title = "Resume" if state == "paused" else "Pause"
            for item in self.voice_menu.values():
                item.state = item.title == VOICES[cfg["voice"]]
            for item in self.language_menu.values():
                item.state = item.title == LANGUAGES[cfg["language"]]
            for item in self.speed_menu.values():
                item.state = item.title == f"{cfg['speed']}×"

            w = self.win
            position = f" — passage {idx + 1} of {len(parts)}" if reading and len(parts) > 1 else ""
            w["status"].setStringValue_(
                "Getting the voice ready…"
                if not ready
                else {"idle": "Ready to read", "reading": "Reading", "paused": "Paused"}[state] + position
            )
            w["voice"].selectItemWithTitle_(VOICES[cfg["voice"]])
            w["language"].selectItemWithTitle_(LANGUAGES[cfg["language"]])
            w["speed"].selectItemWithTitle_(f"{cfg['speed']}×")
            w["show"].setState_(cfg["show_text"])
            w["pause"].setTitle_(self.pause_item.title)
            for name in ("back", "pause", "forward", "stop"):
                w[name].setEnabled_(reading)

            if not reading or not cfg["show_text"] or not own_panel:
                if self.shown[0]:
                    self.panel.orderOut_(None)
                    self.shown = (0, -1)
                return
            if self.shown[0] != rid:
                self.view.setString_("\n\n".join(parts))
                self.view.setFont_(AppKit.NSFont.systemFontOfSize_(15))
                self.view.setTextColor_(AppKit.NSColor.labelColor())
                self.panel.orderFrontRegardless()
            if self.shown != (rid, idx) and 0 <= idx < len(parts):
                # ponytail: highlights the passage being spoken, not the word; the model gives no word timings
                store = self.view.textStorage()
                key = AppKit.NSBackgroundColorAttributeName
                store.removeAttribute_range_(key, (0, store.length()))
                rng = utf16_ranges(parts)[idx]
                store.addAttribute_value_range_(key, AppKit.NSColor.systemYellowColor().colorWithAlphaComponent_(0.4), rng)
                self.view.scrollRangeToVisible_(rng)
            self.shown = (rid, idx)

    nsapp = AppKit.NSApplication.sharedApplication()
    nsapp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)  # Dock icon and Cmd-Tab
    icon = Path(__file__).with_name("icon.icns")
    if icon.exists():
        nsapp.setApplicationIconImage_(AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon)))
    app_menu = AppKit.NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("Close Window", "performClose:", "w")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit LocalTTS", "terminate:", "q")
    main_menu = AppKit.NSMenu.alloc().init()
    main_menu.addItemWithTitle_action_keyEquivalent_("LocalTTS", None, "").setSubmenu_(app_menu)
    nsapp.setMainMenu_(main_menu)
    app = App()

    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, visible):
        app.show_settings()
        return False

    # rumps owns the app delegate, so add the reopen handler to its class
    objc.classAddMethods(
        rumps.rumps.NSApp, [objc.selector(applicationShouldHandleReopen_hasVisibleWindows_, signature=b"Z@:@Z")]
    )
    if show_window:
        app.show_settings()
    app.run()


def selftest():
    assert chunks("") == []
    assert chunks("One. Two!  Three?\n\nFour") == ["One. Two! Three? Four"]
    assert chunks("Aaaa. Bbbb. Cccc.", limit=10) == ["Aaaa.", "Bbbb.", "Cccc."]
    assert chunks("Aaaa. Bbbb. Cccc.", limit=11) == ["Aaaa. Bbbb.", "Cccc."]
    long = "x" * 400
    assert chunks(f"Hi. {long}. Bye.") == ["Hi.", long + ".", "Bye."]
    assert chunks("- one thing\n• another") == ["one thing another"]
    assert "Two" in page_text(html="<html><body><div><span>One</span><button>Two</button></div></body></html>")
    assert page_text(html="") == "I could not get the text of this page."
    assert utf16_ranges(["ab", "c😀", "d"]) == [(0, 2), (4, 3), (9, 1)]

    first = submit("x" * 200 + ". " + "y" * 200 + ". " + "z" * 200 + ".")
    assert (status(), current, len(passages)) == ("loading", 0, 3)
    skip(1), skip(1), skip(1)
    assert current == 2, "skip stops at the last passage"
    skip(-1)
    assert current == 1 and read_id == first, "skipping keeps the same read"
    assert jobs.queue[-1] == (gen, passages, 1)
    start([])
    assert (status(), current) == ("idle", -1)
    skip(1)
    assert status() == "idle", "skip does nothing when idle"
    assert submit("again") == first + 1
    start([])
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        selftest()
    else:
        threading.Thread(target=generator, daemon=True).start()
        threading.Thread(target=player, daemon=True).start()
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        run_app(show_window="--background" not in sys.argv)  # AppKit needs the main thread
