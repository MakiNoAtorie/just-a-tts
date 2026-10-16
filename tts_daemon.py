"""LocalTTS: reads documents, web pages and selected text aloud with a local model (Qwen3-TTS or Voxtral TTS).

The app has a reader window (the text, with the passage being spoken highlighted; click anywhere in it to read
from there), a settings window and a menu bar item. It opens PDF, text, Markdown, HTML, Word and RTF files.

Other programs drive it over HTTP: POST text to /speak, a URL to /page, page HTML to /html, PDF bytes to /pdf
(add ?ui=browser when the caller shows its own read-along). POST /pause toggles, /stop stops, /skip with body
1 or -1 moves a passage. GET /state reports what is being read, and the error if the last read failed.
"""

import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import unicodedata
import urllib.request
from bisect import bisect_left, bisect_right
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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
PORT = 8765
SAMPLE_RATE = 24000  # replaced by the model's own rate once it has loaded
CONFIG = Path.home() / "Library/Application Support/LocalTTS/config.json"
CACHE = Path.home() / "Library/Caches/LocalTTS"  # the text of PDFs opened before, by content
PDF_RULES = 2  # raise when pdf_text changes what it gives, so cached text is not used

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

STOP = r"[.!?。！？][\"'”’»)\]]*"  # the end of a sentence, with any closing quotes or brackets
END = STOP.replace("？", "？;:")  # the same, or the end of a clause
BULLET = r"\s*(?:[-•*▪◦–#>]|\(?\d{1,3}[.)]|\(?[a-z]\))\s"  # how a list item or heading line starts
NOTE = r"(?:\d{1,3}|[*†‡§])[\s.)]*[^\W\d]"  # how a footnote starts
CAPTION = r"(?mi)^(?:fig(?:ure|ura|\.)|tabl[ea]|cuadro|gráfico)\s+\d+[.:].{0,300}(?:\n|$)"  # a paragraph that is one
LEADERS = r"(?:\.\s?){4,}\s*\w+$"  # the end of a line of a table of contents
# A few of the commonest words of each language: enough to tell English from Spanish, which is all detect() does.
STOPWORDS = {
    "english": set("the of and to is that it for with as was this are by at from have not you but they which".split()),
    "spanish": set("el la los las de del que y en un una por con es se al lo para como más su pero este esta muy".split()),
}


