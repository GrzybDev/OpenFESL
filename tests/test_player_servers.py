import asyncio
from xml.etree import ElementTree

import anyio
import pytest
from accounts import select_value
from heroes import MAGMA, create_hero, start, wallets
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from support import (
    FESL,
    THEATER,
    Game,
    cli,
    fetch,
    game,
    ids,
    launcher_sign_in,
    let_in,
    log_in_player,
    login,
    update_stats,
)

from openfesl.core.stats import PERSONA, Change, LevelPoints, Levels, Owner, bucket_of
from openfesl.protocol.fields import Fields
from openfesl.protocol.rank import Period, UpdateType
from openfesl.storage.stats import SqlStatStore

pytestmark = pytest.mark.anyio

# The game a Battlefield Heroes 1.89 dedicated server lists, as the game sends it.
SERVER_GAME = [
    ("LID", "-1"),
    ("RESERVE-HOST", "1"),
    ("NAME", "Invented Heroes Server"),
    ("PORT", "18567"),
    ("HTTYPE", "A"),
    ("TYPE", "G"),
    ("QLEN", "0"),
    ("DISABLE-AUTO-DEQUEUE", "0"),
    ("HXFR", "1"),
    ("INT-PORT", "18567"),
    ("INT-IP", "192.168.1.10"),
    ("MAX-PLAYERS", "16"),
    ("B-maxObservers", "0"),
    ("B-numObservers", "0"),
    ("UGID", ""),
    ("SECRET", ""),
    ("B-version", ""),
    ("JOIN", "O"),
    ("RT", ""),
]


async def register(server_url: str, account: str) -> None:
    # Battlefield Heroes registers no accounts, so they come from another game here.
    async with game(server_url) as registering:
        await log_in_player(registering, FESL, account)


async def hero_of(player: Game, name: str) -> int:
    """Return the number of a hero that a player looks up by its name."""
    lookup = [("TXN", "NuLookupUserInfo"), ("userInfo.0.userName", name), ("userInfo.[]", "1")]
    return int((await player.request(FESL, "acct", lookup))["userInfo.0.userId"])


async def until_listed(player: Game) -> None:
    """Wait until the lobby shows a player the game that the admin approved."""
    with anyio.fail_after(5):
        while True:
            await player.theater(THEATER, "LLST", [("FILTER-FAV-ONLY", "0")])
            if (await player.theater_push(THEATER, "LDAT")).fields["NUM-GAMES"] == "1":
                return
            await anyio.sleep(0.02)


def get_stats(owner: int, *keys: str) -> Fields:
    return [
        ("TXN", "GetStats"),
        ("owner", str(owner)),
        ("ownerType", "1"),
        ("periodId", "0"),
        ("periodPast", "0"),
        *((f"keys.{index}", key) for index, key in enumerate(keys)),
        ("keys.[]", str(len(keys))),
    ]


def values(answer: dict[str, str], prefix: str = "stats.") -> list[str]:
    return [answer[f"{prefix}{index}.value"] for index in range(int(answer[prefix + "[]"]))]


async def magma(server_url: str, cookie: dict[str, str], path: str) -> tuple[str, str | None]:
    """Return the root of a Magma answer and the cause of its failure, if it is one."""
    _, _, body = await fetch(server_url, path, MAGMA, cookie)
    answer = ElementTree.fromstring(body)
    failure = answer.find("failure")
    return answer.tag, failure.get("cause") if failure is not None else None


