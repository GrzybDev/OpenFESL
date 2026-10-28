import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, register
from identity import form_token
from support import FESL, game, log_in_persona

pytestmark = pytest.mark.anyio


async def test_an_account_made_in_a_game_chooses_its_display_name_once(
    server_url: str, database_url: str
) -> None:
    async with game(server_url) as player:
        await log_in_persona(player, FESL, "kim@example.test", "Invented_Kim")
        await player.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", "Kim_Two")])
    async with game(server_url) as other:
        await other.hello(FESL, "bfbc2-pc")
        await register(other, "lee@example.test")
    await execute(
        database_url,
        "UPDATE account SET display_name = 'INVENTED_KIM' WHERE nuid = 'lee@example.test'",
    )
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        # Without a sign-in, the page asks for one that returns to it.
        asked = await client.get(f"{base}/account/display-name")
        assert asked.headers["location"] == "/sign-in?next=%2Faccount%2Fdisplay-name"
        sign_in = await client.get(f"{base}{asked.headers['location']}")
        assert "The display name page needs a signed-in account." in sign_in.text

        signed_in = await post_sign_in(client, base, "kim@example.test", PASSWORD, next="/terms")
        assert signed_in.headers["location"] == "/account/display-name?next=%2Fterms"
        page = await client.get(f"{base}{signed_in.headers['location']}")
        assert "This account was made in Battlefield: Bad Company 2" in page.text
        # Another account uses the first name it plays under, ignoring case, so the next fits.
        assert 'value="Kim_Two"' in page.text

        form = {"csrf": form_token(page.text), "display_name": "invented_kim", "next": "/terms"}
        taken = await client.post(f"{base}/account/display-name", data=form)
        assert taken.status_code == 400
        assert "Another account uses invented_kim, ignoring case." in taken.text
        saved = await client.post(
            f"{base}/account/display-name", data={**form, "display_name": "Kim_Two"}
        )
        assert saved.headers["location"] == "/terms"
        assert "Display name saved: Kim_Two." in (await client.get(f"{base}/terms")).text
        # Asked once: the name stays, and the page leads on.
        again = await client.get(f"{base}/account/display-name")
        assert again.headers["location"] == "/account"