def pieces(text, limit=2000, blocks=False):
    """Split text into passages, one per paragraph: the voice keeps its tone through a call and starts afresh
    on the next, so a cut inside a paragraph is heard. Gives (start, end, words to speak) for each; start and
    end locate the passage in text.

    With blocks, each line is a paragraph, heading or list item of its own (true of documents and extracted
    pages). Otherwise (selections, which may be hard-wrapped) lines run on until one ends a sentence or a blank
    line follows. A paragraph longer than `limit` chars is cut at sentence ends into parts of about equal size:
    the model stops at about 5 minutes of audio (~4500 chars), and 1400 chars in one call is measured to be fine.
    """
    # ponytail: a single sentence longer than limit is passed whole; split on commas if one ever gets cut off
    def say(s):
        """The words to speak for a piece of the text, which itself stays as it is."""
        s = re.sub(r"\s*\(\s*https?://[^()]{0,300}\)", "", s)  # a link's address printed after its words
        s = re.sub(r"https?://(?:www\.)?([^/\s]+)\S*", r"\1", s)  # any other address: the site alone
        s = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", "", s)  # reference marks such as [12] and [3-5]
        return " ".join(re.sub(r"(?m)^\s*[-•*]\s+", "", s).split())  # drop list bullets, tidy spaces

    out = []
    for run in re.finditer(r"(?:[^\n]*\S[^\n]*\n?)+", text):  # the lines up to a blank one
        lines = list(re.finditer(r"[^\n]+", run[0]))
        start = None
        for n, line in enumerate(lines):
            end = run.start() + line.end()
            if start is None:
                start = end - len(line[0].lstrip())
            if not (blocks or n == len(lines) - 1 or re.search(STOP + r"\s*$", line[0])):
                continue  # the paragraph runs on into the next line
            share = (end - start) / -(-len(say(text[start:end])) // limit)  # the size of each part of it
            cut, part = start, 1
            sentences = re.finditer(rf".*?{STOP}(?=\s)|.+", text[start:end], re.S)
            stops = [start + m.end() for m in sentences]
            for stop, after in zip(stops, stops[1:] + [end]):
                # cut here if this sentence end is the nearest one to where the part should end
                if stop == end or (stop + after) / 2 >= start + share * part:
                    words = say(text[cut:stop])
                    if words:
                        out.append((stop - len(text[cut:stop].lstrip()), stop, words))
                    cut, part = stop, part + 1
            start = None
    return out


def utf16_ranges(text, spans):
    """(start, length) of each (start, end, ...) span of text, in UTF-16 units as AppKit counts them."""
    n = lambda s: len(s.encode("utf-16-le")) // 2
    out, at, pos = [], 0, 0
    for start, end, *_ in spans:
        pos += n(text[at:start])
        out.append((pos, n(text[start:end])))
        pos += out[-1][1]
        at = end
    return out


def detect(text):
    """ "english" or "spanish" when the text has more common words of one than of the other, else None."""
    words = re.findall(r"\w+", text.lower())
    en, es = (sum(w in STOPWORDS[lang] for w in words) for lang in ("english", "spanish"))
    return "english" if en > es else "spanish" if es > en else None


def fail(message):
    """Record why a read failed, so every status display can show it, and tell the user now."""
    global error
    error = message
    print(message, flush=True)
    if own_panel:  # the browser extension shows the error on the page instead
        script = f'display notification {json.dumps(message, ensure_ascii=False)} with title "LocalTTS"'
        subprocess.Popen(["osascript", "-e", script])


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


def ocr(page):
    """Text of a scanned PDF page, read by macOS's own text recognition."""
    import Quartz
    import Vision

    box = Quartz.kPDFDisplayBoxMediaBox
    size = page.boundsForBox_(box).size
    scale = 2400 / max(size.width, size.height)  # ~220 dpi for a letter or A4 page
    image = page.thumbnailOfSize_forBox_((size.width * scale, size.height * scale), box)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(["es-ES", "en-US"])
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        image.CGImageForProposedRect_context_hints_(None, None, None)[0], None
    )
    handler.performRequests_error_([request], None)
    return "\n".join(found.topCandidates_(1)[0].string() for found in request.results() or [])


LIGATURES = ("fi", "fl", "ff", "ffi", "ffl")


def unligature(left, right, words):
    """left + right, two pieces of one line, with the ligature the PDF has no text for put back between them.
    words are the words printed on the page (lower case), which is how the right ligature is known."""
    a = re.search(r"\w*(?=\s*$)", left)[0].casefold()
    b = re.match(r"\w*", right)[0].casefold()
    if not b or ((not a or a in words) and b in words):
        return left + right
    for lig in LIGATURES:
        if a and a + lig + b in words:  # "grá " + "cos" -> "gráficos"
            return left.rstrip() + lig + right
    for lig in LIGATURES:
        if lig + b in words:  # "la " + "gura" -> "la figura"
            return left + lig + right
    return left + right


