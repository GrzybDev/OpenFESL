import re

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in
from identity import form_token
from support import FESL, game, login

pytestmark = pytest.mark.anyio


async def test_an_account_behind_on_the_terms_accepts_them_on_the_site_and_games_log_it_in(
    server_url: str, database_url: str, base: str
) -> None:
    await execute(
        database_url,
        "UPDATE account SET terms_version = 'older', display_name = 'Invented_Ann'"
        " WHERE nuid = 'ann@example.test'",
    )
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        refused = await player.request(FESL, "acct", login("ann@example.test", PASSWORD))
        assert refused["errorCode"] == "260"

    async with httpx2.AsyncClient() as client:
        # The terms come first after signing in, and lead on to the page that was to open.
        signed_in = await post_sign_in(client, base, "ann@example.test", PASSWORD, next="/terms")
        assert signed_in.headers["location"] == "/account/terms?next=%2Fterms"
        page = await client.get(f"{base}/account/terms?next=/terms")
        assert "The OpenFESL terms have a new version." in page.text
        assert "OpenFESL is an unofficial, community-run replacement" in page.text
        assert (
            "The OpenFESL terms have a new version." in (await client.get(f"{base}/account")).text
        )

        version = re.search(r'name="version" value="([^"]+)"', page.text)
        assert version
        form = {"csrf": form_token(page.text), "version": version[1], "next": "/terms"}
        stale = await client.post(f"{base}/account/terms", data={**form, "version": "older"})
        assert "The terms changed again meanwhile" in stale.text
        accepted = await client.post(f"{base}/account/terms", data=form)
        assert accepted.headers["location"] == "/terms"
        assert "Terms accepted." in (await client.get(f"{base}/terms")).text
        assert "have a new version" not in (await client.get(f"{base}/account")).text
        assert (await client.get(f"{base}/account/terms")).headers["location"] == "/account"

    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        answer = await player.request(FESL, "acct", login("ann@example.test", PASSWORD))
        assert answer.get("errorCode") != "260"
