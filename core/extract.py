"""Text of web pages, PDFs and files."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from core.text import BULLET

CACHE = Path.home() / "Library/Caches/LocalTTS"  # the text of PDFs opened before, by content
PDF_RULES = 2  # raise when pdf_text changes what it gives, so cached text is not used
# Set by the platform: PDF bytes -> (text, page_starts), and a Word or RTF file's path -> its text.
pdf_text = rich_text = None


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
    """(text, page_starts) of a file: a PDF, a web page, plain text or Markdown, or what the platform reads (Word, RTF)."""
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
    return rich_text(path), []
