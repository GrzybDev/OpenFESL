"""Helpers of the tests of heroes, which games such as Battlefield Heroes make on their site."""

import httpx2
from accounts import PASSWORD, post_sign_in
from identity import form_token

CREATOR = "/bfheroes/user/createHero"


def look(**chosen: str) -> dict[str, str]:
    """Return the creator's fields for a National Army commando, with any of them changed."""
    return {
        "faction": "national",
        "class": "commando",
        "skin_tone": "8",
        "hair_colour": "2",
        "hair_style": "123",
        "facial_hair": "129",
        **chosen,
    }


async def create(
    client: httpx2.AsyncClient, base: str, name: str, **chosen: str
) -> httpx2.Response:
    """Send the creator's form of a signed-in client, as its naming step sends it."""
    page = await client.get(f"{base}{CREATOR}")
    form = {"csrf": form_token(page.text), "name": name, **look(**chosen)}
    return await client.post(f"{base}{CREATOR}", data=form)


async def create_hero(base: str, address: str, name: str, **chosen: str) -> int:
    """Sign an account in on the site and create a hero with it; return the hero's number."""
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, address, PASSWORD)
        created = await create(client, base, name, **chosen)
    assert created.status_code == 303, created.text
    return int(created.headers["location"].split("/")[3])
