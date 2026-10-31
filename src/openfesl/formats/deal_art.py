import math
from collections.abc import Sequence
from io import BytesIO

from PIL import Image, ImageDraw, ImageOps

# The game's home screen shows the pictures unscaled (BFHeroes 1.89 menu, HotDealLarge and
# HotDealSmall): the big one placed 58 px above its 308 x 297 plate, so that its top may overhang
# the plate, and the small one over its 201 x 196 plate.
BIG = (308, 355)
SMALL = (200, 195)
OVERHANG = 58
# The part of each picture over its plate's free middle, as left, top, right and bottom: clear of
# the game's magnifier and discount sticker in the top corners, the hot deals' title over the big
# plates' inner corners, and the price bar and "more" button along the bottom (measured on the
# 1.89 home screen, where the small picture stands 7 px above its plate).
FREE = {True: (15, OVERHANG + 42, 303, OVERHANG + 242), False: (13, 7 + 52, 198, 7 + 132)}
# The most items each picture shows, and its largest tile: about twice a 64 px icon on the big
# picture, about its own size on the small one.
_MOST = {True: 7, False: 5}
_LARGEST = {True: 150, False: 80}
_GAP = 0.08
_TILE = "#CDBFA6"
_INK = "#0A0A0A"

# Where a tile stands, in tiles from the middle of the group, and how far it is turned in degrees.
type _Placement = tuple[float, float, float]


def fits(data: bytes, big: bool) -> bool:
    """Tell whether an uploaded picture is a PNG of the size the game shows a deal's picture.

    The game shows the pictures unscaled: the big one 308 px wide and up to 355 px tall, the
    small one exactly 200 by 195 px.

    Args:
        data: The uploaded file.
        big: Whether it is to be the big picture rather than the small one.
    """
    try:
        with Image.open(BytesIO(data), formats=["PNG"]) as picture:
            width, height = picture.size
    except OSError:
        return False
    if big:
        return width == BIG[0] and height <= BIG[1]
    return (width, height) == SMALL


def deal_art(icons: Sequence[Image.Image | None], big: bool) -> bytes:
    """Return a hot deal's picture for the game's home screen, as a PNG with transparency.

    Each item stands on a tile with its imported icon, or on a plain tile where none is shown:
    one alone, two to four in a row or in two rows, whichever shows them larger, and five or
    more in a fan. They stay over the plate's free middle, and the picture holds nothing else.

    Args:
        icons: The deal's items in order, each as its icon or none.
        big: Whether it is the big picture rather than the small one.
    """
    canvas = Image.new("RGBA", BIG if big else SMALL)
    shown = icons[: _MOST[big]]
    count = len(shown)
    left, top, right, bottom = FREE[big]
    width, height = right - left, bottom - top
    layouts: list[list[_Placement]]
    if count > 4:
        # Cards turned about a point below them, from -30° to +30°.
        fan = []
        for index in range(count):
            angle = -30 + 60 * index / (count - 1)
            turn = math.radians(angle)
            fan.append((1.36 * math.sin(turn), 1.36 * (1 - math.cos(turn)), angle))
        layouts = [fan]
    else:
        step = 1 + _GAP
        rows = [[count], [count // 2, count - count // 2]] if count > 1 else [[count]]
        layouts = [
            [
                ((index - (length - 1) / 2) * step, (line - (len(lengths) - 1) / 2) * step, 0.0)
                for line, length in enumerate(lengths)
                for index in range(length)
            ]
            for lengths in rows
        ]

    def size(layout: list[_Placement]) -> float:
        low_x, low_y, high_x, high_y = _bounds(layout)
        return min(width / (high_x - low_x), height / (high_y - low_y), _LARGEST[big])

    layout = max(layouts, key=size)
    side = math.floor(size(layout))
    low_x, low_y, high_x, high_y = _bounds(layout)
    middle_x = (left + right) / 2 - (low_x + high_x) / 2 * side
    middle_y = (top + bottom) / 2 - (low_y + high_y) / 2 * side
    for icon, (x, y, angle) in zip(shown, layout, strict=True):
        tile = Image.new("RGBA", (side, side))
        ImageDraw.Draw(tile).rounded_rectangle(
            (0, 0, side - 1, side - 1),
            radius=side * 0.1,
            fill=_TILE,
            outline=_INK,
            width=max(2, round(side * 0.035)),
        )
        if icon is not None:
            inner = round(side * 0.84)
            fitted = ImageOps.contain(
                icon.convert("RGBA"), (inner, inner), Image.Resampling.LANCZOS
            )
            tile.alpha_composite(fitted, ((side - fitted.width) // 2, (side - fitted.height) // 2))
        if angle:
            tile = tile.rotate(-angle, Image.Resampling.BICUBIC, expand=True)
        corner = (middle_x + x * side - tile.width / 2, middle_y + y * side - tile.height / 2)
        canvas.alpha_composite(tile, (round(corner[0]), round(corner[1])))
    out = BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def _bounds(layout: list[_Placement]) -> tuple[float, float, float, float]:
    # The group's left, top, right and bottom in tiles, a turned tile taking more room.
    halves = [
        (x, y, (abs(math.cos(math.radians(angle))) + abs(math.sin(math.radians(angle)))) / 2)
        for x, y, angle in layout
    ]
    return (
        min(x - half for x, _, half in halves),
        min(y - half for _, y, half in halves),
        max(x + half for x, _, half in halves),
        max(y + half for _, y, half in halves),
    )
