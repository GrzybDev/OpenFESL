from xml.etree import ElementTree

import anyio
import pytest
from heroes import create_hero
from support import (
    FESL,
    THEATER,
    Game,
    cli,
    enter_theater,
    fetch,
    game,
    ids,
    launcher_sign_in,
    let_in,
    log_in_player,
    update_stats,
)

from openfesl.protocol.fields import Fields

pytestmark = pytest.mark.anyio

MAGMA = "www.battlefieldheroes.com"

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


async def start(
    server_url: str, process: Game, account: str, role: str = "play"
) -> tuple[dict[str, str], int, int]:
    """Sign an account in at the launcher and log its game in to FESL and Theater as its hero.

    Args:
        server_url: The server's relay address.
        process: The game process.
        account: The account's login name.
        role: Whether the game is started to play or to host.

    Returns:
        The magma cookie the game sends, the account's number and the hero's.
    """
    session = (await launcher_sign_in(server_url, account, "secret-pass", role=role)).json()
    cookie = {"Cookie": f"magma={session['sessionId']}"}
    _, _, body = await fetch(server_url, "/nucleus/authToken", MAGMA, cookie)
    token = ElementTree.fromstring(body).findtext("token") or ""
    await process.hello(FESL, "bfwest-pc", client_type="client-noreg")
    silent = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
    account_id = int((await process.request(FESL, "acct", silent))["userId"])
    hero = [("TXN", "NuLoginPersona"), ("name", session["personaName"])]
    playing = await process.request(FESL, "acct", hero)
    await enter_theater(process, THEATER, "bfwest-pc", playing["lkey"])
    return cookie, account_id, int(playing["userId"])


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
async def test_a_server_that_logs_in_as_a_player_keeps_the_rounds_of_the_players_in_its_game(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # Cid registers first and Ann makes her hero first, so Ann's account has the number of Cid's
    # hero: the game names an account and a hero by the same owner type, and only the key tells
    # which one it means.
    accounts = ("cid@example.test", "ann@example.test", "host@example.test")
    for account in accounts:
        await register(server_url, account)
    base = server_url.replace("ws://", "http://").rstrip("/")
    for account in (accounts[1], accounts[0], accounts[2]):
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