def pdf_lines(page):
    """The lines of a PDF page that has text, each as [text, baseline, height], in reading order."""
    # PDFKit starts a new piece of a line where the font changes and where a glyph has no text (fi and fl in fonts
    # made by some web-page printers), and may then put the rest of that line after the line below.
    lines = []  # [pieces, right edge, baseline, height]
    whole = page.selectionForRange_((0, page.numberOfCharacters()))
    for piece in whole.selectionsByLine() if whole else []:
        # ligatures such as ﬁ back to plain letters; soft hyphens are not for reading
        text = unicodedata.normalize("NFKC", piece.string() or "").replace("\xad", "")
        if not text.strip():
            continue
        box = piece.boundsForPage_(page)
        x, y, h = box.origin.x, box.origin.y, box.size.height
        for line in lines[:-4:-1]:
            if abs(line[2] - y) < 0.3 * h and (-2 <= x - line[1] < h or (line is lines[-1] and x > line[1] - 2)):
                line[0].append(text)
                line[1], line[3] = x + box.size.width, max(line[3], h)
                break
        else:
            lines.append([[text], x + box.size.width, y, h])
    # ponytail: every page with a line in pieces (any italic word) is also read by text recognition, about a
    # second a page. Check the fonts for missing ToUnicode maps first if long books open too slowly.
    words = None
    if any(len(line[0]) > 1 for line in lines):
        words = set(re.findall(r"\w+", unicodedata.normalize("NFKC", ocr(page)).casefold()))
    out = []
    for pieces, _, y, h in lines:
        text = ""
        for piece in pieces:
            text = unligature(text, piece, words) if words else text + piece
        out.append([text.strip(), y, h])
    return out


