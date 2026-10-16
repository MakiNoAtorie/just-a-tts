"""Text into passages: where sentences and paragraphs end, and which language a passage is in."""

import re

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

