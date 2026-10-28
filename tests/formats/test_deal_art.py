from io import BytesIO

import pytest
from PIL import Image

from openfesl.formats.deal_art import BIG, FREE, OVERHANG, SMALL, deal_art

GREEN = (20, 200, 60, 255)
TILE = (205, 191, 166, 255)
INK = (10, 10, 10, 255)


def made(icons: list[Image.Image | None], big: bool = True) -> Image.Image:
    data = deal_art(icons, big)
    assert data.startswith(b"\x89PNG")
    picture = Image.open(BytesIO(data)).convert("RGBA")
    assert picture.size == (BIG if big else SMALL)
    return picture


def opaque(picture: Image.Image) -> set[tuple[int, ...]]:
    found = picture.getcolors(picture.width * picture.height) or []
    return {colour for _, colour in found if isinstance(colour, tuple) and colour[3] == 255}


@pytest.mark.parametrize("big", [True, False])
def test_a_deal_picture_keeps_its_items_over_the_free_middle_of_the_games_plate(
    big: bool,
) -> None:
    icon = Image.new("RGBA", (64, 64), GREEN)
    left, top, right, bottom = FREE[big]
    # The band above the big plate stays clear, which the game's hot deals title runs into.
    assert top > OVERHANG or not big
    for count in range(1, 10):
        picture = made([icon] * count, big)
        drawn = picture.getchannel("A").getbbox()
        assert drawn is not None and GREEN in opaque(picture)
        # Clear of the game's magnifier, sticker, price bar and more button around the middle.
        assert left <= drawn[0] and top <= drawn[1] and drawn[2] <= right and drawn[3] <= bottom


def test_items_without_icons_stand_on_plain_tiles_and_wide_icons_keep_their_shape() -> None:
    # No glyph, number or word: the tiles' fill and edge alone.
    assert opaque(made([None] * 3)) == {TILE, INK}
    wide = made([Image.new("RGBA", (80, 50), GREEN)])
    green = [
        (x, y)
        for y in range(wide.height)
        for x in range(wide.width)
        if wide.getpixel((x, y)) == GREEN
    ]
    width = max(x for x, _ in green) - min(x for x, _ in green) + 1
    height = max(y for _, y in green) - min(y for _, y in green) + 1
    assert round(width / height, 1) == 1.6
