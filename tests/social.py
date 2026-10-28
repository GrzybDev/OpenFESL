"""Helpers of the tests of what Battlefield Heroes' games learn of other accounts through Magma."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from xml.etree import ElementTree

from accounts import execute
from heroes import MAGMA, create_hero, start
from support import fetch, game

ANN = "ann@example.test"
BOB = "bob@example.test"

type Player = tuple[dict[str, str], int]


async def name_accounts(base: str, database_url: str, *addresses: str) -> None:
    """Create a hero for each account on the site and give the account a display name.

    The display names are Invented_ and the address before the @, capitalised.
    """
    for address in addresses:
        await create_hero(base, address, f"{address.split('@')[0]}_hero")
    await execute(
        database_url,
        "UPDATE account SET display_name = 'Invented_' || initcap(split_part(nuid, '@', 1))"
        " WHERE nuid = ANY(:addresses)",
        addresses=list(addresses),
    )


@asynccontextmanager
async def playing(server_url: str, *addresses: str) -> AsyncIterator[list[Player]]:
    """Start the game of each account through the launcher, logged in as its hero.

    Yields:
        Each game's magma cookie and account number, while the games stay logged in.
    """
    async with AsyncExitStack() as stack:
        started = []
        for address in addresses:
            process = await stack.enter_async_context(game(server_url))
            cookie, account_id, _ = await start(server_url, process, address)
            started.append((cookie, account_id))
        yield started


async def post(
    server_url: str, player: Player, path: str, body: bytes = b""
) -> ElementTree.Element:
    """Post a Magma request as the player's game does, returning the answer's root."""
    _, _, answer = await fetch(server_url, path, MAGMA, player[0], body)
    return ElementTree.fromstring(answer)


async def roster(server_url: str, player: Player) -> list[dict[str, str]]:
    """Return the player's relationships as its game reads its roster, each child by name."""
    path = f"/relationships/roster/nucleus:{player[1]}"
    _, _, body = await fetch(server_url, path, MAGMA, player[0])
    answer = ElementTree.fromstring(body)
    assert answer.tag == "roster"
    return [entry(relationship) for relationship in answer]


def entry(relationship: ElementTree.Element) -> dict[str, str]:
    """Return a relationship's id and children by name."""
    return {
        "id": relationship.get("id", ""),
        **{child.tag: child.text or "" for child in relationship},
    }


def refusal(answer: ElementTree.Element) -> tuple[str | None, str | None]:
    """Return the code and cause of an error document."""
    failure = answer.find("failure")
    assert answer.tag == "error" and failure is not None
    return answer.get("code"), failure.get("cause")
