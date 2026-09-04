import io

import cairosvg
from PIL import Image

FULL = "unbagged-logo.svg"
SMALL = "unbagged-logo-small.svg"
BG = (239, 231, 220, 255)


def render(src, size):
    png = cairosvg.svg2png(url=src, output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


for size in (16, 32, 48):
    render(SMALL, size).save(f"favicon-{size}.png")

render(FULL, 512).save("icon-512.png")

inner = 180 - 2 * 22
mark = render(FULL, inner)
touch = Image.new("RGBA", (180, 180), BG)
touch.alpha_composite(mark, (22, 22))
touch.convert("RGB").save("apple-touch-icon-180.png")

ico = render(SMALL, 48)
ico.save("favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

print("done")