@pytest.mark.usefixtures("prompt_decisions")
async def test_a_server_keeps_only_the_rounds_of_the_players_in_its_game(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Cid registers first and Ann makes her hero first, so Ann's account has the number of Cid's
    # hero: the game names an account and a hero by the same owner type, and only the key tells
    # which one it means.
    accounts = ("cid@example.test", "ann@example.test")
    for account in accounts:
        await register(server_url, account)
    base = server_url.replace("ws://", "http://").rstrip("/")
    for account in (accounts[1], accounts[0]):
        await create_hero(base, account, account.split("@")[0])
    # Ann's second hero stays out of the server's game.
    ann_two = await create_hero(base, accounts[1], "ann_two")
    async with game(server_url) as ann, game(server_url) as server:
        _, ann_account, ann_hero = await start(server_url, ann, "ann@example.test")
        cid_hero = await hero_of(ann, "cid")
        assert cid_hero == ann_account
        cookie, _, _ = await start(server_url, server, "host@example.test", role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        assert created.error is None
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        await until_listed(ann)
        pid = await let_in(server, ann, created.fields)

        # A round's end: the hero's stats under the hero, the account's under the account.
        round_end = update_stats(
            (str(ann_hero), "1", [(3, "xp", "250.0000", "", "0"), (3, "games", "1.0000", "", "0")]),
            (
                str(ann_account),
                "1",
                [(0, "cdm", "299.0000", "", "0"), (3, "dmc", "1.0000", "", "0")],
            ),
        )
        assert "errorCode" not in await server.request(FESL, "rank", round_end)
        for outsider in (cid_hero, ann_two):
            cheat = update_stats((str(outsider), "1", [(3, "xp", "9999.0000", "", "0")]))
            assert (await server.request(FESL, "rank", cheat))["errorCode"] == "21"

        loaded = await server.request(FESL, "rank", get_stats(ann_hero, "xp", "games"))
        own_account = await ann.request(FESL, "rank", get_stats(ann_account, "c_tut", "cdm", "dmc"))
        heroes = await ann.request(
            FESL,
            "rank",
            [
                ("TXN", "GetStatsForOwners"),
                ("owners.0.ownerId", str(ann_hero)),
                ("owners.0.ownerType", "1"),
                ("owners.1.ownerId", str(cid_hero)),
                ("owners.1.ownerType", "1"),
                ("owners.[]", "2"),
                ("periodId", "0"),
                ("keys.0", "xp"),
                ("keys.[]", "1"),
            ],
        )
        lists = [
            await magma(server_url, cookie, path)
            for path in (
                f"/nucleus/entitlements/{ann_hero}",
                f"/nucleus/wallets/{ann_hero}",
                f"/nucleus/wallets/{cid_hero}",
                f"/nucleus/wallets/{ann_two}",
            )
        ]
        others = [("TXN", "NuGetEntitlements"), ("masterUserId", str(ann_account))]
        entitlements = await server.request(FESL, "acct", others)

        # The server reports a player's round as the player leaves, after it reported it gone.
        left = await server.theater(THEATER, "PLVT", [*ids(created.fields), ("PID", pid)])
        assert left.error is None
        leaving = update_stats(
            (str(ann_hero), "1", [(3, "xp", "10.0000", "", "0")]),
            (str(ann_account), "1", [(0, "c_slm", "25.0000", "", "0")]),
        )
        reported = await server.request(FESL, "rank", leaving)
        after_leaving = await magma(server_url, cookie, f"/nucleus/wallets/{ann_hero}")

        await cli(capsys, "servers", "revoke", created.fields["UGID"])
        late = update_stats((str(ann_hero), "1", [(3, "xp", "10.0000", "", "0")]))
        revoked = await server.request(FESL, "rank", late)
        after = await magma(server_url, cookie, f"/nucleus/wallets/{ann_hero}")
        kept = await ann.request(FESL, "rank", get_stats(0, "xp", "c_slm"))

    assert values(loaded) == ["250.0000", "1.0000"]
    assert values(own_account) == ["0.0000", "299.0000", "1.0000"]
    assert values(heroes, "stats.0.stats.") + values(heroes, "stats.1.stats.") == [
        "250.0000",
        "0.0000",
    ]
    assert lists == [
        ("entitlements", None),
        ("billingAccounts", None),
        ("error", "USER_DOES_NOT_MATCH"),
        ("error", "USER_DOES_NOT_MATCH"),
    ]
    assert "errorCode" not in entitlements
    assert "errorCode" not in reported
    assert after_leaving == ("billingAccounts", None)
    assert revoked["errorCode"] == "21"
    assert after == ("error", "USER_DOES_NOT_MATCH")
    assert values(kept) == ["260.0000", "25.0000"]


@pytest.mark.usefixtures("prompt_decisions")
async def test_a_hero_earns_a_hero_point_on_each_even_level_once(
    server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    accounts = ("ann@example.test", "host@example.test")
    base = server_url.replace("ws://", "http://").rstrip("/")
    for account in accounts:
        await register(server_url, account)
        await create_hero(base, account, account.split("@")[0])
    async with game(server_url) as ann, game(server_url) as server:
        cookie, _, hero = await start(server_url, ann, "ann@example.test")
        await start(server_url, server, "host@example.test", role="host")
        created = await server.theater(THEATER, "CGAM", SERVER_GAME)
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        await until_listed(ann)
        await let_in(server, ann, created.fields)

        # From level 1: two levels gained one by one, three at once, then the level set lower
        # and raised again through levels already paid for, and a level not paid for yet.
        points = []
        for kind, levels in ((3, "1"), (3, "1"), (3, "3"), (0, "2"), (3, "2"), (3, "4")):
            pushed = update_stats((str(hero), "1", [(kind, "level", levels, "", "0")]))
            assert "errorCode" not in await server.request(FESL, "rank", pushed)
            points.append((await wallets(server_url, cookie, hero))["_DH"])
        level = await ann.request(FESL, "rank", get_stats(hero, "level"))
        # Two changes of the level at once pay its points once.
        engine = create_async_engine(database_url)
        store = SqlStatStore(async_sessionmaker(engine))
        all_time = bucket_of(Period.ALL_TIME, 0)
        raised = Change(Owner(PERSONA, hero), "level", all_time, UpdateType.SET, 10.0, None)
        earned = Levels("level", 30, LevelPoints(2, "bfheroes", "_DH", 0))
        await asyncio.gather(*(store.apply("/eagames/bfwest", [raised], earned) for _ in range(2)))
        await engine.dispose()
        # A level past the highest is kept as the highest, and one below the first as the first.
        bounded = []
        for kind, levels in ((3, "1000000000"), (0, "-3")):
            pushed = update_stats((str(hero), "1", [(kind, "level", levels, "", "0")]))
            assert "errorCode" not in await server.request(FESL, "rank", pushed)
            [kept] = values(await ann.request(FESL, "rank", get_stats(hero, "level")))
            bounded.append((kept, (await wallets(server_url, cookie, hero))["_DH"]))

    assert points == [1, 1, 3, 3, 3, 4]
    assert values(level) == ["8.0000"]
    assert bounded == [("30.0000", 15), ("1.0000", 15)]
    ledger = await select_value(
        database_url,
        "SELECT string_agg(reason || ':' || delta, ' ' ORDER BY wallet_entry.id) FROM wallet_entry"
        " JOIN wallet ON wallet.id = wallet_id WHERE persona_id = :hero AND currency = '_DH'",
        hero=hero,
    )
    assert ledger == "level:1 level:2 level:1 level:1 level:10"


@pytest.mark.usefixtures("prompt_decisions")
async def test_servers_share_an_account_that_gives_nobody_the_rights_of_a_player(
    base: str, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await register(server_url, "ann@example.test")
    await create_hero(base, "ann@example.test", "ann")
    async with game(server_url) as ann, game(server_url) as first, game(server_url) as second:
        ann_cookie, ann_account, ann_hero = await start(server_url, ann, "ann@example.test")
        cookie, server_account, server_hero = await start(server_url, first, "", role="host")
        other, other_account, _ = await start(server_url, second, "", role="host")
        created = await first.theater(THEATER, "CGAM", SERVER_GAME)
        apart = await second.theater(THEATER, "CGAM", SERVER_GAME)
        await cli(capsys, "servers", "approve", created.fields["UGID"])
        await cli(capsys, "servers", "approve", apart.fields["UGID"])
        await until_listed(ann)
        await let_in(first, ann, created.fields)

        wallets = f"/nucleus/wallets/{ann_hero}"
        # The session alone is anyone's; only the process that hosts Ann reads what she holds.
        hosting = await magma(server_url, cookie, wallets)
        elsewhere = await magma(server_url, other, wallets)
        without_process = await magma(server_url, {"Cookie": cookie["Cookie"]}, wallets)
        _, _, may_upload = await fetch(
            server_url, f"/api/mugshot/mayupload/{server_hero}", MAGMA, cookie
        )
        ask_ann = f"/relationships/increase/nucleus:{server_account}/nucleus:{ann_account}/friends"
        _, _, friend = await fetch(server_url, ask_ann, MAGMA, cookie, b"")
        by_password = await launcher_sign_in(server_url, "bfh.server@openfesl.invalid", "any")

        # A player cannot take the servers' account with its name and a password.
        async with game(server_url) as player:
            await player.hello(FESL, "bfwest-pc", client_type="client-noreg")
            taken = await player.request(FESL, "acct", login("bfh.server@openfesl.invalid", "any"))

    assert ann_cookie != cookie
    assert other == {**cookie, "X-OpenFESL-Relay": other["X-OpenFESL-Relay"]}
    assert other_account == server_account
    assert created.fields["UGID"] != apart.fields["UGID"]
    assert hosting == ("billingAccounts", None)
    assert elsewhere == ("error", "USER_DOES_NOT_MATCH")
    assert without_process == ("error", "USER_DOES_NOT_MATCH")
    assert may_upload == b"0"
    assert b"USER_DOES_NOT_MATCH" in friend
    assert by_password.status_code == 401
    assert taken["errorCode"] == "122"
