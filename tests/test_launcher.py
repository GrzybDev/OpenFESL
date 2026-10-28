from typing import TYPE_CHECKING, cast

import pytest
from accounts import execute
from support import FESL, cli, game, launcher_sign_in, log_in_player

from openfesl.core.accounts import Account, AccountState
from openfesl.core.launcher import LauncherSession, LauncherSessions
from openfesl.core.personas import Persona
from openfesl.core.session import GameSession, Players

if TYPE_CHECKING:
    from openfesl.core.rich_presence import RichPresence
    from openfesl.core.theater import Theater

pytestmark = pytest.mark.anyio


async def test_a_launcher_session_is_kept_while_its_account_plays_and_a_ttl_after_use() -> None:
    now = [0.0]
    players = Players()
    sessions = LauncherSessions(players, ttl=10.0, clock=lambda: now[0])
    session = LauncherSession(token="a-token", account_id=1, account_name="ann", persona_name="ann")
    # A dedicated server logged in with the account, which reads Magma only when players join.
    server = GameSession({}, players, None, cast("Theater", None), None, cast("RichPresence", None))
    server.account = Account(
        id=1,
        nuid="ann",
        country="PL",
        terms_version=None,
        server=False,
        state=AccountState.ACTIVE,
        state_until=None,
        state_changed=None,
        verification_expires_at=None,
        display_name=None,
        made_in=None,
        session_epoch=0,
    )
    session_id = sessions.issue(session)

    def find_at(moment: float) -> LauncherSession | None:
        now[0] = moment
        return sessions.find(session_id)

    used = [find_at(9.0), find_at(18.0)]
    players.log_in(server)
    playing = find_at(1000.0)
    players.log_out(server)
    left = [find_at(1009.0), find_at(1019.0)]
    # An idle server that loses its connection after a day without a Magma read logs in anew.
    idle_id = sessions.issue(session)
    server.persona = Persona(id=7, account_id=1, namespace="bfwest", name="ann")
    players.log_in(server)
    players.play(server)
    now[0] = 5000.0
    players.leave(server, server.persona)
    players.log_out(server)
    reconnect = sessions.find(idle_id)
    now[0] = 5011.0
    gone = sessions.find(idle_id)

    assert used == [session, session]
    assert playing == session
    assert left == [session, None]
    assert (reconnect, gone) == (session, None)
    assert sessions.find("made-up") is None


async def test_a_sign_in_is_refused_with_a_reason_the_launcher_words(
    server_url: str, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    account = "ann@example.test"
    async with game(server_url) as player:
        await log_in_player(player, FESL, account)

    wrong = await launcher_sign_in(server_url, account, "a-wrong-password")
    unknown = await launcher_sign_in(server_url, account, "secret-pass", game="made-up")
    # A persona without the faction and class of a hero made on the site, as an earlier
    # server made them: the game cannot play it, while a server it hosts logs in as it.
    await execute(
        database_url,
        "INSERT INTO persona (account_id, namespace, name)"
        " SELECT id, 'bfwest', 'Ann_bare' FROM account WHERE nuid = :account",
        account=account,
    )
    no_hero = await launcher_sign_in(server_url, account, "secret-pass")
    hosting = await launcher_sign_in(server_url, account, "secret-pass", role="host")
    await cli(capsys, "accounts", "state", account, "banned")
    banned = await launcher_sign_in(server_url, account, "secret-pass")

    assert (wrong.status_code, wrong.json()) == (401, {"error": "wrong-sign-in"})
    assert (unknown.status_code, unknown.json()) == (404, {"error": "unknown-game"})
    assert (no_hero.status_code, no_hero.json()) == (401, {"error": "no-hero"})
    assert hosting.json()["personaName"] == "Ann_bare"
    assert (banned.status_code, banned.json()) == (401, {"error": "banned"})
