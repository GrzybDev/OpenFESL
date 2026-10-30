import html
import re
from pathlib import Path

import anyio
import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from heroes import create_hero, pictures, start
from identity import IdentityProvider, form_token, sign_in
from playwright.async_api import Browser, Route
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from support import cli, game, png_image
from test_admin_catalogues import KEEPER, PAGE, WARDEN, importing
from test_catalogue_import import extracted
from test_creator_stage import FIGURE, ROYAL
from test_mugshots import upload

from openfesl.core.admin import AdminFeed
from openfesl.core.game_art import ArtViewers, GameArt
from openfesl.core.games import Game
from openfesl.storage.admin import SqlAdminLog
from openfesl.storage.game_art import SqlArtStore
from openfesl.web.pages import built_assets

pytestmark = pytest.mark.anyio
ANN = "ann@example.test"
CHOICE = f"{PAGE}/bfheroes/art"


async def choose(client: httpx2.AsyncClient, base: str, shown: str) -> str:
    """Choose who sees Battlefield Heroes' art as the form without scripts does; return the page."""
    page = (await client.get(base + PAGE)).text
    chosen = await client.post(base + CHOICE, data={"csrf": form_token(page), "shown": shown})
    assert chosen.status_code == 303, chosen.text
    return html.unescape((await client.get(base + PAGE)).text)


def checked(page: str) -> list[str]:
    """Return the values of a page's checked art tiles."""
    return re.findall(r'name="shown"\s+value="(\w+)"\s+checked', page)


