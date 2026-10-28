import html
import json
import os
import re
from io import BytesIO
from pathlib import Path

import anyio
import httpx2
import pytest
from accounts import PASSWORD, post_sign_in
from game_files import COPY, NEEDS_COPY
from heroes import CREATOR
from PIL import Image, ImageChops, ImageStat
from playwright.async_api import Browser, Page
from support import cli, visible
from test_catalogue_import import extracted

from openfesl.web.pages import built_assets

pytestmark = pytest.mark.anyio

FIGURE = "/bfheroes/heroes/figure"
# A saved copy of the portrait that the game rendered of a National commando in its starting
# outfit, with the seventh skin tone and neither hair nor facial hair: the game writes each hero
# it renders over its own file, mods/bfheroes/mugshot.png.
PORTRAIT = os.environ.get("OPENFESL_TEST_BFHEROES_PORTRAIT")
# The part of the game's portrait that the stage shows, as in hero-model.js.
FRAME = (0.2227, 0.1592, 0.5829, 0.8161)
# A Royal commando, who wears the invented copy's default hat; the copy has no hair or beard of
# the Royal lists.
ROYAL = {
    "faction": "royal",
    "class": "commando",
    "skin_tone": "3",
    "hair_colour": "2",
    "hair_style": "0",
    "facial_hair": "0",
}
# A graphics card's name for the renderer of the tests' browser, which draws in software, where
# the stage keeps the class glyph and the look in words.
GRAPHICS_CARD = """
for (const context of [WebGLRenderingContext, WebGL2RenderingContext]) {
  const read = context.prototype.getParameter;
  context.prototype.getParameter = function (name) {
    return name === 0x9246 ? "Invented Graphics Card" : read.call(this, name);
  };
}
"""


