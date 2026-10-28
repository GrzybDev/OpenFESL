import asyncio
from xml.etree import ElementTree

import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in, select_value
from heroes import CREATOR, MAGMA, create, create_hero
from identity import form_token
from support import FESL, fetch, game, launcher_sign_in, markup, once_token, visible

pytestmark = pytest.mark.anyio

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
        assert "Play Now starts it again as Mossgrove" in done
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


async def test_the_heroes_page_lists_each_hero_and_changes_the_hero_the_game_logs_in_with(
    base: str, server_url: str, database_url: str
) -> None:
    first = await create_hero(base, "ann@example.test", "Brassbuckle", faction="royal")
    second = await create_hero(base, "ann@example.test", "Tin_Tankard", **{"class": "soldier"})
    stranger = await create_hero(base, "bob@example.test", "Mudlark")
    await execute(
        database_url,
        "UPDATE entitlement SET use_count = 2 WHERE persona_id = :hero",
        hero=first,
    )
    # A hero the launcher made before the site's creator, without a faction, class or look.
    await execute(
        database_url,
        "INSERT INTO persona (account_id, namespace, name)"
        " SELECT account_id, namespace, 'Oldlamp' FROM persona WHERE id = :hero",
        hero=first,
    )
    legacy = await select_value(database_url, "SELECT id FROM persona WHERE name = 'Oldlamp'")
    async with httpx2.AsyncClient() as client:
        asked = await client.get(f"{base}/bfheroes/heroes")
        assert asked.headers["location"] == "/sign-in?next=%2Fbfheroes%2Fheroes"
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}/bfheroes/heroes")
        shown = visible(page.content)
        # Slot by slot: the first hero is the default one, the others can become it, apart from
        # the one the game could not start with.
        assert shown.index("Brassbuckle") < shown.index("Tin_Tankard") < shown.index("Oldlamp")
        assert "3 of 4 used" in shown and "Free hero slot" in shown
        assert "12 items · 1 running low" in shown and "0 unspent of 0" in shown
        heroes = (first, second, legacy)
        offered = [f"/bfheroes/heroes/{hero}/default" in page.text for hero in heroes]
        assert offered == [False, True, False]

        # A form from another site changes nothing.
        forged = {"csrf": "forged", "next": "/bfheroes/play"}
        await client.post(f"{base}/bfheroes/heroes/{second}/default", data=forged)
        unchanged = visible((await client.get(f"{base}/bfheroes/heroes")).content)
        assert "Default hero Brassbuckle" in unchanged
        # The selector of every page returns to that page.
        form = {"csrf": form_token(page.text), "next": "/bfheroes/play"}
        made = await client.post(f"{base}/bfheroes/heroes/{second}/default", data=form)
        assert made.headers["location"] == "/bfheroes/play"
        play = visible((await client.get(f"{base}/bfheroes/play")).content)
        assert "Tin_Tankard is now the default hero" in play
        assert "Default hero Tin_Tankard" in play
        # Another account's hero stays as it is, as does a persona without a faction and class.
        await client.post(f"{base}/bfheroes/heroes/{stranger}/default", data=form)
        await client.post(f"{base}/bfheroes/heroes/{legacy}/default", data=form)
    session = (await launcher_sign_in(server_url, "ann@example.test", PASSWORD)).json()
    assert session["personaName"] == "Tin_Tankard"
    assert (await launcher_sign_in(server_url, "bob@example.test", PASSWORD)).json()[
        "personaName"
    ] == "Mudlark"


async def test_a_new_name_costs_funds_only_when_it_is_given(
    base: str, server_url: str, database_url: str
) -> None:
    hero = await create_hero(base, "ann@example.test", "Brassbuckle")
    stranger = await create_hero(base, "bob@example.test", "Copperpot")
    rename = f"{base}/bfheroes/heroes/{hero}/rename"
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(rename)
        assert "Funds after</dt><dd>1,001" in markup(page.content)
        csrf = form_token(page.text)
        check = {"name": "brassBUCKLE", "current": "Brassbuckle"}
        same = await client.get(f"{base}/bfheroes/heroes/name", params=check)
        assert 'data-check="same"' in same.text
        # Another account's hero has no rename page, and a form from another site renames nothing.
        theirs = f"{base}/bfheroes/heroes/{stranger}/rename"
        asked = await client.get(theirs)
        sent = await client.post(theirs, data={"csrf": csrf, "name": "Mudlark"})
        assert (asked.status_code, sent.status_code) == (404, 404)
        named = "SELECT name FROM persona WHERE id = :hero"
        assert await select_value(database_url, named, hero=stranger) == "Copperpot"
        await client.post(rename, data={"csrf": "forged", "name": "Mudlark"})
        refused = visible((await client.get(f"{base}/bfheroes/heroes")).content)
        assert "Brassbuckle was not renamed: the form had expired. Nothing was spent." in refused

        # A name that another hero holds comes back in the form, which says so.
        taken = await client.post(rename, data={"csrf": csrf, "name": "copperpot"})
        assert taken.status_code == 409 and 'value="copperpot"' in taken.text
        assert "copperpot is taken. Names ignore capitals." in markup(taken.content)
        # Nothing was spent, so the Funds were not even taken out of their allowance.
        assert await select_value(database_url, "SELECT count(*) FROM wallet") == 0

        await client.post(rename, data={"csrf": csrf, "name": "Copper_Kettle"})
        heroes = visible((await client.get(f"{base}/bfheroes/heroes")).content)
        assert "Renamed Brassbuckle to Copper_Kettle. 1,499 Funds spent;" in heroes
        assert "1,001 Funds 0 Valor Points" in heroes
        poor = await client.post(rename, data={"csrf": csrf, "name": "Kettle_Two"})
        assert poor.status_code == 303
        heroes = markup((await client.get(f"{base}/bfheroes/heroes")).content)
        assert "the account has 1,001 Funds; a rename costs 1,499. Nothing was spent." in heroes

        # Two renames at once with the price of one: one is given, and the balance stays whole.
        await execute(database_url, "UPDATE wallet SET balance = 1500")
        raced = await asyncio.gather(
            client.post(rename, data={"csrf": csrf, "name": "Kettle_Two"}),
            client.post(rename, data={"csrf": csrf, "name": "Kettle_Three"}),
        )
        assert all(answer.status_code == 303 for answer in raced)
    assert await select_value(database_url, "SELECT balance FROM wallet") == 1
    ledger = await select_value(
        database_url, "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY id) FROM wallet_entry"
    )
    assert ledger == "allowance:2500 rename:-1499 rename:-1499"
    session = (await launcher_sign_in(server_url, "ann@example.test", PASSWORD)).json()
    assert session["personaName"] in ("Kettle_Two", "Kettle_Three")


