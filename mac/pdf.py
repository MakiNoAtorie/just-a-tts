"""PDF text on macOS: lines read by PDFKit, scans read by Vision."""

import re
import unicodedata

from core import engine
from core.pdf import layout, unligature
from core.text import LEADERS


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
    import Quartz

    pdf = Quartz.PDFDocument.alloc().initWithData_(Quartz.NSData.dataWithBytes_length_(data, len(data)))
    count = pdf.pageCount() if pdf else 0
    pages = []  # for each page, its lines as [text, baseline, height]; a recognised line has no baseline
    for n in range(count):
        page = pdf.pageAtIndex_(n)
        engine.busy = f"Reading page {n + 1} of {count}…"
        if (page.string() or "").strip():
            pages.append([line for line in pdf_lines(page) if line[0] and not re.search(LEADERS, line[0])])
        else:  # a scan, or an empty page
            text = unicodedata.normalize("NFKC", ocr(page))
            pages.append([[line.strip(), None, 0] for line in text.splitlines() if line.strip()])
    engine.busy = ""
    return layout(pages)
