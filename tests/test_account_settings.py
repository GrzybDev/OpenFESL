import pytest
from support import Answer, Game, game, log_in_player, login

from openfesl.core.accounts import Credentials
from openfesl.core.logins import Logins
from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
NUID = "ann@example.test"


def update_account(
    password: str, *fields: tuple[str, str], nuid: str = NUID
) -> list[tuple[str, str]]:
    return [("TXN", "NuUpdateAccount"), ("nuid", nuid), ("password", password), *fields]


def update_password(password: str, new: str) -> list[tuple[str, str]]:
    return [
        ("TXN", "NuUpdatePassword"),
        ("password", password),
        ("newPassword", new),
        ("returnEncryptedInfo", "1"),
    ]


async def silent_login(player: Game, token: str) -> Answer:
    fields = [("TXN", "NuLogin"), ("returnEncryptedInfo", "1"), ("encryptedInfo", token)]
    return await player.request(FESL, "acct", fields)


async def test_a_player_changes_the_country_and_the_password_which_ends_remembered_logins(
    server_url: str,
) -> None:
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        early = await player.request(FESL, "acct", [("TXN", "NuGetAccount")])
        assert early["errorCode"] == "20"
        first = await log_in_player(player, ConnectionKey(Channel.FESL, 2), NUID)
        mine = ConnectionKey(Channel.FESL, 2)

        details = await player.request(mine, "acct", [("TXN", "NuGetAccount")])
        assert (details["nuid"], details["userId"], details["country"]) == (
            NUID,
            first["userId"],
            "PL",
        )
        wrong = await player.request(mine, "acct", update_account("wrong-pass", ("country", "DE")))
        assert wrong["errorCode"] == "122"
        invalid = await player.request(
            mine,
            "acct",
            update_account(
                "secret-pass",
                ("country", "XX"),
                ("DOBDay", "1"),
                ("DOBMonth", "1"),
                ("DOBYear", "2024"),
                nuid="eve@example.test",
            ),
        )
        assert invalid["errorCode"] == "21"
        assert [invalid[f"errorContainer.{index}.fieldName"] for index in range(3)] == [
            "email",
            "country",
            "dob",
        ]
        changed = update_account("secret-pass", ("country", "de"), ("globalOptin", "1"))
        assert "errorCode" not in await player.request(mine, "acct", changed)
        assert (await player.request(mine, "acct", [("TXN", "NuGetAccount")]))["country"] == "DE"

        for password, new, code in [
            ("wrong-pass", "other-pass", "122"),
            ("secret-pass", "short", "21"),
        ]:
            refused = await player.request(mine, "acct", update_password(password, new))
            assert refused["errorCode"] == code
        long = await player.request(mine, "acct", update_password("secret-pass", "x" * 32))
        assert (long["errorContainer.0.fieldName"], long["errorContainer.0.fieldError"]) == (
            "newPassword",
            "2",
        )
        token = (await player.request(mine, "acct", update_password("secret-pass", "other-pass")))[
            "encryptedPasswordInfo"
        ]

    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        assert (await silent_login(player, first["encryptedLoginInfo"]))["errorCode"] == "122"
        assert (await player.request(FESL, "acct", login(NUID, "secret-pass")))[
            "errorCode"
        ] == "122"
        assert (await silent_login(player, token))["userId"] == first["userId"]
        lookup = [("TXN", "NuGetAccountByNuid"), ("nuid", NUID.upper()), ("password", "other-pass")]
        assert (await player.request(FESL, "acct", lookup))["nuid"] == NUID


async def test_a_login_checked_against_the_old_password_gets_no_token_once_it_changed(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = Logins.check

    async def changed_meanwhile(self: Logins, nuid: str, password: str) -> Credentials:
        # The owner changes the password between the login's check and its remembered token.
        checked = await check(self, nuid, password)
        await self._store.change_password(checked.account.id, await self.hash("other-pass"))
        return checked

    async with game(server_url) as player:
        await log_in_player(player, FESL, NUID)
        monkeypatch.setattr(Logins, "check", changed_meanwhile)
        other = ConnectionKey(Channel.FESL, 2)
        await player.hello(other, "bfbc2-pc")
        raced = await player.request(other, "acct", login(NUID, "secret-pass"))
        assert (raced["errorCode"], "encryptedLoginInfo" in raced) == ("122", False)
