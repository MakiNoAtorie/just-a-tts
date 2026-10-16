"""The macOS app: menu bar item, reader and settings windows, Dock and Open With."""

import json
import os
import subprocess
import sys
from bisect import bisect_left
from pathlib import Path

from core import engine
from core.engine import (
    HINT, LANGUAGES, SAMPLE_TEXT, SPEEDS, STYLES, TEMPERATURES, VOICES, cfg, goto, open_file, seek, set_cfg, skip,
    start, status, submit, toggle_pause,
)

def notify(message):
    script = f'display notification {json.dumps(message, ensure_ascii=False)} with title "LocalTTS"'
    subprocess.Popen(["osascript", "-e", script])


def rich_text(path):
    """The text of a file AppKit can read: Word, RTF."""
    import AppKit

    url = AppKit.NSURL.fileURLWithPath_(str(path))
    rich = AppKit.NSAttributedString.alloc().initWithURL_options_documentAttributes_error_(url, {}, None, None)[0]
    if rich is None:
        raise ValueError("LocalTTS cannot read this kind of file")
    return rich.string()


def run_app(show_window):
    import AppKit
    import objc
    import rumps

    def by_label(table, label):
        return next(key for key, value in table.items() if value == label)

    def button(title, target, action, tip=None):
        made = AppKit.NSButton.buttonWithTitle_target_action_(title, target, action)
        made.setToolTip_(tip)
        return made

    class ReadingView(AppKit.NSTextView):
        def acceptsFirstMouse_(self, event):  # a click reads from there even when the window is behind another
            return True

        def mouseDown_(self, event):
            objc.super(ReadingView, self).mouseDown_(event)  # returns when the button is released
            spot = self.selectedRange()
            if spot.length == 0:  # a click; dragging still selects text to copy
                seek(spot.location)

    class Controls(AppKit.NSObject):
        def voice_(self, sender):
            set_cfg("voice", by_label(VOICES, sender.titleOfSelectedItem()))

        def language_(self, sender):
            set_cfg("language", by_label(LANGUAGES, sender.titleOfSelectedItem()))

        def style_(self, sender):
            set_cfg("style", sender.stringValue().strip())

        def speed_(self, sender):
            set_cfg("speed", float(sender.titleOfSelectedItem()[:-1]))

        def temperature_(self, sender):
            set_cfg("temperature", float(sender.titleOfSelectedItem()))

        def seed_(self, sender):
            text = sender.stringValue().strip()
            set_cfg("seed", text if text.isascii() and text.isdigit() and len(text) < 10 else "")
            sender.setStringValue_(cfg["seed"])  # anything that is not a number is cleared: random again

        def show_(self, sender):
            set_cfg("show_text", bool(sender.state()))

        def test_(self, sender):
            submit(SAMPLE_TEXT)

        def open_(self, sender):
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            panel = AppKit.NSOpenPanel.openPanel()
            if panel.runModal() == AppKit.NSModalResponseOK:
                app.show_reader()
                open_file(panel.URL().path())

        def clipboard_(self, sender):
            submit(AppKit.NSPasteboard.generalPasteboard().stringForType_(AppKit.NSPasteboardTypeString) or "")

        def page_(self, sender):
            goto(bisect_left(engine.doc["pages"], sender.intValue() - 1))

        def back_(self, sender):
            skip(-1)

        def pause_(self, sender):
            toggle_pause() if engine.active else goto(engine.current)

        def forward_(self, sender):
            skip(1)

        def stop_(self, sender):
            start([])

        def settings_(self, sender):
            app.settings["window"].makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

    def reader_window(target):
        status = AppKit.NSTextField.labelWithString_("")
        status.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        status.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        # never wider than the window, however long the message
        status.setContentCompressionResistancePriority_forOrientation_(1, AppKit.NSLayoutConstraintOrientationHorizontal)
        page = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 44, 22))
        page.setToolTip_("Type a page number and press Return to read from that page")
        page.setTarget_(target)
        page.setAction_("page:")
        page.widthAnchor().constraintEqualToConstant_(44).setActive_(True)
        of = AppKit.NSTextField.labelWithString_("")
        w = {
            "status": status,
            "of": of,
            "pages": AppKit.NSStackView.stackViewWithViews_([AppKit.NSTextField.labelWithString_("Page"), page, of]),
            "back": button("⏮", target, "back:", "Back one passage"),
            "pause": button("Read", target, "pause:"),
            "forward": button("⏭", target, "forward:", "Forward one passage"),
            "stop": button("Stop", target, "stop:"),
        }
        bar = AppKit.NSStackView.stackViewWithViews_(
            [
                button("Open…", target, "open:", "Read a PDF, text, Markdown, web page, Word or RTF file"),
                button("Read Clipboard", target, "clipboard:"),
                w["back"],
                w["pause"],
                w["forward"],
                w["stop"],
            ]
        )
        bar.addView_inGravity_(w["pages"], AppKit.NSStackViewGravityTrailing)
        bar.setEdgeInsets_((8, 12, 4, 12))
        scroll = ReadingView.scrollableTextView()
        view = scroll.documentView()
        view.setEditable_(False)
        view.setTextContainerInset_((18, 14))
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 700, 620),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        content = win.contentView()
        for part, inset in ((bar, 0), (status, 14), (scroll, 0)):  # stacked, each as wide as the window
            part.setTranslatesAutoresizingMaskIntoConstraints_(False)
            content.addSubview_(part)
            part.leadingAnchor().constraintEqualToAnchor_constant_(content.leadingAnchor(), inset).setActive_(True)
            part.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), -inset).setActive_(True)
        bar.topAnchor().constraintEqualToAnchor_(content.topAnchor()).setActive_(True)
        status.topAnchor().constraintEqualToAnchor_(bar.bottomAnchor()).setActive_(True)
        scroll.topAnchor().constraintEqualToAnchor_constant_(status.bottomAnchor(), 8).setActive_(True)
        scroll.bottomAnchor().constraintEqualToAnchor_(content.bottomAnchor()).setActive_(True)
        win.setContentMinSize_((560, 300))
        win.setReleasedWhenClosed_(False)
        win.center()
        win.setFrameAutosaveName_("reader")  # remembers where the user put it
        w["window"], w["view"] = win, view
        return w

    def settings_window(target):
        def popup(titles, action):
            made = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(AppKit.NSMakeRect(0, 0, 220, 26), False)
            made.addItemsWithTitles_(list(titles))
            made.setTarget_(target)
            made.setAction_(action)
            return made

        def row(text, control):
            label = AppKit.NSTextField.labelWithString_(text)
            label.setAlignment_(AppKit.NSTextAlignmentRight)
            label.widthAnchor().constraintEqualToConstant_(90).setActive_(True)
            return AppKit.NSStackView.stackViewWithViews_([label, control])

        seed = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 100, 22))
        seed.setPlaceholderString_("Random")
        seed.setStringValue_(cfg["seed"])  # set once, like the style
        seed.setTarget_(target)
        seed.setAction_("seed:")
        seed.widthAnchor().constraintEqualToConstant_(100).setActive_(True)
        style = AppKit.NSComboBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 250, 26))
        style.addItemsWithObjectValues_(STYLES)
        style.setPlaceholderString_("Neutral — pick one or type your own")
        style.setStringValue_(cfg["style"])  # set once; syncing it on the timer would fight typing
        style.setTarget_(target)
        style.setAction_("style:")
        style.cell().setSendsActionOnEndEditing_(True)  # also saved when you click elsewhere, not only on Return
        style.widthAnchor().constraintEqualToConstant_(225).setActive_(True)
        w = {
            "voice": popup(VOICES.values(), "voice:"),
            "language": popup(LANGUAGES.values(), "language:"),
            "speed": popup([f"{s}×" for s in SPEEDS], "speed:"),
            "temperature": popup(map(str, TEMPERATURES), "temperature:"),
            "show": AppKit.NSButton.checkboxWithTitle_target_action_(
                "Bring the LocalTTS window forward when a read starts", target, "show:"
            ),
        }
        note = AppKit.NSTextField.wrappingLabelWithString_(
            "Auto-detect tells English from Spanish paragraph by paragraph.\n"
            "Voxtral voices speak their own language and ignore Style. A voice of the other model loads it (a minute the first time).\n"
            "Temperature: lower is steadier, higher is livelier; 0.9 is the model's own default.\n"
            "Seed: a number makes the same paragraph sound the same every time; empty is a new take each time.\n"
            "Changes apply at once: the paragraph being read starts again."
        )
        note.setPreferredMaxLayoutWidth_(330)
        note.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        note.setFont_(AppKit.NSFont.systemFontOfSize_(AppKit.NSFont.smallSystemFontSize()))
        stack = AppKit.NSStackView.stackViewWithViews_(
            [
                row("Voice", w["voice"]),
                row("Language", w["language"]),
                row("Speed", w["speed"]),
                row("Style", style),
                row("Temperature", w["temperature"]),
                row("Seed", seed),
                AppKit.NSStackView.stackViewWithViews_([button("Play a sample", target, "test:")]),
                w["show"],
                note,
            ]
        )
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        stack.setSpacing_(12)
        stack.setEdgeInsets_((20, 20, 20, 20))
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 370, 300),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("LocalTTS Settings")
        win.setReleasedWhenClosed_(False)
        win.setContentView_(stack)
        win.center()
        w["window"] = win
        return w

    class App(rumps.App):
        def __init__(self):
            super().__init__("localtts", title="⏳", quit_button="Quit")
            self.controls = Controls.alloc().init()
            self.pause_item = rumps.MenuItem("Pause", callback=lambda _: self.controls.pause_(None))
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
            self.settings = settings_window(self.controls)
            self.reader = reader_window(self.controls)
            self.menu = [
                self.pause_item,
                self.stop_item,
                None,
                rumps.MenuItem("Open File…", callback=lambda _: self.controls.open_(None)),
                rumps.MenuItem("Read Clipboard", callback=lambda _: self.controls.clipboard_(None)),
                None,
                self.voice_menu,
                self.language_menu,
                self.speed_menu,
                None,
                rumps.MenuItem("Show LocalTTS", callback=lambda _: self.show_reader()),
                rumps.MenuItem("Settings…", callback=lambda _: self.controls.settings_(None)),
                None,
            ]
            self.shown = (-1, -1)  # (read, passage) the reader currently displays

        def show_reader(self):
            self.reader["window"].makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

        @rumps.timer(0.25)
        def tick(self, _):
            e = engine
            parts, idx, rid, state, d = e.passages, e.current, e.read_id, status(), e.doc
            failed = e.error if state == "idle" else ""
            self.title = "⚠️" if failed else "⏳" if not e.ready else {"idle": "🔈", "reading": "🔊", "paused": "⏸"}[state]
            self.pause_item.title = {"idle": "Read", "paused": "Resume"}.get(state, "Pause")
            for item in self.voice_menu.values():
                item.state = item.title == VOICES[cfg["voice"]]
            for item in self.language_menu.values():
                item.state = item.title == LANGUAGES[cfg["language"]]
            for item in self.speed_menu.values():
                item.state = item.title == f"{cfg['speed']}×"
            s = self.settings
            s["voice"].selectItemWithTitle_(VOICES[cfg["voice"]])
            s["language"].selectItemWithTitle_(LANGUAGES[cfg["language"]])
            s["speed"].selectItemWithTitle_(f"{cfg['speed']}×")
            s["temperature"].selectItemWithTitle_(str(cfg["temperature"]))
            s["show"].setState_(cfg["show_text"])

            r = self.reader
            where = f" — passage {idx + 1} of {len(parts)}" if len(parts) > 1 else ""
            if idx < len(d["pages"]):
                where += f", page {d['pages'][idx] + 1} of {d['npages']}"
            text = (
                failed
                or e.busy
                or ("Getting the voice ready…" if not e.ready else state.capitalize().replace("Idle", "Ready") + where)
            )
            r["status"].setStringValue_(text)
            r["status"].setToolTip_(text)
            r["pause"].setTitle_(self.pause_item.title)
            r["pause"].setEnabled_(bool(parts))
            for name in ("back", "forward", "stop"):
                r[name].setEnabled_(state != "idle")
            r["pages"].setHidden_(not d["pages"])
            r["of"].setStringValue_(f"of {d['npages']}")

            view = r["view"]
            store = view.textStorage()
            if self.shown[0] != rid:
                view.setString_(d["text"] or HINT)
                view.setFont_(AppKit.NSFont.systemFontOfSize_(15))
                view.setTextColor_(AppKit.NSColor.labelColor())
                spacing = AppKit.NSMutableParagraphStyle.alloc().init()
                spacing.setParagraphSpacing_(10)
                spacing.setLineSpacing_(3)
                store.addAttribute_value_range_(AppKit.NSParagraphStyleAttributeName, spacing, (0, store.length()))
                r["window"].setTitle_(Path(d["path"]).name if d["path"] else "LocalTTS")
                if state != "idle" and cfg["show_text"] and e.own_panel:
                    r["window"].orderFrontRegardless()
                self.shown = (rid, -1)
            if self.shown != (rid, idx) and idx < len(d["ranges"]):
                # ponytail: highlights the passage being spoken, not the word; the model gives no word timings
                key = AppKit.NSBackgroundColorAttributeName
                store.removeAttribute_range_(key, (0, store.length()))
                store.addAttribute_value_range_(
                    key, AppKit.NSColor.systemYellowColor().colorWithAlphaComponent_(0.4), d["ranges"][idx]
                )
                view.scrollRangeToVisible_(d["ranges"][idx])
                self.shown = (rid, idx)

    nsapp = AppKit.NSApplication.sharedApplication()
    nsapp.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)  # Dock icon and Cmd-Tab
    icon = Path(__file__).parent.parent / "icon.icns"
    if icon.exists():
        nsapp.setApplicationIconImage_(AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon)))
    app = App()
    # Each menu's keys work only if the menu exists: without Edit there is no paste into the Style box and no
    # copy from the reader. Items with no target go to whatever has the keyboard focus.
    own = app.controls
    main_menu = AppKit.NSMenu.alloc().init()
    for title, items in (
        ("LocalTTS", [("Settings…", "settings:", ",", own), ("Close Window", "performClose:", "w", None),
                      ("Quit LocalTTS", "terminate:", "q", None)]),
        ("File", [("Open…", "open:", "o", own), ("Read Clipboard", "clipboard:", "V", own)]),
        ("Edit", [("Undo", "undo:", "z", None), ("Redo", "redo:", "Z", None), ("Cut", "cut:", "x", None),
                  ("Copy", "copy:", "c", None), ("Paste", "paste:", "v", None), ("Select All", "selectAll:", "a", None)]),
        ("Window", [("Minimize", "performMiniaturize:", "m", None)]),
    ):
        menu = AppKit.NSMenu.alloc().initWithTitle_(title)
        for name, action, keys, target in items:
            menu.addItemWithTitle_action_keyEquivalent_(name, action, keys).setTarget_(target)
        main_menu.addItemWithTitle_action_keyEquivalent_(title, None, "").setSubmenu_(menu)
    nsapp.setMainMenu_(main_menu)

    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, visible):
        app.show_reader()
        return False

    def application_openFiles_(self, sender, paths):  # a file dropped on the Dock icon, or Open With
        # AppKit also offers what is on our own command line: this script
        if os.path.isfile(paths[0]) and not os.path.samefile(paths[0], sys.argv[0]):
            app.show_reader()
            open_file(paths[0])

    # rumps owns the app delegate, so add the handlers to its class
    objc.classAddMethods(
        rumps.rumps.NSApp,
        [
            objc.selector(applicationShouldHandleReopen_hasVisibleWindows_, signature=b"Z@:@Z"),
            objc.selector(application_openFiles_, signature=b"v@:@@"),
        ],
    )
    if show_window:
        app.show_reader()
    app.run()