async def test_a_further_hero_slot_is_bought_once_with_the_funds_for_one(
    base: str, database_url: str
) -> None:
    for name in ("Brassbuckle", "Tin_Tankard", "Pip-Lantern", "Mossgrove"):
        await create_hero(base, "ann@example.test", name)
    slots = f"{base}/bfheroes/user/buyCharacterSlots"
    async with httpx2.AsyncClient() as client, httpx2.AsyncClient() as twin:
        for session in (client, twin):
            await post_sign_in(session, base, "ann@example.test", PASSWORD)
        full = await client.get(f"{base}{CREATOR}")
        shown = visible(full.content)
        assert "All 4 hero slots are in use" in shown
        assert "Price 1,749 Funds · Funds now 2,500" in shown
        assert 'href="/bfheroes/user/buyCharacterSlots?next=%2Fbfheroes%2Fuser%2FcreateHero"' in (
            full.text
        )
        page = await client.get(f"{slots}?next={CREATOR}")
        assert "4 → 5" in visible(page.content) and "Funds after 751" in visible(page.content)

        # A form from another site, or one without its one-time token, buys nothing.
        for sent in ({"csrf": "forged"}, {"csrf": form_token(page.text), "price": "1749"}):
            await client.post(slots, data={**sent, "next": CREATOR})
            refused = visible((await client.get(f"{base}{CREATOR}")).content)
            assert "No hero slot was added: the form had expired. Nothing was spent." in refused
        # Two purchases at once with the Funds for one: one slot is added, nothing goes below 0.
        form = {"csrf": form_token(page.text), "next": CREATOR, "once": once_token(page.text)}
        other = (await twin.get(slots)).text
        raced = await asyncio.gather(
            client.post(slots, data=form),
            twin.post(slots, data={**form, "csrf": form_token(other), "once": once_token(other)}),
        )
        assert [answer.headers["location"] for answer in raced] == [CREATOR, CREATOR]
        creator = await client.get(f"{base}{CREATOR}")
        opened = visible(creator.content)
        refused = visible((await twin.get(f"{base}{CREATOR}")).content)

        # With every slot used again, the purchase stays in view, unavailable, saying why.
        await create(client, base, "Mudlark")
        short = await client.get(f"{base}{CREATOR}")
        assert 'aria-disabled="true" aria-describedby="creator-why"' in short.text
        assert "Funds now 751 · 998 short" in visible(short.content)
        assert "The account has 751 Funds; a slot costs 1,749." in visible(short.content)

        # One form sent twice at once, as a double click without scripts sends it, and later
        # again, as Back and sending it do, adds one slot.
        await execute(database_url, "UPDATE wallet SET balance = 4000")
        page = await client.get(slots)
        form = {"csrf": form_token(page.text), "next": CREATOR, "once": once_token(page.text)}
        await asyncio.gather(*(client.post(slots, data=form) for _ in range(2)))
        await client.get(f"{base}{CREATOR}")
        await client.post(slots, data=form)
        again = visible((await client.get(f"{base}{CREATOR}")).content)
    messages = opened + refused
    assert "Hero slot 5 added. 1,749 Funds spent." in messages
    assert "No hero slot was added: the account has 751 Funds; a slot costs 1,749." in messages
    assert "4 of 5 used" in opened and 'name="faction"' in creator.text
    assert "This hero slot purchase was made already; sending it again added no further" in again
    assert await select_value(database_url, "SELECT balance FROM wallet") == 2251
    ledger = await select_value(
        database_url, "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY id) FROM wallet_entry"
    )
    assert ledger == "allowance:2500 hero-slot:-1749 hero-slot:-1749"