def pdf_text(data):
    """(text, page_starts) of a PDF: its body text with one paragraph per line, and where each page begins in it."""
    # Left out: running headers and footers, page numbers, footnotes, captions and contents lines.
    # ponytail: paragraphs are rebuilt from the space between lines, their heights and their lengths, and column
    # order is PDFKit's (right in the generated two-column checks). Tables and formulas are read as they come:
    # telling them from text needs a layout model.
    global busy
    import Quartz

    pdf = Quartz.PDFDocument.alloc().initWithData_(Quartz.NSData.dataWithBytes_length_(data, len(data)))
    count = pdf.pageCount() if pdf else 0
    pages = []  # for each page, its lines as [text, baseline, height]; a recognised line has no baseline
    for n in range(count):
        page = pdf.pageAtIndex_(n)
        busy = f"Reading page {n + 1} of {count}…"
        if (page.string() or "").strip():
            pages.append([line for line in pdf_lines(page) if line[0] and not re.search(LEADERS, line[0])])
        else:  # a scan, or an empty page
            text = unicodedata.normalize("NFKC", ocr(page))
            pages.append([[line.strip(), None, 0] for line in text.splitlines() if line.strip()])
    busy = ""
    # Running headers, footers and page numbers: the same line, digits aside, at the top or bottom of 3 pages
    # or more, two of them at most a page apart. (Chapter headings also repeat, but further apart.)
    key = lambda line: re.sub(r"\d+|^[ivxlcdm]+$", "#", line[0], flags=re.I)
    seen = {}  # key -> the pages that have it among their first or last two lines
    for n, lines in enumerate(pages):
        for k in {key(line) for line in lines[:2] + lines[-2:]}:
            seen.setdefault(k, []).append(n)
    running = {k for k, on in seen.items() if len(on) >= 3 and min(b - a for a, b in zip(on, on[1:])) <= 2}
    for lines in pages:
        while lines and key(lines[0]) in running:
            del lines[0]
        while lines and key(lines[-1]) in running:
            del lines[-1]
    # A web address counts for nothing in a line's length: it is not spoken.
    length = lambda text: len(re.sub(r"\(?\s*https?://\S*\s*\)?", "", text))
    lengths = sorted(len(line[0]) for lines in pages for line in lines)
    full = lengths[len(lengths) * 4 // 5] if lengths else 0  # the length of a line that fills the column
    common = lambda values: max(set(values), key=values.count) if values else 0
    body = common([round(h) for lines in pages for _, y, h in lines if y is not None])  # height of a body line
    pitch = common(  # and how far apart two of them are within a paragraph
        [round(a[1] - b[1]) for lines in pages for a, b in zip(lines, lines[1:]) if a[1] and b[1] and a[1] > b[1]]
    )
    # Footnotes: small lines that end a page, the first starting with a number or mark, under lines of body size.
    small = lambda line: line[1] is not None and line[2] < 0.92 * body
    for lines in pages:
        for n, line in enumerate(lines):
            if small(line) and re.match(NOTE, line[0]) and all(map(small, lines[n:])) and not all(map(small, lines[:n])):
                del lines[n:]
                break
    # Hyphenated words printed whole somewhere ("real-time") keep their hyphen when a line ends inside them.
    compounds = set(re.findall(r"[^\W\d]+-[^\W\d]+", "\n".join(line[0] for lines in pages for line in lines).casefold()))
    out, starts, size, prev = [], [], 0, None
    for lines in pages:
        starts.append(size)
        for n, (line, y, h) in enumerate(lines):
            if prev:
                was, y0, h0 = prev
                # How far below the line before, in body line steps for lines this tall (0: unknown, or not below)
                drop = (y0 - y) / (pitch * max(h, h0) / body) if n and y and y0 and pitch and body else 0
            if not prev:
                gap = ""
            elif re.search(r"[^\W\d]-$", was) and line[0].islower():  # a word hyphenated across lines
                gap = ""
                word = re.search(r"[^\W\d]+-$", was)[0] + re.match(r"[^\W\d]*", line)[0]
                if "/" not in was.split()[-1] and word.casefold() not in compounds:  # not an address or a compound
                    out[-1], size = out[-1][:-1], size - 1
            elif (not n or drop < 0) and y and y0 and not re.search(END + "$", was) and line[0].islower():
                gap = " "  # a sentence that carries on in the next column or page
            elif 0 < drop < 1.2 and h0 > 1.3 * body and abs(h - h0) < 0.05 * h:  # a heading on two lines
                gap = " "
            elif (
                drop > 1.2  # space above the line
                or (drop > 0 and max(h, h0) > 1.3 * min(h, h0))  # a heading and its text
                or length(was) < 0.6 * full  # a short line: a heading, a list item, or the end of a paragraph
                or (length(was) < 0.85 * full and re.search(END + "$", was))
                or re.match(BULLET, line)
            ):
                gap = "\n"
            else:
                gap = " "
            if n == 0:
                starts[-1] = size + len(gap)
            out.append(gap + line)
            size += len(gap) + len(line)
            prev = line, y, h
    text = "".join(out)
    for found in reversed(list(re.finditer(CAPTION, text))):
        a, b = found.span()
        text = text[:a] + text[b:]
        starts = [s - (b - a) if s >= b else min(s, a) for s in starts]
    return text, starts


def page_text(url=None, html=None, pdf=None):
    """(text, page_starts) of a web page or PDF, given its address, its HTML, or the PDF's bytes."""
    import trafilatura

    if url is not None:
        if not re.match(r"(https?|file)://", url):
            return "That is not a web page.", []
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh) LocalTTS"})
            html = urllib.request.urlopen(request, timeout=20).read(50_000_000)
        except Exception:
            html = None
        if html and html.lstrip()[:5] == b"%PDF-":
            pdf, html = html, None
    if pdf is not None:
        # ponytail: the cache is never emptied; a book's text is small. Delete the folder if it ever matters.
        cached = CACHE / f"{hashlib.sha256(pdf).hexdigest()}-{PDF_RULES}.json"
        try:
            text, starts = json.loads(cached.read_text())
        except (OSError, ValueError):
            text, starts = pdf_text(pdf)
            if text:
                CACHE.mkdir(parents=True, exist_ok=True)
                cached.write_text(json.dumps([text, starts]))
        return (text, starts) if text else ("I could not find any text in this PDF.", [])
    # Article extraction first; pages that are not articles (apps, feeds, short pages) fall back to all visible text.
    text = html and (trafilatura.extract(html) or trafilatura.extract(html, favor_recall=True) or trafilatura.html2txt(html))
    text = text or "I could not get the text of this page."
    return re.sub(r"\[(\d+|edit|citation needed)\]", "", text), []  # footnote and edit markers are noise read aloud


