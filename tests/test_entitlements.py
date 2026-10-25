import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from support import cli, entitle_game, game, host_game, log_in_player, login, registration

from openfesl.protocol.relay import Channel, ConnectionKey
from openfesl.server.cli import main

FESL = ConnectionKey(Channel.FESL, 1)


async def run(database_url: str, statement: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(text(statement))
    await engine.dispose()


@pytest.mark.anyio
async def test_keys_grant_entitlements_once_and_a_revoked_one_stays_revoked(
    open_hosting: None, server_url: str, database_url: str
) -> None:
    async with game(server_url) as player, game(server_url) as server:
        account = await log_in_player(player, FESL, "ann@example.test")
        unknown = await player.request(FESL, "acct", [("TXN", "NuEntitleUser"), ("key", "NOPE")])
        assert unknown["errorCode"] == "181"
        for _ in range(2):
            veteran = await player.request(
                FESL, "acct", [("TXN", "NuEntitleUser"), ("key", "activate-veteran")]
            )
            product = (veteran["productList.[]"], veteran["productList.0.productId"])
            assert product == ("1", "OFB-EAST:40873")
        # A key whose grants name no product still answers an entry, or the game reports a
        # failed redeem.
        kits = await player.request(
            FESL, "acct", [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-SPECACT")]
        )
        assert (kits["productList.[]"], kits["productList.0.name"]) == ("1", "BFBC2:PC:ALLKIT")
        assert "productList.0.productId" not in kits

        query = [("TXN", "NuGetEntitlements"), ("groupName", "AddsVetRank"), ("status", "ACTIVE")]
        own = await player.request(FESL, "acct", query)
        tags = {own["entitlements.0.entitlementTag"], own["entitlements.1.entitlementTag"]}
        assert (own["entitlements.[]"], tags) == (
            "2",
            {"BFBC2:PC:ADDSVETRANK", "BF3:PC:ADDSVETRANK"},
        )
        count = await player.request(FESL, "acct", [("TXN", "NuGetEntitlementCount")])
        assert count["entitlementCount"] == "7"
        # The game's check for licences of its PC, by the network card's address.
        device = [
            ("TXN", "NuGetEntitlements"),
            ("global", "1"),
            ("status", "ACTIVE"),
            ("devicePhysicalId", "$0a0b0c0d0e0f"),
        ]
        assert (await player.request(FESL, "acct", device))["entitlements.[]"] == "0"

        await server.hello(FESL, "bfbc2-pc-server", client_type="server")
        await server.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
        players = [*query, ("masterUserId", account["userId"])]
        # Anyone logs in like a dedicated server; one that hosts reads its players' entitlements.
        assert (await server.request(FESL, "acct", players))["errorCode"] == "21"
        async with game(server_url) as hosting:
            await host_game(hosting)
            assert (await hosting.request(FESL, "acct", players))["entitlements.[]"] == "2"
        other = [*query, ("masterUserId", "999999")]
        assert (await player.request(FESL, "acct", other))["errorCode"] == "21"
        redeem = [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-VIETNAM")]
        assert (await server.request(FESL, "acct", redeem))["errorCode"] == "21"

    await run(database_url, "UPDATE entitlement SET status = 'REVOKED' WHERE tag = 'bfbc2-pc'")
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        entitle = entitle_game("ann@example.test", "secret-pass", "ACTIVATE-GAME")
        assert (await player.request(FESL, "acct", entitle))["errorCode"] == "120"
        again = await player.request(FESL, "acct", login("ann@example.test", "secret-pass"))
        assert again["errorCode"] == "120"


@pytest.mark.anyio
async def test_a_key_is_refused_once_used_up_and_to_an_account_that_redeemed_it(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    limited = await cli(
        capsys, "keys", "limit", "activate-premium", "--max-uses", "1", "--once-per-account"
    )
    assert limited == "ACTIVATE-PREMIUM  uses 0 of 1  once per account\n"
    redeem = [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-PREMIUM")]
    async with game(server_url) as first, game(server_url) as second:
        await log_in_player(first, FESL, "ann@example.test")
        await log_in_player(second, FESL, "ben@example.test")
        assert "errorCode" not in await first.request(FESL, "acct", redeem)
        assert (await first.request(FESL, "acct", redeem))["errorCode"] == "182"
        assert (await second.request(FESL, "acct", redeem))["errorCode"] == "180"

        # Entered again by the same account, the key grants nothing new and uses nothing up.
        await cli(capsys, "keys", "limit", "ACTIVATE-PREMIUM", "--any")
        assert "errorCode" not in await first.request(FESL, "acct", redeem)
        assert (await second.request(FESL, "acct", redeem))["errorCode"] == "180"
        unlimited = await cli(capsys, "keys", "limit", "ACTIVATE-PREMIUM", "--unlimited")
        shown = "ACTIVATE-PREMIUM  uses 1 of unlimited  any number of times per account\n"
        assert unlimited == shown
        assert "errorCode" not in await second.request(FESL, "acct", redeem)

    # The key prompt at login refuses a used-up key too.
    await cli(capsys, "keys", "limit", "ACTIVATE-GAME", "--max-uses", "2")
    async with game(server_url) as third:
        await third.hello(FESL, "bfbc2-pc")
        terms = (await third.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
        await third.request(FESL, "acct", registration("cat@example.test", "secret-pass", terms))
        entitle = entitle_game("cat@example.test", "secret-pass", "ACTIVATE-GAME")
        assert (await third.request(FESL, "acct", entitle))["errorCode"] == "180"


@pytest.mark.anyio
async def test_a_disabled_key_grants_nothing(server_url: str, database_url: str) -> None:
    await run(
        database_url, "UPDATE product_key SET enabled = false WHERE code = 'ACTIVATE-SPECACT'"
    )
    try:
        async with game(server_url) as player:
            await log_in_player(player, FESL, "ann@example.test")
            redeem = [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-SPECACT")]
            assert (await player.request(FESL, "acct", redeem))["errorCode"] == "181"
    finally:
        await run(database_url, "UPDATE product_key SET enabled = true")


def test_importing_keys_adds_only_the_missing_ones(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    main(["keys", "import"])
    capsys.readouterr()
    asyncio.run(run(database_url, "DELETE FROM product_key WHERE code = 'ACTIVATE-VIETNAM'"))
    main(["keys", "import"])
    main(["keys", "import"])

    assert capsys.readouterr().out.splitlines() == ["Added 1 product keys", "Added 0 product keys"]


@pytest.mark.anyio
async def test_the_game_key_prompt_takes_only_a_key_for_the_game(server_url: str) -> None:
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
        await player.request(FESL, "acct", registration("ann@example.test", "secret-pass", terms))

        content = entitle_game("ann@example.test", "secret-pass", "ACTIVATE-VIETNAM")
        assert (await player.request(FESL, "acct", content))["errorCode"] == "181"
        await player.request(
            FESL, "acct", entitle_game("ann@example.test", "secret-pass", "ACTIVATE-GAME")
        )
        await player.request(FESL, "acct", login("ann@example.test", "secret-pass"))
        owned = await player.request(
            FESL, "acct", [("TXN", "NuGetEntitlementCount"), ("groupName", "BFBC2PC")]
        )
        assert owned["entitlementCount"] == "0"


@pytest.mark.anyio
async def test_a_game_cannot_grant_its_account_what_no_key_gave_it(server_url: str) -> None:
    async with game(server_url) as player, game(server_url) as server:
        await log_in_player(player, FESL, "ann@example.test")
        grant = [("TXN", "NuGrantEntitlement"), ("entitlementTag", "BFBC2:PC:VIETNAM_ACCESS")]
        assert (await player.request(FESL, "acct", grant))["errorCode"] == "21"
        vietnam = [("TXN", "NuGetEntitlementCount"), ("groupName", "BFBC2PC")]
        assert (await player.request(FESL, "acct", vietnam))["entitlementCount"] == "0"

        await player.request(FESL, "acct", [("TXN", "NuEntitleUser"), ("key", "ACTIVATE-VIETNAM")])
        assert "errorCode" not in await player.request(FESL, "acct", grant)
        assert (await player.request(FESL, "acct", vietnam))["entitlementCount"] == "2"

        await server.hello(FESL, "bfbc2-pc-server", client_type="server")
        await server.request(FESL, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
        assert (await server.request(FESL, "acct", grant))["errorCode"] == "21"