async def import_models(base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Import the invented copy's catalogue as the admin does, and wait for the server."""
    await cli(capsys, "catalogue", "import", str(await extracted(tmp_path, capsys)))
    async with httpx2.AsyncClient() as client:
        with anyio.fail_after(5):
            while (await client.get(f"{base}{FIGURE}", params=ROYAL)).status_code != 200:
                await anyio.sleep(0.05)


def model_data(page: str) -> dict[str, object] | None:
    """Return the description of the game's model that a creator's page carries, if any."""
    found = re.search(r'<script type="application/json" data-model>(.*?)</script>', page)
    return json.loads(html.unescape(found.group(1))) if found else None


@pytest.mark.usefixtures("prompt_actions")
async def test_the_creator_offers_the_games_model_only_with_an_import(
    files: Path, base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}{CREATOR}")
        unknown = await client.get(f"{base}{FIGURE}", params=ROYAL)
        await import_models(base, tmp_path, capsys)
        offered = (await client.get(f"{base}{CREATOR}")).text
        answer = await client.get(f"{base}{FIGURE}", params=ROYAL)
        refused = await client.get(f"{base}{FIGURE}", params={**ROYAL, "skin_tone": "10"})

    # Without the models the stage shows the hero's look in words, and its script has nothing
    # to load.
    look = (
        r"Skin tone \d, hair colour \d, (hair style \d|no hair), (facial hair \d|no facial hair)\."
    )
    assert re.search(look, visible(page.content))
    assert model_data(page.text) is None and "data-figures" not in page.text
    assert unknown.status_code == 404
    assert model_data(offered) is not None
    assert 'data-figures="/bfheroes/heroes/figure"' in offered
    [kept] = (files / "catalogues" / "bfheroes").iterdir()
    folder = f"/catalogues/bfheroes/{kept.name}"
    # The hero wears its faction's default hat on its faction's skeleton, posed as the menu's
    # portrait from the game data; the copy has no texture that the hero wears.
    figure = answer.json()
    assert figure.pop("camera")["look_at"] == [0.0, 1.024, 0.0]
    assert figure == {
        "skeleton": f"{folder}/models/british_male_soldier.glb",
        "roles": {
            "idle": ["idle"],
            "faces": ["face_idle1"],
            "poses": ["hands", "default"],
            "nod": ["yes"],
            "flex": ["flex"],
            "wave": ["wave"],
            "cheer": ["cheer"],
        },
        "pose": ["idle", 5.6],
        "parts": [f"{folder}/models/invented_hat.glb"],
        "layers": [],
        "atlas": 1024,
        "warp": f"{folder}/textures/defaultlightwarpskinned.webp",
        "light": [0.5, 0.2, 0.0],
    }
    assert refused.status_code == 404


async def creator(
    browser: Browser, base: str, still: bool = False, styled: bool = False, card: bool = True
) -> Page:
    """Open the creator of ann, signed in, in a new window of a browser.

    Args:
        browser: The browser.
        base: The server's web address.
        still: Whether the window asks for reduced motion.
        styled: Whether the test may add styles that the page's policy forbids.
        card: Whether the browser names a graphics card as its renderer.
    """
    context = await browser.new_context(
        locale="en-GB", reduced_motion="reduce" if still else "no-preference", bypass_csp=styled
    )
    if card:
        await context.add_init_script(GRAPHICS_CARD)
    page = await context.new_page()
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
    for name, value in client.cookies.items():
        await context.add_cookies([{"name": name, "value": value, "url": base}])
    await page.goto(f"{base}{CREATOR}")
    return page


@pytest.mark.usefixtures("prompt_actions")
async def test_the_stage_shows_the_games_model_and_falls_back_to_the_look_in_words(
    files: Path, base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], edge: Browser
) -> None:
    if not built_assets().app:
        pytest.skip("the web pages' scripts are not built")
    await import_models(base, tmp_path, capsys)
    page = await creator(edge, base)
    stage = page.locator("[data-stage]")
    await page.locator("[data-stage][data-shown=model]").wait_for()
    model = page.get_by_role("img", name=re.compile("^The game's model of the hero: "))
    hidden = stage.locator("[data-stand-in][aria-hidden=true]")
    caption = stage.locator("figcaption")

    # Once the model's first frame is drawn, it takes the place and caption of the glyph and look.
    assert await model.count() == 1 and await hidden.count() == 1
    assert await caption.text_content() == (
        "The game's model of the hero, imported by the administrators of this service from "
        "their copy of the game."
    )
    # The model's script keeps three.js's licence notice, which has to travel with its code.
    script = await page.evaluate(
        "performance.getEntriesByType('resource').find((e) => e.name.includes('/hero-model-')).name"
    )
    async with httpx2.AsyncClient() as client:
        assert "Three.js Authors" in (await client.get(script)).text
    # Each choice gives the model its label too.
    await page.get_by_text("Royal Army", exact=True).click()
    for hero_class in ("Soldier", "Commando"):
        await page.get_by_text(hero_class, exact=True).click()
        await page.get_by_role("img", name=re.compile(f"{hero_class} of the Royal Army")).wait_for()
    # Surprise me changes four choices at once, and the stage asks the server for one look.
    await page.wait_for_load_state("networkidle")
    figures: list[str] = []
    page.on(
        "request", lambda request: figures.append(request.url) if FIGURE in request.url else None
    )
    await page.get_by_role("button", name="Surprise me").click()
    await page.wait_for_load_state("networkidle")
    assert len(figures) == 1
    # The browser takes the model's drawing away: the look in words returns at once, with its
    # caption.
    await page.evaluate(
        "document.querySelector('canvas').getContext('webgl2')"
        ".getExtension('WEBGL_lose_context').loseContext()"
    )
    await page.locator("[data-stage]:not([data-shown])").wait_for()
    assert await page.locator("canvas").count() == 0 and await hidden.count() == 0
    assert await caption.text_content() == "The game shows the hero with its own models."

    # A browser without WebGL2 keeps the look in words and loads none of the model's files, and
    # so does one that draws WebGL in software, where loading the model would hold the page up.
    without = await creator(edge, base)
    await without.context.add_init_script("delete window.WebGL2RenderingContext")
    software = await creator(edge, base, card=False)
    asked: list[str] = []
    for unmodelled in (without, software):
        unmodelled.on("request", lambda request: asked.append(request.url))
        await unmodelled.reload()
        await unmodelled.wait_for_load_state("networkidle")
        assert await unmodelled.locator("canvas").count() == 0
    assert not [address for address in asked if "/catalogues/" in address or "model" in address]

    # Files that fail to load leave the look in words too.
    failing = await creator(edge, base)
    await failing.route(re.compile(r"\.glb$"), lambda route: route.abort())
    await failing.reload()
    await failing.wait_for_load_state("networkidle")
    assert await failing.locator("canvas").count() == 0
    assert await failing.locator("[data-stage][data-shown]").count() == 0