@pytest.mark.usefixtures("prompt_actions")
async def test_who_sees_a_games_art_changes_its_pages_at_once_and_is_logged(
    files: Path,
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await cli(capsys, "catalogue", "import", str(await extracted(tmp_path, capsys)))
    hero = await create_hero(base, ANN, "Brassbuckle", faction="royal")
    # A display name makes the account and its heroes' profiles public.
    await execute(
        database_url, "UPDATE account SET display_name = 'Quillfuse' WHERE nuid = :ann", ann=ANN
    )
    async with game(server_url) as player:
        cookie, _, _ = await start(server_url, player, ANN)
        await upload(server_url, cookie, hero, png_image(64, 64))
    portrait = f'src="/mugshots/bfheroes/{hero}/1.png"'

    async with (
        httpx2.AsyncClient() as admin,
        httpx2.AsyncClient() as ann,
        httpx2.AsyncClient() as visitor,
    ):
        admin_provider.user = KEEPER
        await sign_in(admin, base, next=PAGE)
        await post_sign_in(ann, base, ANN, PASSWORD)
        with anyio.fail_after(5):
            while (await ann.get(base + FIGURE, params=ROYAL)).status_code != 200:
                await anyio.sleep(0.05)

        async def seen() -> list[bool]:
            # Whether the creator's model reaches a visitor, the player and an admin, whether
            # the player's hero card shows the game's portrait, and whether the hero's public
            # profile shows it to the visitor.
            models = [
                (await client.get(base + FIGURE, params=ROYAL)).status_code == 200
                for client in (visitor, ann, admin)
            ]
            card = (await ann.get(f"{base}/bfheroes/heroes")).text
            profile = (await visitor.get(f"{base}/bfheroes/heroes/{hero}")).text
            return [*models, portrait in card, portrait in profile]

        # By default signed-in players, admins and the game see the art; a visitor gets
        # placeholders.
        page = html.unescape((await admin.get(base + PAGE)).text)
        assert checked(page) == ["signed_in"] and "Default setting." in page
        assert await seen() == [False, True, True, True, False]

        page = await choose(admin, base, "everyone")
        assert "Battlefield Heroes shows the game's art to everyone." in page
        assert checked(page) == ["everyone"]
        assert re.search(r"Chosen \d+ \w+ \d{4}, \d\d:\d\d UTC · invented-keeper\.", page)
        assert await seen() == [True, True, True, True, True]

        page = await choose(admin, base, "nobody")
        assert "Battlefield Heroes shows placeholders instead of the game's art." in page
        assert await seen() == [False, False, False, False, False]
        hidden = (await ann.get(f"{base}/bfheroes/heroes")).text
        assert pictures(hidden) == ["reticle", "reticle"]

        # Changing back needs no import, as every file stayed kept.
        await choose(admin, base, "signed_in")
        assert await seen() == [False, True, True, True, False]
        log = html.unescape((await admin.get(f"{base}/admin/audit")).text)

    for action in (
        "Showed the game's art to everyone",
        "Replaced the game's art with placeholders",
        "Showed the game's art to signed-in players",
    ):
        assert action in log
    assert log.count("Icons: 5 · hero models: 4 · textures: 2 · portraits: 1") == 3

    async with httpx2.AsyncClient() as moderator:
        admin_provider.user = WARDEN
        seen_page = html.unescape((await sign_in(moderator, base, next=PAGE)).text)
        assert "Choosing who sees the game's art needs the admin role." in seen_page
        assert re.search(r'<fieldset class="catalogue-art-choice"\s+disabled>', seen_page)
        refused = await moderator.post(
            base + CHOICE, data={"csrf": form_token(seen_page), "shown": "nobody"}
        )
    assert refused.status_code == 403
    assert "who sees each game's art is chosen, with the admin role." in html.unescape(refused.text)


async def test_a_choice_of_who_sees_the_art_reaches_every_server_through_the_log(
    catalogue_sessions: async_sessionmaker[AsyncSession],
    bfheroes: Game,
) -> None:
    choosing, following = (GameArt(SqlArtStore(catalogue_sessions)) for _ in "ab")
    feed = AdminFeed(SqlAdminLog(catalogue_sessions))
    following.follow(feed)
    await feed.start()
    await following.load()

    await choosing.choose(bfheroes, ArtViewers.NOBODY, {"icons": 0}, None)
    assert choosing.viewers(bfheroes) is ArtViewers.NOBODY
    assert following.viewers(bfheroes) is ArtViewers.SIGNED_IN
    await feed.apply()
    later = GameArt(SqlArtStore(catalogue_sessions))
    await later.load()
    try:
        assert following.viewers(bfheroes) is ArtViewers.NOBODY
        assert later.viewers(bfheroes) is ArtViewers.NOBODY
        assert not later.shown(bfheroes, signed_in=True)
    finally:
        async with catalogue_sessions.begin() as session:
            await session.execute(text("DELETE FROM setting WHERE key LIKE 'art.%'"))


async def test_the_tiles_save_a_choice_once_it_rests_and_say_when_it_failed(
    files: Path, admin_provider: IdentityProvider, server_url: str, edge: Browser
) -> None:
    if not built_assets().app:
        pytest.skip("the web pages' scripts are not built")
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        await sign_in(client, base, next=PAGE)
        page = await importing(edge, base, client)
    section = page.locator("#art-bfheroes")
    posted: list[str] = []
    page.on("request", lambda sent: posted.append(sent.method) if CHOICE in sent.url else None)

    # Arrow keys passing over a tile save nothing; the tile the choice rests on is saved.
    await section.get_by_role("radio", name=re.compile("^To signed-in players")).focus()
    for key in ("ArrowLeft", "ArrowRight", "ArrowRight"):
        await page.keyboard.press(key)
    await page.get_by_text("Battlefield Heroes shows placeholders").wait_for()
    assert len(posted) == 1
    assert await section.locator("[data-art-word]").text_content() == "To nobody"
    assert await section.locator("[data-strip] td[data-shown=game]").count() == 0
    # The saved section keeps the keyboard on the tile it rests on.
    nobody = section.get_by_role("radio", name=re.compile("^To nobody"))
    assert await nobody.evaluate("radio => radio === document.activeElement")

    # A save that fails returns the accent to the saved tile, and its toast stays.
    async def fail(route: Route) -> None:
        await route.abort()

    await page.route(re.compile(r"/art$"), fail)
    await section.get_by_role("radio", name=re.compile("^To signed-in players")).click()
    await page.get_by_text("The change was not saved. Try again.").wait_for()
    assert await section.locator("input[value=nobody]").is_checked()
    await page.wait_for_timeout(4500)
    assert await page.get_by_text("The change was not saved. Try again.").count() == 1


@pytest.mark.usefixtures("prompt_actions")
async def test_a_games_files_and_portraits_reach_only_those_its_art_is_shown_to(
    files: Path,
    admin_provider: IdentityProvider,
    base: str,
    server_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await cli(capsys, "catalogue", "import", str(await extracted(tmp_path, capsys)))
    hero = await create_hero(base, ANN, "Brassbuckle", faction="royal")
    [kept] = (files / "catalogues" / "bfheroes").iterdir()
    folder = f"/catalogues/bfheroes/{kept.name}"
    paths = [
        f"{folder}/icons/item1.webp",
        f"{folder}/models/invented_hat.glb",
        f"{folder}/textures/invented_hair.webp",
        f"/mugshots/bfheroes/{hero}/1.png",
    ]
    public = "public, max-age=31536000, s-maxage=600, immutable"
    private = "private, max-age=31536000, immutable"

    async with (
        game(server_url) as player,
        httpx2.AsyncClient() as admin,
        httpx2.AsyncClient() as ann,
        httpx2.AsyncClient() as visitor,
    ):
        cookie, _, _ = await start(server_url, player, ANN)
        await upload(server_url, cookie, hero, png_image(64, 64))
        admin_provider.user = KEEPER
        await sign_in(admin, base, next=PAGE)
        await post_sign_in(ann, base, ANN, PASSWORD)
        with anyio.fail_after(5):
            while (await ann.get(base + FIGURE, params=ROYAL)).status_code != 200:
                await anyio.sleep(0.05)

        async def answers(client: httpx2.AsyncClient, **headers: str) -> set[tuple[int, str]]:
            # What each of the game's files answers a viewer: its status and how it may be kept.
            found = [await client.get(base + path, headers=headers) for path in paths]
            return {(answer.status_code, answer.headers["cache-control"]) for answer in found}

        # By default a visitor gets nothing that a proxy could keep for players; the players,
        # the admins and the game, through its own session, get the files kept by their own
        # browsers alone.
        assert await answers(visitor) == {(404, "no-store")}
        assert await answers(visitor, Cookie="magma=invented-session") == {(404, "no-store")}
        for viewer, headers in ((ann, {}), (admin, {}), (visitor, cookie)):
            assert await answers(viewer, **headers) == {(200, private)}

        # Everyone gets the files, which shared caches keep only ten minutes.
        await choose(admin, base, "everyone")
        assert await answers(visitor) == {(200, public)}

        # Nobody gets them, admins and the game included, though every file stays kept.
        await choose(admin, base, "nobody")
        for viewer, headers in ((visitor, {}), (ann, {}), (admin, {}), (visitor, cookie)):
            assert await answers(viewer, **headers) == {(404, "no-store")}
        await choose(admin, base, "signed_in")
        assert await answers(ann) == {(200, private)}
