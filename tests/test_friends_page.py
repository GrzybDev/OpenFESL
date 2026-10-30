import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from identity import form_token
from social import ANN, BOB, name_accounts
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import visible

from openfesl.core.relationships import Party
from openfesl.storage.relationships import SqlRelationshipStore

pytestmark = pytest.mark.anyio

FRIENDS = "/bfheroes/friends"
SEARCH = "/bfheroes/search"


async def change(client: httpx2.AsyncClient, base: str, action: str, **member: str) -> str:
    """Send one of the friends page's forms and return the page that follows."""
    page = await client.get(f"{base}{FRIENDS}")
    form = {"csrf": form_token(page.text), "action": action, "name": "Invented", **member}
    await client.post(f"{base}{FRIENDS}", data=form)
    return visible((await client.get(f"{base}{FRIENDS}")).content)


async def test_a_request_becomes_a_friendship_that_either_side_ends_and_a_vip_list_can_be_left(
    base: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    ann = str(await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=ANN))
    bob = str(await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=BOB))
    # A dedicated server put ann on its VIP list in the game.
    engine = create_async_engine(database_url)
    store = SqlRelationshipStore(async_sessionmaker(engine))
    await store.increase("bfheroes", "bvip", Party(server="Harbour Lights"), Party(int(ann)), 10)
    await engine.dispose()
    async with httpx2.AsyncClient() as anns, httpx2.AsyncClient() as bobs:
        signed_out = visible((await anns.get(f"{base}{FRIENDS}")).content)
        await post_sign_in(anns, base, ANN, PASSWORD)
        await post_sign_in(bobs, base, BOB, PASSWORD)
        asked = await change(bobs, base, "ask", account=ann)
        twice = await change(bobs, base, "ask", account=ann)
        requested = visible((await anns.get(f"{base}{FRIENDS}")).content)
        declined = await change(anns, base, "decline", account=bob)
        withdrawn = visible((await bobs.get(f"{base}{FRIENDS}")).content)
        await change(bobs, base, "ask", account=ann)
        befriended = await change(anns, base, "accept", account=bob)
        # A default hero that the admin took out of play is not shown to other accounts.
        await execute(database_url, "UPDATE persona SET state = 'disabled' WHERE name = 'bob_hero'")
        hidden = visible((await anns.get(f"{base}{FRIENDS}")).content)
        removal = visible((await anns.get(f"{base}{FRIENDS}/{bob}/remove")).content)
        removed = await change(anns, base, "remove", account=bob)
        left = await change(anns, base, "leave", server="Harbour Lights")

    assert "Sign in to see friends" in signed_out
    assert "Friend request sent to Invented." in asked
    assert "Request sent on " in asked and "Waiting Cancel" in asked
    assert "No request was sent: the request to Invented waits already." in twice
    assert "Invented_Bob Default hero bob_hero · National Army Commando, level 1" in requested
    assert "Sent a friend request on " in requested and "Accept" in requested
    assert "VIP on servers" in requested and "Harbour Lights VIP since " in requested
    # A declined request is gone on both sides, and its sender is not told.
    assert "Requests" not in declined and "Requests" not in withdrawn
    assert "declined" not in withdrawn
    assert "Invented is now a friend." in befriended
    assert "Friends · 1 Invented_Bob" in befriended and "Offline" in befriended
    assert "bob_hero" in befriended and "Invented_Bob" in hidden and "bob_hero" not in hidden
    assert "Invented_Ann and Invented_Bob stop being friends on both sides" in removal
    assert "Friends · 0 No friends yet" in removed
    assert "VIP on servers" not in left


