"""LocalTTS: reads documents, web pages and selected text aloud with a local model (Qwen3-TTS or Voxtral TTS).

The app has a reader window (the text, with the passage being spoken highlighted; click anywhere in it to read
from there), a settings window and a menu bar item. It opens PDF, text, Markdown, HTML, Word and RTF files.

Other programs drive it over HTTP: POST text to /speak, a URL to /page, page HTML to /html, PDF bytes to /pdf
(add ?ui=browser when the caller shows its own read-along). POST /pause toggles, /stop stops, /skip with body
1 or -1 moves a passage. GET /state reports what is being read, and the error if the last read failed.

This file starts the macOS app. The platform-free parts are in core/, the macOS ones in mac/.
"""

import sys
import threading
from http.server import ThreadingHTTPServer

from core import engine, extract
from core.engine import fail, player, seek, skip, start, status, submit
from core.extract import page_text
from core.server import PORT, Handler
from core.text import detect, pieces, utf16_ranges
from mac import app, pdf

engine.notify = app.notify
extract.pdf_text = pdf.pdf_text
extract.rich_text = app.rich_text


def selftest():
    e = engine
    chunks = lambda text, **options: [p[2] for p in pieces(text, **options)]
    assert chunks("") == []
    assert chunks("One. Two!  Three?\n\nFour") == ["One. Two! Three?", "Four"]
    assert chunks('"Go." “No!” (Why?) he said: yes; no.') == ['"Go." “No!” (Why?) he said: yes; no.'], "one paragraph"
    wrapped = "The cat sat\non the mat. It\nslept.\nNext one\nhere."
    assert chunks(wrapped) == ["The cat sat on the mat. It slept.", "Next one here."]
    assert chunks("- one thing\n• another") == ["one thing another"]
    assert chunks("Aaaa. Bbbb. Cccc. Dddd.", limit=12) == ["Aaaa. Bbbb.", "Cccc. Dddd."], "long ones are cut evenly"
    assert [len(c) for c in chunks(" ".join(["Nine chars."] * 500))] == [2003, 1991, 2003], "no short tail"
    long = "x" * 400
    assert chunks(f"Hi. {long}. Bye.", limit=250) == [f"Hi. {long}.", "Bye."]
    text = "Chapter 3\n  The storm began. It rained.\n- a\n- b"
    found = pieces(text, blocks=True)
    assert [p[2] for p in found] == ["Chapter 3", "The storm began. It rained.", "a", "b"]
    assert [text[a:b] for a, b, _ in found] == ["Chapter 3", "The storm began. It rained.", "- a", "- b"]
    assert utf16_ranges("ab c😀 d", [(0, 2), (3, 5), (6, 7)]) == [(0, 2), (3, 3), (7, 1)]
    assert detect("The cat is on the table and it was happy.") == "english"
    assert detect("El gato está en la mesa y es muy feliz.") == "spanish"
    assert detect("Capítulo 3") is None
    assert "Two" in page_text(html="<html><body><div><span>One</span><button>Two</button></div></body></html>")[0]
    assert page_text(html="") == ("I could not get the text of this page.", [])

    first = submit("First one.\nSecond one.\nThird one.", panel=False)
    assert (status(), e.current, len(e.passages)) == ("loading", 0, 3)
    skip(1), skip(1), skip(1)
    assert e.current == 2, "skip stops at the last passage"
    skip(-1)
    assert e.current == 1 and e.read_id == first, "skipping keeps the same read"
    assert e.jobs.queue[-1] == (e.gen, e.passages, 1)
    start([])
    assert (status(), e.current, len(e.passages)) == ("idle", 1, 3), "stopping keeps the text and the place"
    skip(1)
    assert status() == "idle", "skip does nothing when idle"
    seek(e.doc["starts"][2] + 5)
    assert (status(), e.current) == ("loading", 2), "a click reads from the passage clicked"
    assert submit(" \n", panel=False) == first + 1
    assert (status(), e.error, len(e.passages)) == ("idle", "There is no text to read.", 3)
    submit("Uno. Dos.", page_starts=[0, 5], panel=False)
    assert e.error == "" and e.doc["pages"] == [0], "a new read clears the error"
    start([])
    print("ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        selftest()
    else:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        except OSError:
            fail(f"LocalTTS is already running, or another program is using port {PORT}.")
            sys.exit(1)
        threading.Thread(target=engine.generator, args=("mac.voice",), daemon=True).start()
        threading.Thread(target=player, daemon=True).start()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        app.run_app(show_window="--background" not in sys.argv)  # AppKit needs the main thread
