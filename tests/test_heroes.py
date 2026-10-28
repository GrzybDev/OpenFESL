import asyncio
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import PASSWORD, post_sign_in, select_value
from heroes import CREATOR, create, create_hero
from identity import form_token
from support import FESL, fetch, game, launcher_sign_in, markup, visible

pytestmark = pytest.mark.anyio

MAGMA = "www.battlefieldheroes.com"
CREATION = ("c_team", "c_kit", "c_skc", "c_hrc", "c_ft", "c_hrs", "c_fhrs", "level")


async def test_a_hero_made_on_the_site_is_the_hero_the_game_logs_in_with(
    base: str, server_url: str, database_url: str
) -> None:
    # Battlefield Heroes registers no accounts, so they come from another game; without a hero
    # the launcher has none to log the game in with.
    refused = await launcher_sign_in(server_url, "ann@example.test", PASSWORD)
    assert refused.json() == {"error": "no-hero"}

    async with httpx2.AsyncClient() as client:
        signed_out = visible((await client.get(f"{base}{CREATOR}")).content)
        assert "Sign in to create a hero" in signed_out
        from_game = (await client.get(f"{base}{CREATOR}?from=game")).text
        assert "The creator opens after signing in." in visible(from_game.encode())
        assert "or use the sign-in above with the e-mail address" in visible(from_game.encode())
        assert 'name="faction"' not in from_game
        # A number in another script is no number of the look.
        odd = await client.post(f"{base}{CREATOR}", data={"skin_tone": "²"})
        assert odd.status_code == 400
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = markup((await client.get(f"{base}{CREATOR}?from=game")).content)
        assert "Battlefield Heroes sent this page" in page
        assert 'name="from" value="game"' in page and "0 of 4 used" in page

        checks = [
            (await client.get(f"{base}/bfheroes/heroes/name", params={"name": name})).text
            for name in ("x", "Mossgrove")
        ]
        assert 'data-check="format"' in checks[0] and "Mossgrove is free." in markup(
            checks[1].encode()
        )
        invalid = await create(client, base, "Moss grove")
        assert invalid.status_code == 400 and 'aria-invalid="true"' in invalid.text
        # A form from another site creates nothing, so the name stays free.
        forged = {"csrf": "forged", "name": "Mossgrove"}
        refused = await client.post(f"{base}{CREATOR}", data=forged)
        assert refused.status_code == 400
        shown = visible(refused.content)
        assert "The hero was not created: the form had expired." in shown
        # The page that answers a form still shows the site's bar.
        assert "2,500 Funds 0 Valor Points" in shown

        created = await create(client, base, "Mossgrove", **{"from": "game"})
        assert created.headers["location"].endswith("/created?from=game")
        arrived = await client.get(f"{base}{created.headers['location']}")
        done = markup(arrived.content)
        assert "Mossgrove is the default hero, in slot 1 of 4." in done
        assert "when it starts again, it logs in as Mossgrove" in done
        # The bar shows the default hero from now on.
        assert "Default hero Mossgrove" in visible(arrived.content)
        # The game may have used another account, whose sign-in returns to the creator.
        other = {"csrf": form_token(arrived.text), "next": f"{CREATOR}?from=game"}
        switched = await client.post(f"{base}/sign-out", data=other)
        assert switched.headers["location"] == (
            "/sign-in?next=%2Fbfheroes%2Fuser%2FcreateHero%3Ffrom%3Dgame"
        )

    # The launcher logs the game in with the new hero, which it finds with its choices, its
    # loadout and the bandages the game does not grant itself.
    session = (await launcher_sign_in(server_url, "ann@example.test", PASSWORD)).json()
    assert session["personaName"] == "Mossgrove"
    cookie = {"Cookie": f"magma={session['sessionId']}"}
    _, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    token = ElementTree.fromstring(body).findtext("token") or ""
    async with game(server_url) as player:
        await player.hello(FESL, "bfwest-pc", client_type="client-noreg")
        login = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
        await player.request(FESL, "acct", login)
        personas = await player.request(FESL, "acct", [("TXN", "NuGetPersonas")])
        # Heroes come only from the site, which never deletes them.
        kept_out = [
            (await player.request(FESL, "acct", [("TXN", txn), ("name", name)])).get("errorCode")
            for txn, name in (("NuAddPersona", "Mudlark"), ("NuDisablePersona", "Mossgrove"))
        ]
        playing = await player.request(
            FESL, "acct", [("TXN", "NuLoginPersona"), ("name", "Mossgrove")]
        )
        hero = playing["userId"]
        keys = (*CREATION, "c_apr", "c_eqp", "c_emo")
        asked = [
            ("TXN", "GetStats"),
            ("owner", hero),
            ("ownerType", "1"),
            ("periodId", "0"),
            ("periodPast", "0"),
            *((f"keys.{index}", key) for index, key in enumerate(keys)),
            ("keys.[]", str(len(keys))),
        ]
        stats = await player.request(FESL, "rank", asked)
    assert (personas["personas.[]"], personas["personas.0"]) == ("1", "Mossgrove")
    assert kept_out == ["21", "21"]
    found = {
        stats[f"stats.{index}.key"]: stats.get(f"stats.{index}.text")
        or stats[f"stats.{index}.value"]
        for index in range(int(stats["stats.[]"]))
    }
    assert [float(found[key]) for key in CREATION] == [1, 0, 8, 2, 0, 123, 129, 1]
    assert (found["c_apr"], found["c_eqp"], found["c_emo"]) == (
        "10;981;979;56",
        "3002;3014;2141;3155;2005",
        "5000;5007;5016",
    )
    _, _, body = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    items = [
        (entry.findtext("entitlementTag"), entry.findtext("useCount"))
        for entry in ElementTree.fromstring(body).iter("entitlement")
    ]
    assert items == [("2005-lmct", "10")]
    # Nothing opened the hero's Hero Points; the game's first load opens them empty.
    assert await select_value(database_url, "SELECT count(*) FROM wallet") == 0