async def test_the_player_search_finds_heroes_and_players_and_asks_one_from_its_results(
    base: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    bob = str(await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=BOB))
    async with httpx2.AsyncClient() as client:
        public = visible((await client.get(f"{base}{SEARCH}", params={"q": "bob"})).content)
        short = visible((await client.get(f"{base}{SEARCH}", params={"q": "b"})).content)
        await post_sign_in(client, base, ANN, PASSWORD)
        found = await client.get(f"{base}{SEARCH}", params={"q": "inv"})
        back = f"{SEARCH}?q=inv"
        form = {"csrf": form_token(found.text), "action": "ask", "account": bob, "next": back}
        asked = await client.post(f"{base}{FRIENDS}", data=form | {"name": "Invented_Bob"})
        after = visible((await client.get(f"{base}{back}")).content)

    # Anyone may search; only a signed-in account sends requests. The start that matched is
    # marked, which the page's text shows apart.
    assert "1 hero and 0 players start with “bob”." in public
    assert "_hero National Army Commando · player Invented_Bob level 1" in public
    assert "Add as friend" not in public
    assert "Type 2 or more characters" in short
    shown = visible(found.content)
    assert "ented_Ann 1 hero · default ann_hero This account" in shown
    assert "ented_Bob 1 hero · default bob_hero Add as friend" in shown
    assert '<mark class="search-match">Inv</mark>ented_Bob' in found.text
    assert asked.headers["location"] == back
    assert "Friend request sent to Invented_Bob." in after
    assert "ented_Bob 1 hero · default bob_hero Request sent" in after


async def test_a_friend_action_from_a_page_out_of_date_changes_nothing(
    base: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    ann = str(await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=ANN))
    bob = str(await select_value(database_url, "SELECT id FROM account WHERE nuid = :a", a=BOB))
    async with httpx2.AsyncClient() as anns, httpx2.AsyncClient() as bobs:
        await post_sign_in(anns, base, ANN, PASSWORD)
        await post_sign_in(bobs, base, BOB, PASSWORD)
        await change(bobs, base, "ask", account=ann)
        await change(bobs, base, "cancel", account=ann)
        # Accept and Cancel from pages shown before the other side changed the request.
        accepted = await change(anns, base, "accept", account=bob)
        not_asked = visible((await bobs.get(f"{base}{FRIENDS}")).content)
        await change(bobs, base, "ask", account=ann)
        await change(anns, base, "accept", account=bob)
        cancelled = await change(bobs, base, "cancel", account=ann)
        await change(anns, base, "remove", account=bob)
        await change(bobs, base, "ask", account=ann)
        # Asking an account that asked already accepts its request.
        asked = await change(anns, base, "ask", account=bob)

    assert "Nothing changed: the page was out of date." in accepted
    assert "Requests" not in accepted and "Requests" not in not_asked
    assert "Nothing changed: the page was out of date." in cancelled
    assert "Friends · 1 Invented_Ann" in cancelled
    assert "Invented is now a friend." in asked and "Friends · 1 Invented_Bob" in asked


async def test_the_player_search_counts_all_it_finds_and_pages_heroes_by_level(
    base: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN, BOB)
    # 45 more heroes of ann's whose names start as bob's display name does, the last of them at
    # level 30; bob's own hero is taken out of play by the admin.
    for number in range(45):
        await execute(
            database_url,
            "INSERT INTO persona (account_id, namespace, name)"
            " SELECT account_id, namespace, :name FROM persona WHERE name = 'ann_hero'",
            name=f"Invented_B{number:02}",
        )
    await execute(
        database_url,
        "INSERT INTO stat SELECT partition, owner_type, p.id, period, period_start, 'level', 30,"
        " '' FROM stat, persona p WHERE owner_id = (SELECT id FROM persona WHERE name ="
        " 'ann_hero') AND key = 'level' AND p.name = 'Invented_B44'",
    )
    await execute(database_url, "UPDATE persona SET state = 'disabled' WHERE name = 'bob_hero'")
    async with httpx2.AsyncClient() as client:
        first = await client.get(f"{base}{SEARCH}", params={"q": "invented_b"})
        second = visible(
            (await client.get(f"{base}{SEARCH}", params={"q": "invented_b", "heroes": 2})).content
        )

    shown = visible(first.content)
    assert "45 heroes and 1 player start with “invented_b”." in shown
    assert "Heroes · 45 1\u201340 of 45, by level" in shown
    assert "Players · 1 1\u20131 of 1, by name" in shown
    # The highest level first; the admin's disabling hides bob's hero also as his default.
    assert first.text.index("</mark>44") < first.text.index("</mark>00")
    assert "</mark>ob" in first.text and "No hero yet" in shown and "bob_hero" not in shown
    assert 'href="/bfheroes/search?q=invented_b&amp;heroes=2"' in first.text
    assert "Heroes · 45 41\u201345 of 45, by level" in second
