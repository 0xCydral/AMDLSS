"""Draws the AMDLSS icon (a small square growing into a larger one, on the launcher's dark stage) as .ico.
usage: python packaging/make_icon.py amdlss.ico   (needs Pillow)"""
import sys

from PIL import Image, ImageDraw

S = 1024  # drawn large, scaled down for every icon size


def draw():
    image = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=190, fill=(15, 13, 15, 255), outline=(74, 58, 54, 255),
                        width=14)
    d.rectangle((150, 560, 450, 860), fill=(160, 146, 130, 255))  # the small picture
    d.rectangle((420, 170, 880, 630), fill=(142, 26, 31, 255), outline=(200, 169, 106, 255), width=18)  # the big one
    d.line((470, 540, 720, 290), fill=(232, 223, 207, 255), width=56)  # the arrow between them
    d.polygon([(620, 250), (810, 240), (800, 430)], fill=(232, 223, 207, 255))
    return image


if __name__ == '__main__':
    draw().save(sys.argv[1], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
