"""Draws the app icon (speaker on a rounded gradient square) and writes icon.icns next to this file."""
import subprocess
import tempfile
from pathlib import Path

import AppKit

SIZE = 1024
image = AppKit.NSImage.alloc().initWithSize_((SIZE, SIZE))
image.lockFocus()
square = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(AppKit.NSMakeRect(100, 100, 824, 824), 185, 185)
top = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.42, 0.36, 0.95, 1)
bottom = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.20, 0.55, 0.98, 1)
AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(top, bottom).drawInBezierPath_angle_(square, -90)
config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
    400, AppKit.NSFontWeightSemibold
).configurationByApplyingConfiguration_(
    AppKit.NSImageSymbolConfiguration.configurationWithPaletteColors_([AppKit.NSColor.whiteColor()])
)
symbol = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
    "speaker.wave.2.fill", None
).imageWithSymbolConfiguration_(config)
w, h = symbol.size()
symbol.drawInRect_(AppKit.NSMakeRect((SIZE - w) / 2, (SIZE - h) / 2, w, h))
image.unlockFocus()

with tempfile.TemporaryDirectory() as tmp:
    master = Path(tmp) / "master.png"
    bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
    bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, None).writeToFile_atomically_(str(master), True)
    iconset = Path(tmp) / "icon.iconset"
    iconset.mkdir()
    for points in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{points}x{points}{'@2x' if scale == 2 else ''}.png"
            px = str(points * scale)
            subprocess.run(["sips", "-z", px, px, str(master), "--out", str(iconset / name)], check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(Path(__file__).with_name("icon.icns"))], check=True)
