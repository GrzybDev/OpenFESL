import re

import anyio
import httpx2
import pytest
from accounts import execute
from heroes import create_hero
from playwright.async_api import Browser
from store import ROYAL_SOLDIER
from support import cli, visible
from test_profiles import ROYAL_GUNNER, name_players, play

from openfesl.web import boards

pytestmark = pytest.mark.anyio

BOARDS = "/bfheroes/leaderboards"
NATIONAL_COMMANDO = {"faction": "national", "class": "commando"}


@pytest.mark.usefixtures("prompt_actions")
async def test_leaderboards_rank_the_heroes_that_played_two_hours_by_stat_faction_and_period(
    base: str,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await name_players(database_url)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    pip = await create_hero(base, "ann@example.test", "Pip-Lantern", **ROYAL_SOLDIER)
    kettle = await create_hero(base, "bob@example.test", "Kettlebrook", **NATIONAL_COMMANDO)
    # This week's rounds: Pip-Lantern has played an hour, the others two and more.
    await play(database_url, brass, {"rs": 5000, "ct": 7200, "ki": 10, "dt": 5})
    await play(database_url, pip, {"rs": 9000, "ct": 3600})
    await play(database_url, kettle, {"rs": 7000, "ct": 9000, "ki": 30, "dt": 10})
    # Last week's, in the bucket the game reads as one week past.
    await play(database_url, kettle, {"rs": 2500, "ct": 7200}, weeks_ago=1)
    async with httpx2.AsyncClient() as client:
        menu = await client.get(f"{base}{BOARDS}")
        score = await client.get(f"{base}{BOARDS}/score/weekly/0/1")
        ratio = await client.get(f"{base}/pl{BOARDS}/kills-deaths/weekly/0/1")
        royal = await client.get(f"{base}{BOARDS}/royal-score/indefinite/0/1")
        chosen = await client.get(
            f"{base}{BOARDS}", params={"board": "kills-deaths", "heroes": "national", "past": "0"}
        )
        last_week = await client.get(f"{base}{BOARDS}/score/weekly/1/1")
        empty_week = await client.get(f"{base}{BOARDS}/score/weekly/2/1")
        profiles = [await client.get(f"{base}/bfheroes/heroes/{hero}") for hero in (brass, pip)]
        unknown = await client.get(f"{base}/bfheroes/heroes/999999")
        missing = [
            await client.get(f"{base}{BOARDS}/{path}")
            for path in ("invented/weekly/0/1", "score/weekly/4/1", "score/indefinite/1/1")
        ]
        # A page holds one hero here; a page past the last shows the last.
        monkeypatch.setattr(boards, "PAGE_SIZE", 1)
        second = await client.get(f"{base}{BOARDS}/score/weekly/0/2")
        beyond = await client.get(f"{base}{BOARDS}/score/weekly/0/9")
        # Rounds wait for the board's next ranking, an hour later, and a ban whose time has
        # passed changes nothing.
        await play(database_url, brass, {"rs": 9000, "ct": 600})
        await play(database_url, pip, {"ct": 3600})
        ended = "UPDATE account SET state = 'banned', state_until = now() - interval '1 minute'"
        await execute(database_url, f"{ended} WHERE nuid = 'ann@example.test'")
        week = f"{base}{BOARDS}/score/weekly/0/1"
        within_the_hour = await client.get(week)
        # The admin's ban takes the account's heroes off the boards at once, ranking them again.
        await cli(capsys, "accounts", "state", "bob@example.test", "banned")
        with anyio.fail_after(10):
            while "Kettlebrook" in (banned := visible((await client.get(week)).content)):
                await anyio.sleep(0.05)
        # A board of one place lists only the best hero, and only that hero's profile has a rank.
        monkeypatch.setattr(boards, "PAGES", 1)
        beyond_the_board = await client.get(f"{base}/bfheroes/heroes/{pip}")

    assert menu.headers["location"] == f"{BOARDS}/score/weekly/0/1"
    shown = visible(score.content)
    assert "Royal Army 50% National Army 50%" in shown
    assert (
        "1 Kettlebrook Level 1 · Kettle_Inv 7,000 2 Brassbuckle Level 1 · Quill_Invented 5,000"
    ) in shown
    assert "Pip-Lantern" not in shown
    assert (
        "A board lists its best 1,000 heroes that have played 2 hours of rounds in its period."
    ) in shown
    assert re.search(r"Ranked at \d\d:\d\d UTC; rankings are at most an hour old\.", shown)
    assert f'id="hero-{brass}"' in score.text
    polish = visible(ratio.content)
    assert "Zabójstwa i zgony" in polish
    assert "1 Kettlebrook Poziom 1 · Kettle_Inv 3,000 2 Brassbuckle" in polish
    assert "1 Brassbuckle Level 1 · Quill_Invented 5,000" in visible(royal.content)
    assert "Kettlebrook" not in visible(royal.content)
    # A board of one faction has no split.
    assert "Royal Army 100%" not in visible(royal.content)
    assert chosen.headers["location"] == f"{BOARDS}/national-kills-deaths/weekly/0/1"
    assert "1 Kettlebrook Level 1 · Kettle_Inv 2,500" in visible(last_week.content)
    assert "No hero was on this board that week" in visible(empty_week.content)
    assert f'href="{BOARDS}/score/weekly/3/1"' in empty_week.text
    # A hero's profile names its place on the all-time score board, on the board's page.
    assert "Score 5,000 · rank 2" in visible(profiles[0].content)
    assert f'href="{BOARDS}/score/indefinite/0/1#hero-{brass}"' in profiles[0].text
    ranked = r"The rank is the all-time score board's, ranked at \d\d:\d\d UTC\."
    assert re.search(ranked, visible(profiles[0].content))
    assert "Score 9,000 · not ranked yet" in visible(profiles[1].content)
    assert {page.status_code for page in missing} == {404}
    # An unknown hero's page leads to the player search and the leaderboards.
    assert 'href="/bfheroes/search"' in unknown.text and f'href="{BOARDS}"' in unknown.text
    assert "2 Brassbuckle" in visible(second.content) and "Kettlebrook" not in visible(
        second.content
    )
    assert "2 Brassbuckle" in visible(beyond.content)
    assert "1 Kettlebrook Level 1 · Kettle_Inv 7,000" in visible(within_the_hour.content)
    # The banned account's heroes left the boards, and those of the account whose ban ended
    # stay.
    assert "1 Brassbuckle Level 1 · Quill_Invented 14,000" in banned
    assert "Royal Army 100% National Army 0%" in banned
    # Pip-Lantern has played its two hours but is not among the board's best: no rank, as on
    # the 2012 site.
    assert "Score 9,000 Wins" in visible(beyond_the_board.content)


async def test_a_leaderboards_choice_waits_for_its_button_and_its_columns_stay_named_on_phones(
    base: str, database_url: str, edge: Browser
) -> None:
    await name_players(database_url)
    brass = await create_hero(base, "ann@example.test", "Brassbuckle", **ROYAL_GUNNER)
    await play(database_url, brass, {"rs": 5000, "ct": 7200})
    board = f"{base}{BOARDS}/score/indefinite/0/1"
    context = await edge.new_context(locale="en-GB", viewport={"width": 390, "height": 844})
    page = await context.new_page()
    await page.goto(board)
    # The rows are cards on a phone, yet screen readers still name each cell's column.
    columns = await page.get_by_role("columnheader").all_text_contents()
    # Stepping through the boards with the arrow keys stays on this one until it is sent.
    await page.locator("select[name=board]").focus()
    await page.keyboard.press("ArrowDown")
    stepped = page.url
    focused = await page.evaluate("document.activeElement.name")
    await page.get_by_role("button", name="Show").click()
    await page.wait_for_url(f"{base}{BOARDS}/kills-deaths/indefinite/0/1")
    await context.close()

    assert [column.strip() for column in columns] == ["Place", "Hero", "Score"]
    assert (stepped, focused) == (board, "board")
