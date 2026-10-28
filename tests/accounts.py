"""Helpers of the tests of the site's accounts: accounts made in a game and the ways to sign in."""

import re
from urllib.parse import urlsplit

import httpx2
from identity import form_token
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import FESL, Game, registration

PASSWORD = "secret-pass"


async def register(player: Game, address: str) -> None:
    """Register an account in Bad Company 2 with the current terms."""
    terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
    answer = await player.request(FESL, "acct", registration(address, PASSWORD, terms))
    assert "errorCode" not in answer


async def post_sign_in(
    client: httpx2.AsyncClient, base: str, address: str, password: str, **fields: str
) -> httpx2.Response:
    """Send the sign-in form with an address and a game password."""
    page = await client.get(f"{base}/sign-in")
    form = {"csrf": form_token(page.text), "email": address, "password": password, **fields}
    return await client.post(f"{base}/sign-in", data=form)


async def sign_in_through(
    client: httpx2.AsyncClient, base: str, service: str, next: str = ""
) -> httpx2.Response:
    """Sign in through a service as its next account, and return the callback's answer."""
    page = await client.get(f"{base}/sign-in")
    form = {"csrf": form_token(page.text), "service": service, "next": next}
    started = await client.post(f"{base}/sign-in/service", data=form)
    return await returned(client, base, started)


async def returned(
    client: httpx2.AsyncClient, base: str, started: httpx2.Response
) -> httpx2.Response:
    """Follow a sign-in from the redirect to the service back to the site's callback.

    The service sends the browser to the public address, which the tests' server is not at.
    """
    assert started.status_code == 302, started.text
    back = urlsplit((await client.get(started.headers["location"])).headers["location"])
    return await client.get(f"{base}{back.path}?{back.query}")


async def post_registration(
    client: httpx2.AsyncClient, base: str, path: str = "/register", **fields: str
) -> httpx2.Response:
    """Send a registration form of the site, filled in as a valid one unless a field says else."""
    page = await client.get(f"{base}{path}")
    # Where the page's form sends it, so the form is the one a browser sends.
    action = re.search(r'<form[^>]*action="(/register[^"]*)"', page.text)
    assert action, "the page has no registration form"
    form = {
        "csrf": form_token(page.text),
        "email": "cat@example.test",
        "password": PASSWORD,
        "display_name": "Invented_Cat",
        "country": "PL",
        "birth_day": "28",
        "birth_month": "5",
        "birth_year": "1990",
        "terms": "1",
        **fields,
    }
    return await client.post(f"{base}{action[1]}", data=form)


async def confirm_address(client: httpx2.AsyncClient, base: str, link: str) -> httpx2.Response:
    """Open a mailed confirmation link and press the button of the page it opens."""
    page = await client.get(f"{base}{link}")
    return await client.post(f"{base}{link}", data={"csrf": form_token(page.text)})


async def sign_out(client: httpx2.AsyncClient, base: str) -> httpx2.Response:
    """Sign out through the account menu."""
    page = await client.get(f"{base}/terms")
    return await client.post(f"{base}/sign-out", data={"csrf": form_token(page.text)})


async def execute(database_url: str, statement: str, **values: object) -> None:
    """Change the database directly, for a state that a later step of the site creates."""
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text(statement), values)
    await engine.dispose()
