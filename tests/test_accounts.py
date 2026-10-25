from datetime import UTC, datetime, timedelta

import anyio
import pytest
from support import (
    cli,
    enter_theater,
    entitle_game,
    game,
    log_in_player,
    login,
    registration,
    text,
)

from openfesl.core import admin
from openfesl.protocol.relay import Channel, ConnectionKey, Data

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)
SERVER_ACCOUNT = "bfbc2.server.pc@ea.com"


@pytest.fixture
def prompt_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    # The admin acts in another process; the server reads the log of actions this often.
    monkeypatch.setattr(admin, "ADMIN_CHECK_INTERVAL", 0.05)


async def test_a_player_registers_logs_in_and_a_later_login_sends_the_first_session_away(
    server_url: str,
) -> None:
    async with game(server_url) as first:
        await first.hello(FESL, "bfbc2-pc", locale="pl_PL")
        countries = await first.request(FESL, "acct", [("TXN", "GetCountryList")])
        count = int(countries["countryList.[]"])
        names = {
            countries[f"countryList.{index}.ISOCode"]: countries[f"countryList.{index}.description"]
            for index in range(count)
        }
        assert names["PL"] == "Polska"
        terms = await first.request(FESL, "acct", [("TXN", "NuGetTos"), ("countryCode", "PL")])
        assert "Electronic Arts" in terms["tos"]
        assert "nieoficjalna" in terms["tos"]

        form = registration("ann@example.test", "secret-pass", terms["version"])
        assert "errorCode" not in await first.request(FESL, "acct", form)
        refused = await first.request(FESL, "acct", login("ann@example.test", "secret-pass"))
        assert refused["errorCode"] == "120"
        entitle = entitle_game("ann@example.test", "secret-pass", "activate-game")
        assert "errorCode" not in await first.request(FESL, "acct", entitle)
        answer = await first.request(FESL, "acct", login("ANN@example.test", "secret-pass"))
        assert answer["nuid"] == "ann@example.test"
        assert answer["lkey"].endswith(".")

        async with game(server_url) as second:
            await second.hello(FESL, "bfbc2-pc")
            # The game's own remembered login asks for no token, then keeps what comes back.
            token = answer["encryptedLoginInfo"]
            silent = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
            again = await second.request(FESL, "acct", silent)
            assert (again["userId"], again["encryptedLoginInfo"]) == (answer["userId"], token)
            assert (await first.push("Goodbye"))["reason"] == "2"


async def test_registration_and_login_refuse_what_the_game_must_correct(
    server_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]

        young = await player.request(
            FESL, "acct", registration("kid@example.test", "secret-pass", terms, born="2024-01-01")
        )
        assert young["errorCode"] == "21"
        assert (young["errorContainer.0.fieldName"], young["errorContainer.0.fieldError"]) == (
            "dob",
            "16",
        )
        # A NUL and bytes that are not UTF-8, which neither the database nor Argon2 takes; the
        # log names the refused field, never its value.
        unreadable = [("nul\0@example.test", "secret-pass"), ("bob@example.test", "hunter2\xff")]
        for nuid, password in unreadable:
            form = registration(nuid, password, terms)
            assert (await player.request(FESL, "acct", form))["errorCode"] == "21"
        short = await player.request(FESL, "acct", registration("bob@example.test", "short", terms))
        assert (short["errorCode"], short["errorContainer.0.fieldName"]) == ("21", "password")
        # Sixteen characters, but 32 bytes of UTF-8 on the wire, of which the SDK keeps 31; the
        # game shows its "password is too long" text for 3.
        long = registration("bob@example.test", "ł".encode().decode("latin-1") * 16, terms)
        refused = await player.request(FESL, "acct", long)
        assert (refused["errorContainer.0.fieldName"], refused["errorContainer.0.fieldError"]) == (
            "password",
            "3",
        )

        form = registration("bob@example.test", "secret-pass", "outdated")
        assert "errorCode" not in await player.request(FESL, "acct", form)
        form = registration("BOB@example.test", "secret-pass", terms)
        assert (await player.request(FESL, "acct", form))["errorCode"] == "160"

        for nuid, password, code in [
            ("nobody@example.test", "secret-pass", "101"),
            ("bob@example.test", "wrong-pass", "122"),
            ("bob@example.test", "hunter2\xff", "21"),
            ("bob@example.test", "secret-pass", "260"),
        ]:
            assert (await player.request(FESL, "acct", login(nuid, password)))["errorCode"] == code
        assert "Invalid acct NuLogin" in caplog.text
        assert "hunter2" not in caplog.text
        accepted = login("bob@example.test", "secret-pass", ("tosVersion", terms))
        assert (await player.request(FESL, "acct", accepted))["errorCode"] == "120"
        entitle = entitle_game("bob@example.test", "secret-pass", "ACTIVATE-GAME")
        await player.request(FESL, "acct", entitle)
        assert "errorCode" not in await player.request(
            FESL, "acct", login("bob@example.test", "secret-pass")
        )


async def test_dedicated_servers_share_the_server_account_of_their_game(server_url: str) -> None:
    async with game(server_url) as first, game(server_url) as second:
        for server in (first, second):
            await server.hello(FESL, "bfbc2-pc-server", client_type="server")
            answer = await server.request(FESL, "acct", login(SERVER_ACCOUNT, "from-game-data"))
            assert answer["nuid"] == SERVER_ACCOUNT

        other = ConnectionKey(Channel.FESL, 2)
        await first.hello(other, "bfbc2-pc-server", client_type="server")
        refused = await first.request(other, "acct", login("someone@example.test", "secret"))
        assert refused["errorCode"] == "122"
        # Answered after the second server's login, so a Goodbye would have arrived by now.
        assert first.pushed("Goodbye") is None

    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
        squat = await player.request(
            FESL, "acct", registration(SERVER_ACCOUNT, "secret-pass", terms)
        )
        assert squat["errorCode"] == "160"
        refused = await player.request(FESL, "acct", login(SERVER_ACCOUNT, "from-game-data"))
        assert refused["errorCode"] == "122"
        disguised = ConnectionKey(Channel.FESL, 2)
        await player.hello(disguised, "bfbc2-pc-server")
        refused = await player.request(disguised, "acct", login(SERVER_ACCOUNT, "from-game-data"))
        assert refused["errorCode"] == "122"


