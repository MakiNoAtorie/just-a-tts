"""PDF text rules that need no PDF library: lines with positions in, paragraphs out."""

import re

from core.text import BULLET, CAPTION, END, NOTE

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



def layout(pages):
    """(text, page_starts) from each page's lines as [text, baseline, height] (a recognised line has no baseline):
    the body text with one paragraph per line, and where each page begins in it."""
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
