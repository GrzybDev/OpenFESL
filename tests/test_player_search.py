from xml.etree import ElementTree

import pytest
from accounts import execute, register
from heroes import MAGMA, create_hero
from social import ANN, BOB, Player, name_accounts, playing, refusal
from support import FESL, fetch, game

pytestmark = pytest.mark.anyio

CAT = "cat@example.test"


async def search(server_url: str, player: Player, text: str) -> bytes:
    """Return the answer to a persona search as the player's game sends it."""
    _, _, body = await fetch(server_url, f"/nucleus/personas/{text}", MAGMA, player[0])
    return body


def found(body: bytes) -> list[tuple[str, str]]:
    """Return the names a persona search found, each with its namespace."""
    answer = ElementTree.fromstring(body)
    return [
        (persona.findtext("displayName") or "", persona.findtext("namespaceName") or "")
        for persona in answer.iter("persona")
    ]


async def test_a_search_finds_display_names_and_heroes_of_accounts_in_use_from_their_start(
    base: str, server_url: str, database_url: str
) -> None:
    async with game(server_url) as registering:
        await registering.hello(FESL, "bfbc2-pc")
        await register(registering, CAT)
    await name_accounts(base, database_url, ANN, BOB, CAT)
    for name in ("catxhero", "cat_hidden", "cat_removed"):
        await create_hero(base, CAT, name)
    # A hero the admin disabled and one its player removed, and a banned account.
    await execute(database_url, "UPDATE persona SET state = 'disabled' WHERE name = 'cat_hidden'")
    await execute(database_url, "UPDATE persona SET disabled_at = now() WHERE name = 'cat_removed'")
    await execute(database_url, "UPDATE account SET state = 'banned' WHERE nuid = :bob", bob=BOB)
    async with playing(server_url, ANN, CAT) as (ann, cat):
        displayed = await search(server_url, ann, "invented")
        answers = [
            found(await search(server_url, ann, text)) for text in ("CAT", "*_hero", "inv*c")
        ]

    # Display names in the website's namespace, with their accounts' numbers.
    named = (
        f"<personas><persona><userId>{ann[1]}</userId><displayName>Invented_Ann</displayName>"
        "<namespaceName>bfheroes_web</namespaceName></persona>"
        f"<persona><userId>{cat[1]}</userId><displayName>Invented_Cat</displayName>"
        "<namespaceName>bfheroes_web</namespaceName></persona></personas>"
    )
    assert displayed == named.encode()
    # Heroes in the game's namespace; an underscore is no wildcard.
    assert answers == [
        [("cat_hero", "bfwest"), ("catxhero", "bfwest")],
        [("ann_hero", "bfwest"), ("cat_hero", "bfwest")],
        [("Invented_Cat", "bfheroes_web")],
    ]


async def test_a_search_needs_two_characters_and_answers_at_most_fifty_names(
    base: str, server_url: str, database_url: str
) -> None:
    await name_accounts(base, database_url, ANN)
    await execute(
        database_url,
        "INSERT INTO account (nuid, country, server, display_name)"
        " SELECT 'player' || n || '@example.test', 'PL', false, 'Invented_' || n"
        " FROM generate_series(1, 60) AS n",
    )
    async with playing(server_url, ANN) as [ann]:
        refusals = [
            refusal(ElementTree.fromstring(await search(server_url, ann, text)))
            for text in ("a", "*a**")
        ]
        many = found(await search(server_url, ann, "invented_"))
        _, _, anonymous = await fetch(server_url, "/nucleus/personas/invented", MAGMA)

    assert refusals == [("VALIDATION_FAILED", "SEARCH_TOO_SHORT")] * 2
    assert len(many) == 50
    assert refusal(ElementTree.fromstring(anonymous)) == ("NOT_LOGGED_IN", "NO_SESSION")