def born(age: int) -> str:
    """Return the birth date of a player who turned some age this New Year's Day."""
    return f"{datetime.now(UTC).year - age}-01-01"


async def test_registration_checks_the_birth_date_against_the_country_age_limits(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc")
        countries = await player.request(FESL, "acct", [("TXN", "GetCountryList")])
        limits = {
            countries[f"countryList.{index}.ISOCode"]: (
                countries[f"countryList.{index}.registrationAgeLimit"],
                countries[f"countryList.{index}.parentalControlAgeLimit"],
            )
            for index in range(int(countries["countryList.[]"]))
        }
        assert (limits["PL"], limits["DE"]) == (("13", "18"), ("14", "14"))
        terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]

        async def register(nuid: str, age: int, country: str = "PL") -> str:
            form = registration(nuid, "secret-pass", terms, born(age), country)
            answer = await player.request(FESL, "acct", form)
            if "errorCode" not in answer:
                return "registered"
            assert answer["errorContainer.0.fieldName"] == "dob"
            return answer["errorContainer.0.fieldError"]

        # 16 shows the birth date refusal; 15 the same refusal, after which the game disconnects.
        assert await register("teen@example.test", 15) == "registered"
        assert await register("kid.de@example.test", 13, "DE") == "16"
        await cli(capsys, "settings", "set", "bfbc2.registration.under_parental_age", "refuse")
        assert await register("teen.pl@example.test", 15) == "15"
        assert await register("adult@example.test", 18) == "registered"
        await cli(capsys, "settings", "set", "bfbc2.registration.age_check", "off")
        assert await register("kid@example.test", 5) == "registered"


async def test_the_admin_takes_an_account_out_of_use_which_signs_its_player_out(
    prompt_actions: None, server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    persona = [("TXN", "NuLoginPersona"), ("name", "Ann_01")]
    again = login("ann@example.test", "secret-pass")
    async with game(server_url) as ann:
        token = (await log_in_player(ann, FESL, "ann@example.test"))["encryptedLoginInfo"]
        await ann.request(FESL, "acct", [("TXN", "NuAddPersona"), ("name", "Ann_01")])
        lkey = (await ann.request(FESL, "acct", persona))["lkey"]
        await enter_theater(ann, THEATER, "bfbc2-pc", lkey)

        await cli(capsys, "accounts", "state", "ann@example.test", "banned")
        await ann.closed(FESL)
        await ann.closed(THEATER)
        pushed = [
            frame.packet.command if frame.connection == THEATER else text(frame.packet)["TXN"]
            for frame in ann.frames
            if isinstance(frame, Data)
        ]
        # The game names the ban only for Theater's DISC, so it comes before the Goodbye.
        assert pushed.index("DISC") < pushed.index("Goodbye")
        assert (await ann.push("Goodbye"))["reason"] == "4"

    async with game(server_url) as ann:
        await ann.hello(FESL, "bfbc2-pc")
        silent = [("TXN", "NuLogin"), ("returnEncryptedInfo", "0"), ("encryptedInfo", token)]
        assert (await ann.request(FESL, "acct", silent))["errorCode"] == "103"
        for state, code in (("disabled", "102"), ("deactivated", "250")):
            await cli(capsys, "accounts", "state", "ann@example.test", state)
            assert (await ann.request(FESL, "acct", again))["errorCode"] == code
        # Only the right password learns the state.
        wrong = login("ann@example.test", "wrong-pass")
        assert (await ann.request(FESL, "acct", wrong))["errorCode"] == "122"
        await cli(capsys, "accounts", "state", "ann@example.test", "active")
        assert "errorCode" not in await ann.request(FESL, "acct", again)

        lkey = (await ann.request(FESL, "acct", persona))["lkey"]
        await enter_theater(ann, THEATER, "bfbc2-pc", lkey)
        await cli(capsys, "accounts", "state", "ann@example.test", "disabled")
        # Without a ban the game reads only that its authorization failed.
        assert (await ann.push("Goodbye"))["reason"] == "4"
        await ann.closed(THEATER)
        assert not [
            frame
            for frame in ann.frames
            if isinstance(frame, Data) and frame.packet.command == "DISC"
        ]

    until = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    await cli(capsys, "accounts", "state", "ann@example.test", "banned", "--until", until)
    async with game(server_url) as ann:
        await ann.hello(FESL, "bfbc2-pc")
        assert (await ann.request(FESL, "acct", again))["errorCode"] == "103"
        with anyio.fail_after(5):
            while "errorCode" in await ann.request(FESL, "acct", again):
                await anyio.sleep(0.1)

    for args, message in [
        (("nobody@example.test", "banned"), "No player's account"),
        ((SERVER_ACCOUNT, "banned"), "No player's account"),
        (("ann@example.test", "active", "--until", "2999-01-01"), "no end date"),
        # A date that has passed, which argparse refuses with its usage.
        (("ann@example.test", "banned", "--until", "2000-01-01"), "2"),
    ]:
        with pytest.raises(SystemExit, match=message):
            await cli(capsys, "accounts", "state", *args)
