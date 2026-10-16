"""Checks of the PDF text, on PDFs drawn here: uv run python test_pdf.py"""
import AppKit
import Quartz

from tts_daemon import pdf_text, pieces, unligature

WORDS = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore".split()


def make_pdf(pages):
    """A PDF with, on each page, the given (x, y, font size, text) lines."""
    data = AppKit.NSMutableData.data()
    ctx = Quartz.CGPDFContextCreate(Quartz.CGDataConsumerCreateWithCFData(data), ((0, 0), (612, 792)), None)
    for lines in pages:
        Quartz.CGPDFContextBeginPage(ctx, None)
        AppKit.NSGraphicsContext.setCurrentContext_(
            AppKit.NSGraphicsContext.graphicsContextWithCGContext_flipped_(ctx, False)
        )
        for x, y, size, text in lines:
            font = AppKit.NSFont.fontWithName_size_("Times New Roman", size)
            AppKit.NSString.stringWithString_(text).drawAtPoint_withAttributes_((x, y), {AppKit.NSFontAttributeName: font})
        Quartz.CGPDFContextEndPage(ctx)
    Quartz.CGPDFContextClose(ctx)
    return bytes(data)


def column(x, top, count, tag, width=11):
    """count full lines of filler, 14 points apart, each starting with its tag and number."""
    return [(x, top - 14 * n, 11, f"{tag}{n} " + " ".join(WORDS[(n + k) % 15] for k in range(width))) for n in range(count)]


words = {"gráficos", "la", "figura", "confiando", "con", "flujo", "un", "o", "work", "eficiente"}
assert unligature("con Kanban y grá ", "cos de Burndown.", words) == "con Kanban y gráficos de Burndown."
assert unligature("en la ", "gura:", words) == "en la figura:"
assert unligature("Terminan con ", "ando en", words) == "Terminan confiando en"
assert unligature("", "ujo más", words) == "flujo más"
assert unligature("un flujo e ", "ciente de", words) == "un flujo eficiente de"
assert unligature("El WIP o ", "Work in", words) == "El WIP o Work in"

# Two columns, drawn row by row: the left one is read first.
rows = [line for pair in zip(column(72, 700, 20, "L", 5), column(320, 700, 20, "R", 5)) for line in pair]
text, _ = pdf_text(make_pdf([rows]))
assert text.index("L19") < text.index("R0"), text

# Three pages of a book: running header, page numbers, a title on two lines, hyphens, a footnote, a caption,
# a contents line, and a sentence that carries on over the page.
book = []
for n in range(3):
    page = [(72, 760, 9, "The Running Header"), (300, 30, 9, ["ii", "3", "4"][n])] + column(72, 700, 10, f"P{n}L")
    book.append(page)
book[0] += [(72, 740, 22, "A Title That Is Set"), (72, 716, 22, "On Two Lines")]
book[0][2:12] = column(72, 680, 10, "P0L")
book[0] += [
    (72, 520, 11, "Chapter One " + ". " * 30 + "12"),
    (72, 490, 11, "this line shows a real-time thing and then some more words until a real-"),
    (72, 476, 11, "time one is cut by the line end, as is the plain word incidi-"),
    (72, 462, 11, "dunt here. The paragraph ends with a note.[12]"),
    (72, 420, 11, "Figure 3: the caption of a figure."),
    (72, 380, 11, "A last paragraph of the page which runs on to the very end of its last line and"),
    (72, 60, 8, "1 A footnote in small print at the foot of the page,"),
    (72, 50, 8, "on two lines."),
]
book[1][2:] = [(72, 700, 11, "carries on over the page. Then it ends.")] + column(72, 660, 10, "P1L")
text, starts = pdf_text(make_pdf(book))
lines = text.split("\n")
assert lines[0] == "A Title That Is Set On Two Lines", lines[0]
for gone in ("Running Header", "footnote", "two lines.", "caption", "Chapter One", "\nii", "\n3\n"):
    assert gone not in text, (gone, text)
assert "until a real-time one" in text and "word incididunt here" in text, text
assert "last line and carries on over the page. Then it ends." in text, text
assert [text[s : s + 4] for s in starts[1:]] == ["carr", "P2L0"], starts
spoken = " ".join(p[2] for p in pieces(text + "\nSee (https://a.example/x-y) and https://www.b.example/z?q=1 now.", blocks=True))
assert "ends with a note. " in spoken and "[12]" in text, spoken
assert spoken.endswith("See and b.example now."), spoken
print("ok")