def file_text(path):
    """(text, page_starts) of a file: a PDF, a web page, plain text or Markdown, or what AppKit reads (Word, RTF)."""
    data = Path(path).read_bytes()
    kind = Path(path).suffix.lower()
    if data[:5] == b"%PDF-":
        return page_text(pdf=data)
    if kind in (".html", ".htm"):
        return page_text(html=data)
    if kind in ("", ".txt", ".text", ".md", ".markdown"):
        text = data.decode("utf-8", "replace")
        if kind.startswith(".m"):  # Markdown marks are noise read aloud; links keep their words
            text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
            text = re.sub(r"^#+\s*|[*`]+|^[-=_]{3,}\s*$", "", text, flags=re.M)
        # ponytail: a file with blank lines between paragraphs and no long line is taken as hard-wrapped, and
        # its lines are joined unless the next one starts a list item
        if "\n\n" in text and max(map(len, text.splitlines())) <= 100:
            text = re.sub(rf"(?<!\n)\n(?!\n|{BULLET})", " ", text)
        return text, []
    import AppKit

    url = AppKit.NSURL.fileURLWithPath_(str(path))
    rich = AppKit.NSAttributedString.alloc().initWithURL_options_documentAttributes_error_(url, {}, None, None)[0]
    if rich is None:
        raise ValueError("LocalTTS cannot read this kind of file")
    return rich.string(), []


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


def generator():
    # MLX wants the model used on the thread that loaded it
    global ready, SAMPLE_RATE
    import mlx.core as mx
    import numpy as np
    from mlx_audio.tts.utils import load_model

    model = loaded = job = None
    while True:
        repo = MODELS[cfg["voice"].split("/")[0]]["repo"]
        if loaded != repo:
            ready, model, loaded = False, None, None
            mx.clear_cache()  # free the other model first
            try:
                model = load_model(repo)
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
                        mx.random.seed(int(cfg["seed"]) + attempt)
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
        state = {"read": read_id, "status": status(), "index": current, "total": len(passages), "error": error}
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
            submit(body, panel=panel)
        elif url.path == "/page":
            submit(*page_text(url=body), panel=panel, blocks=True)
        elif url.path == "/html":
            submit(*page_text(html=body), panel=panel, blocks=True)
        elif url.path == "/pdf":
            submit(*page_text(pdf=raw), panel=panel, blocks=True)
        else:
            return self.send_error(404)
        self.reply({"read": read_id})

    def log_message(self, *args):
        pass


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
            goto(bisect_left(doc["pages"], sender.intValue() - 1))

        def back_(self, sender):
            skip(-1)

        def pause_(self, sender):
            toggle_pause() if active else goto(current)

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
            parts, idx, rid, state, d = passages, current, read_id, status(), doc
            failed = error if state == "idle" else ""
            self.title = "⚠️" if failed else "⏳" if not ready else {"idle": "🔈", "reading": "🔊", "paused": "⏸"}[state]
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
                or busy
                or ("Getting the voice ready…" if not ready else state.capitalize().replace("Idle", "Ready") + where)
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
                if state != "idle" and cfg["show_text"] and own_panel:
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
    icon = Path(__file__).with_name("icon.icns")
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
        if os.path.isfile(paths[0]) and not os.path.samefile(paths[0], __file__):
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


def selftest():
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
    assert (status(), current, len(passages)) == ("loading", 0, 3)
    skip(1), skip(1), skip(1)
    assert current == 2, "skip stops at the last passage"
    skip(-1)
    assert current == 1 and read_id == first, "skipping keeps the same read"
    assert jobs.queue[-1] == (gen, passages, 1)
    start([])
    assert (status(), current, len(passages)) == ("idle", 1, 3), "stopping keeps the text and the place"
    skip(1)
    assert status() == "idle", "skip does nothing when idle"
    seek(doc["starts"][2] + 5)
    assert (status(), current) == ("loading", 2), "a click reads from the passage clicked"
    assert submit(" \n", panel=False) == first + 1
    assert (status(), error, len(passages)) == ("idle", "There is no text to read.", 3)
    submit("Uno. Dos.", page_starts=[0, 5], panel=False)
    assert error == "" and doc["pages"] == [0], "a new read clears the error"
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
        threading.Thread(target=generator, daemon=True).start()
        threading.Thread(target=player, daemon=True).start()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        run_app(show_window="--background" not in sys.argv)  # AppKit needs the main thread
