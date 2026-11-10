# python tools/make_assets.py : draws the add-on's icon and the 8x8 white texture the preview box is tinted from
import os
from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'service.seekpreview')
os.makedirs(os.path.join(ROOT, 'resources', 'media'), exist_ok=True)
Image.new('RGBA', (8, 8), (255, 255, 255, 255)).save(os.path.join(ROOT, 'resources', 'media', 'white.png'))

S = 4  # draw at 4x, scale down for smooth edges
W = 512 * S
im = Image.new('RGB', (W, W))
px = im.load()
a, b = (79, 70, 229), (192, 38, 211)  # indigo -> magenta
for y in range(W):
    for x in range(0, W):
        t = (x + y) / (2.0 * W)
        px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
d = ImageDraw.Draw(im)
u = W / 512.0
# the preview box with a play mark in it
d.rounded_rectangle([146 * u, 110 * u, 366 * u, 240 * u], radius=18 * u, fill=(255, 255, 255))
d.polygon([(238 * u, 148 * u), (238 * u, 202 * u), (284 * u, 175 * u)], fill=a)
d.polygon([(240 * u, 240 * u), (272 * u, 240 * u), (256 * u, 262 * u)], fill=(255, 255, 255))  # pointer down to the bar
# the seek bar: played part, knob, rest
d.rounded_rectangle([70 * u, 318 * u, 442 * u, 334 * u], radius=8 * u, fill=(255, 255, 255, 90) if False else (190, 170, 240))
d.rounded_rectangle([70 * u, 318 * u, 256 * u, 334 * u], radius=8 * u, fill=(255, 255, 255))
d.ellipse([234 * u, 304 * u, 278 * u, 348 * u], fill=(255, 255, 255))
im.resize((512, 512), Image.LANCZOS).save(os.path.join(ROOT, 'icon.png'))
print('icon.png and white.png written')
