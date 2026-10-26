import httpx2
import pytest
from identity import IdentityProvider, form_token, sign_in
from support import FESL, game, log_in_player

from openfesl.core.games import Games, Http, load_games
from openfesl.server import app

pytestmark = pytest.mark.anyio
KEEPER = {"sub": "keeper-6", "preferred_username": "invented-keeper", "groups": ["fesl-admins"]}
WARDEN = {"sub": "warden-6", "preferred_username": "invented-warden", "groups": ["fesl-moderators"]}


async def test_admins_add_disable_and_enable_product_keys_that_moderators_only_see(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        admin_provider.user = KEEPER
        page = (await sign_in(client, base, next="/admin/keys")).text
        token = form_token(page)
        assert "ACTIVATE-VETERAN" in page and "6 of 6 here" in page and "Keys: 6" in page

        # New codes are unique in any case and keep to the characters the game's prompt takes.
        added = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "LAN-2026"})
        assert "Added LAN-2026." in (await client.get(base + added.headers["location"])).text
        taken = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "lan-2026"})
        assert taken.status_code == 400 and "A key with this code exists already" in taken.text
        wrong = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "a b"})
        assert wrong.status_code == 400

        # Disabling asks first; the game then refuses the key and the guide leaves it out.
        disable = f"{base}/admin/keys/ACTIVATE-VETERAN/disable"
        asked = await client.post(disable, data={"csrf": token})
        question = (await client.get(base + asked.headers["location"])).text
        assert "Disable this key?" in question and "BF3:PC:ADDSVETRANK" in question
        await client.post(disable, data={"csrf": token, "confirmed": "1"})
        disabled = (await client.get(f"{base}/admin/keys", params={"state": "disabled"})).text
        assert "Disabled ACTIVATE-VETERAN." in disabled and "· invented-keeper" in disabled
        assert "ACTIVATE-VETERAN" not in (await client.get(f"{base}/bfbc2/play")).text
        redeem = [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-VETERAN")]
        async with game(server_url) as ann:
            await log_in_player(ann, FESL, "ann@example.test")
            assert (await ann.request(FESL, "acct", redeem))["errorCode"] == "181"
            await client.post(f"{base}/admin/keys/ACTIVATE-VETERAN/enable", data={"csrf": token})
            assert "errorCode" not in await ann.request(FESL, "acct", redeem)

        again = await client.post(f"{base}/admin/keys", data={"csrf": token, "starting": "1"})
        assert (
            "Every starting key is here already."
            in (await client.get(base + again.headers["location"])).text
        )

    async with httpx2.AsyncClient() as client:
        admin_provider.user = WARDEN
        seen = (await sign_in(client, base, next="/admin/keys")).text
        assert "LAN-2026" in seen and "Add a key" not in seen
        assert "Product keys are added, edited, enabled and disabled with the admin role." in seen
        refused = await client.post(
            f"{base}/admin/keys/LAN-2026/disable", data={"csrf": form_token(seen)}
        )
        assert refused.status_code == 403


async def test_admins_edit_a_keys_grants_labels_and_limits_which_the_game_and_guide_follow(
    admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        page = (await sign_in(client, base, next="/admin/keys")).text
        token = form_token(page)
        await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "LAN-2026"})
        edit = f"{base}/admin/keys/LAN-2026"
        assert "Edit key" in (await client.get(edit)).text

        # A grant without a tag saves nothing and says which one it is.
        wrong = {"csrf": token, "tag": [""], "group": ["Invented"], "product": [""]}
        refused = await client.post(edit, data=wrong)
        assert refused.status_code == 400 and "grant 1 has no tag" in refused.text

        saved = {
            "csrf": token,
            "tag": ["INVENTED:LAN", ""],
            "group": ["Invented", ""],
            "product": ["", ""],
            "label.en": "LAN party",
            "max_uses": "1",
            "once": "1",
        }
        done = await client.post(edit, data=saved)
        listed = (await client.get(base + done.headers["location"])).text
        assert "Saved LAN-2026." in listed and "LAN party" in listed
        assert "0 of 1 accounts · once per account" in listed
        guide = (await client.get(f"{base}/bfbc2/play")).text
        assert "LAN-2026" in guide and "LAN party" in guide
        # A language without a label shows the code alone.
        polish = (await client.get(f"{base}/pl/bfbc2/play")).text
        assert "LAN-2026" in polish and "LAN party" not in polish

        redeem = [("TXN", "NuEntitleUser"), ("key", "lan-2026")]
        async with game(server_url) as ann, game(server_url) as bob:
            await log_in_player(ann, FESL, "ann@example.test")
            await log_in_player(bob, FESL, "bob@example.test")
            assert "errorCode" not in await ann.request(FESL, "acct", redeem)
            assert (await ann.request(FESL, "acct", redeem))["errorCode"] == "182"
            assert (await bob.request(FESL, "acct", redeem))["errorCode"] == "180"

        # Removing the grant takes it from later entries only.
        emptied = {"csrf": token, "tag": ["INVENTED:LAN", ""], "group": ["", ""]}
        emptied |= {"product": ["", ""], "remove": "1", "label.en": "LAN party"}
        await client.post(edit, data=emptied)
        assert "It grants nothing yet." in (await client.get(f"{base}/admin/keys")).text


@pytest.fixture
def several_games(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bad Company 2 and an invented game without keys; the server starts with both.
    def load() -> Games:
        bfbc2 = load_games().by_id("bfbc2")
        assert bfbc2 is not None
        other = {"id": "invented", "name": "Invented Game", "namespace": "invented"}
        return Games(
            [bfbc2, bfbc2.model_copy(update={**other, "clients": {}, "http": Http(), "keys": []})]
        )

    monkeypatch.setattr(app, "load_games", load)


async def test_a_key_needs_its_game_chosen_when_the_server_serves_several(
    several_games: None, admin_provider: IdentityProvider, server_url: str
) -> None:
    admin_provider.user = KEEPER
    base = server_url.replace("ws://", "http://").rstrip("/")
    async with httpx2.AsyncClient() as client:
        token = form_token((await sign_in(client, base, next="/admin/keys")).text)
        unchosen = await client.post(f"{base}/admin/keys", data={"csrf": token, "code": "LAN-2026"})
        chosen = {"csrf": token, "code": "LAN-2026", "game": "invented"}
        added = await client.post(f"{base}/admin/keys", data=chosen)

    assert unchosen.status_code == 400 and "Choose the game the key is for." in unchosen.text
    assert added.status_code == 303
