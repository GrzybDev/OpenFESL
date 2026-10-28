import pytest
from support import FESL, cli, game, launcher_sign_in, log_in_player

from openfesl.core.launcher import LauncherSession, LauncherSessions

pytestmark = pytest.mark.anyio


async def test_a_launcher_session_is_found_until_it_expires() -> None:
    now = [0.0]
    sessions = LauncherSessions(ttl=10.0, clock=lambda: now[0])
    session = LauncherSession(token="a-token", account_name="ann", persona_name="ann")

    session_id = sessions.issue(session)

    assert sessions.find(session_id) == session
    assert sessions.find("made-up") is None
    now[0] = 10.0
    assert sessions.find(session_id) is None


async def test_a_sign_in_is_refused_with_a_reason_the_launcher_words(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    account = "ann@example.test"
    async with game(server_url) as player:
        await log_in_player(player, FESL, account)

    wrong = await launcher_sign_in(server_url, account, "a-wrong-password")
    unknown = await launcher_sign_in(server_url, account, "secret-pass", game="made-up")
    await cli(capsys, "accounts", "state", account, "banned")
    banned = await launcher_sign_in(server_url, account, "secret-pass")

    assert (wrong.status_code, wrong.json()) == (401, {"error": "wrong-sign-in"})
    assert (unknown.status_code, unknown.json()) == (404, {"error": "unknown-game"})
    assert (banned.status_code, banned.json()) == (401, {"error": "banned"})