@NEEDS_COPY
@pytest.mark.skipif(
    not PORTRAIT, reason="OPENFESL_TEST_BFHEROES_PORTRAIT names no saved portrait from the game"
)
@pytest.mark.usefixtures("prompt_actions")
async def test_the_stage_draws_and_moves_the_hero_as_the_games_menu_does(
    files: Path, base: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], edge: Browser
) -> None:
    assert COPY and PORTRAIT
    if not built_assets().app:
        pytest.skip("the web pages' scripts are not built")
    catalogue = tmp_path / "catalogue.zip"
    await cli(capsys, "catalogue", "extract", COPY, "-o", str(catalogue))
    await cli(capsys, "catalogue", "import", str(catalogue))
    async with httpx2.AsyncClient() as client:
        with anyio.fail_after(30):
            while (await client.get(f"{base}{FIGURE}", params=ROYAL)).status_code != 200:
                await anyio.sleep(0.1)
    page = await creator(edge, base, still=True, styled=True)
    await page.get_by_text("National Army", exact=True).click()
    await page.get_by_text("Commando", exact=True).click()
    await page.get_by_label("Skin tone 7 of 9").check()
    for tab, panel in (("Hair style", "#look-hair"), ("Facial hair", "#look-beard")):
        await page.get_by_role("tab", name=tab).click()
        await page.locator(f"{panel} fieldset:not([hidden]) .look-tile").first.click()
    await page.locator("[data-stage][data-shown=model]").wait_for()
    await page.wait_for_load_state("networkidle")
    # The model alone, on a colour that neither picture holds.
    await page.add_style_tag(
        content="* { visibility: hidden } canvas { visibility: visible }"
        " .site-sky, .stage-sky { display: none } html, body { background: #ff00ff }"
    )
    drawn = Image.open(BytesIO(await page.locator("canvas").screenshot())).convert("RGB")

    game = Image.open(PORTRAIT).convert("RGBA")
    left, top, width, height = (round(part * game.width) for part in FRAME)
    game = game.crop((left, top, left + width, top + height))
    game = game.resize(drawn.size, Image.Resampling.LANCZOS)
    key = Image.new("RGBA", drawn.size, (255, 0, 255, 255))
    expected = Image.alpha_composite(key, game).convert("RGB")
    hero = [hero_pixels(image) for image in (drawn, expected)]
    shared = ImageChops.logical_and(*hero).convert("L")
    either = ImageChops.logical_or(*hero).convert("L")
    overlap = ImageStat.Stat(shared).sum[0] / ImageStat.Stat(either).sum[0]
    difference = ImageStat.Stat(ImageChops.difference(drawn, expected), mask=shared)
    # The render trial measured 0.97 and a difference of 4 at the portrait's own size.
    assert overlap > 0.94
    assert max(difference.mean) < 10

    # Once the reader allows motion, the hero plays the menu's idle; asked for reduced motion
    # again, it holds still.
    await page.emulate_media(reduced_motion="no-preference")
    moving = [await frame(page) for _ in range(2)]
    await page.emulate_media(reduced_motion="reduce")
    held = [await frame(page) for _ in range(2)]
    assert moving[0] != moving[1] and held[0] == held[1]


async def frame(page: Page) -> bytes:
    """Return what a page's model shows after a moment of its life, as a PNG picture."""
    await page.wait_for_timeout(400)
    return await page.locator("canvas").screenshot()


def hero_pixels(picture: Image.Image) -> Image.Image:
    """Return the pixels of a hero on the key colour: those far from that colour."""
    distance = ImageChops.difference(picture, Image.new("RGB", picture.size, (255, 0, 255)))
    return distance.convert("L").point(lambda value: 255 if value > 40 else 0).convert("1")