async def test_names_ignore_capitals_and_slots_run_out_without_a_hero_made_twice(
    base: str, database_url: str
) -> None:
    await create_hero(base, "bob@example.test", "Copperpot", faction="royal", hair_style="84")
    async with httpx2.AsyncClient() as ann, httpx2.AsyncClient() as twin:
        for client in (ann, twin):
            await post_sign_in(client, base, "ann@example.test", PASSWORD)
        check = await ann.get(f"{base}/bfheroes/heroes/name", params={"name": "copperpot"})
        assert "copperpot is taken. Names ignore capitals." in markup(check.content)
        taken = await create(ann, base, "copperpot")
        assert taken.status_code == 409
        assert "copperpot was not created: the name was taken a moment earlier." in markup(
            taken.content
        )

        # A style of the other faction's list takes its place in the chosen faction's list.
        await create(ann, base, "Brassbuckle", hair_style="84")
        assert (
            await select_value(
                database_url,
                "SELECT value FROM stat JOIN persona ON persona.id = owner_id"
                " WHERE name = 'Brassbuckle' AND key = 'c_hrs'",
            )
            == 122
        )
        await create(ann, base, "Tin_Tankard", **{"class": "gunner"})
        await create(ann, base, "Kettlebrook", faction="royal", facial_hair="0")
        # Two requests at once for the last slot: one hero is created, the other is refused.
        raced = await asyncio.gather(
            create(ann, base, "Pip-Lantern"), create(twin, base, "Mudlark")
        )
        assert sorted(answer.status_code for answer in raced) == [303, 409]
        full = markup((await ann.get(f"{base}{CREATOR}")).content)
    assert "All 4 hero slots are in use" in full and 'name="faction"' not in full
    assert (
        await select_value(
            database_url,
            "SELECT count(*) FROM persona JOIN account ON account.id = account_id"
            " WHERE nuid = 'ann@example.test'",
        )
        == 4
    )
    # The account's first hero stays its default one.
    assert (
        await select_value(
            database_url,
            "SELECT name FROM hero_account JOIN persona ON persona.id = default_persona_id"
            " WHERE hero_account.account_id = persona.account_id AND name LIKE 'B%'",
        )
        == "Brassbuckle"
    )
