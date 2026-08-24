#!/usr/bin/env python3
"""Generate the app icon (.ico) and the MSIX PNG assets from one drawing.

Run once (needs Pillow):  python packaging/make_icons.py
Outputs:
  packaging/app.ico                      - used by the desktop/Start-menu shortcut
  packaging/assets/Square44x44Logo.png   - MSIX small tile / taskbar
  packaging/assets/Square150x150Logo.png - MSIX medium tile
  packaging/assets/StoreLogo.png         - MSIX store logo
  packaging/assets/Wide310x150Logo.png   - MSIX wide tile
  packaging/assets/SplashScreen.png      - MSIX splash
"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")
os.makedirs(ASSETS, exist_ok=True)

BG1 = (24, 33, 54)      # deep navy
BG2 = (37, 99, 160)     # blue
ACCENT = (94, 214, 194)  # teal
DISC = (222, 230, 240)   # disc silver


def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_icon(size):
    """A DVD disc with a little sound-wave, on a rounded gradient tile."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # vertical gradient background with rounded corners
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), (
            int(BG1[0] + (BG2[0] - BG1[0]) * t),
            int(BG1[1] + (BG2[1] - BG1[1]) * t),
            int(BG1[2] + (BG2[2] - BG1[2]) * t),
        ))
    grad = grad.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    r = max(2, size // 8)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=r, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # DVD disc
    cx, cy = size * 0.5, size * 0.52
    R = size * 0.34
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=DISC)
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(150, 165, 185),
              width=max(1, size // 96))
    # inner reflection ring
    r2 = R * 0.62
    d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], outline=ACCENT,
              width=max(1, size // 64))
    # centre hole
    rh = R * 0.16
    d.ellipse([cx - rh, cy - rh, cx + rh, cy + rh], fill=(24, 33, 54))

    # sound-wave bars over the disc
    bar_w = max(1, size // 40)
    heights = [0.10, 0.20, 0.30, 0.20, 0.12]
    gap = bar_w * 2
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    x0 = cx - total / 2
    for i, h in enumerate(heights):
        bx = x0 + i * (bar_w + gap)
        bh = size * h
        d.rounded_rectangle([bx, cy - bh, bx + bar_w, cy + bh],
                            radius=bar_w // 2, fill=BG1)
    return img


def main():
    base = draw_icon(256)

    # .ico with several sizes
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                 (128, 128), (256, 256)]
    base.save(os.path.join(HERE, "app.ico"), sizes=ico_sizes)

    # MSIX PNG assets (transparent tiles are fine)
    draw_icon(44).save(os.path.join(ASSETS, "Square44x44Logo.png"))
    draw_icon(150).save(os.path.join(ASSETS, "Square150x150Logo.png"))
    draw_icon(50).save(os.path.join(ASSETS, "StoreLogo.png"))

    # wide tile: centre the square icon on a gradient strip
    wide = Image.new("RGBA", (310, 150), (0, 0, 0, 0))
    strip = draw_icon(310)
    wide.paste(strip.crop((0, 80, 310, 230)), (0, 0))
    ic = draw_icon(120)
    wide.alpha_composite(ic, (16, 15))
    d = ImageDraw.Draw(wide)
    d.text((150, 58), "DVD\nIdentifier", font=_font(26), fill=(255, 255, 255))
    wide.save(os.path.join(ASSETS, "Wide310x150Logo.png"))

    splash = Image.new("RGBA", (620, 300), (24, 33, 54, 255))
    splash.alpha_composite(draw_icon(200), (40, 50))
    ImageDraw.Draw(splash).text((270, 120), "DVD Episode\nIdentifier",
                                font=_font(40), fill=(255, 255, 255))
    splash.save(os.path.join(ASSETS, "SplashScreen.png"))

    print("Wrote app.ico and assets to", ASSETS)


if __name__ == "__main__":
    main()
