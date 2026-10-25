import pytest
from support import cli, game, log_in_player, login

from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
CONCURRENT_LOGIN = "bfbc2.accounts.concurrent_login"


async def test_the_admin_overrides_a_policy_so_that_a_second_login_is_refused(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert f"{CONCURRENT_LOGIN}  replace" in (await cli(capsys, "settings", "list")).splitlines()
    assert await cli(capsys, "settings", "set", CONCURRENT_LOGIN, "refuse") == "refuse\n"
    listed = (await cli(capsys, "settings", "list")).splitlines()
    assert f"{CONCURRENT_LOGIN}  refuse  (default replace)" in listed

    async with game(server_url) as first:
        await log_in_player(first, FESL, "ann@example.test")
        async with game(server_url) as second:
            await second.hello(FESL, "bfbc2-pc")
            again = login("ann@example.test", "secret-pass")
            assert (await second.request(FESL, "acct", again))["errorCode"] == "121"
            # Answered after the refusal, so a Goodbye would have arrived by now.
            assert "errorCode" not in await first.request(FESL, "acct", [("TXN", "NuGetAccount")])
            assert first.pushed("Goodbye") is None
    async with game(server_url) as later:
        await later.hello(FESL, "bfbc2-pc")
        assert "errorCode" not in await later.request(FESL, "acct", again)

    assert await cli(capsys, "settings", "reset", CONCURRENT_LOGIN) == "replace\n"
    assert await cli(capsys, "settings", "get", CONCURRENT_LOGIN) == "replace\n"
    with pytest.raises(SystemExit, match="takes no value"):
        await cli(capsys, "settings", "set", CONCURRENT_LOGIN, "sometimes")
    with pytest.raises(SystemExit, match="No setting is called"):
        await cli(capsys, "settings", "get", "bfbc2.accounts.nothing")


async def hello_error(
    server_url: str, client_string: str, client_type: str = "", *, user_agent: str | None = None
) -> str:
    """Return the error code that a new game's Hello is answered with, 0 when it is greeted.

    Args:
        server_url: The server's WebSocket URL.
        client_string: The game's client string.
        client_type: The game's client type, server for a dedicated server.
        user_agent: The User-Agent the hook names itself with; none is sent when omitted.
    """
    async with game(server_url, user_agent=user_agent) as connecting:
        answer = await connecting.hello(FESL, client_string, client_type)
        return answer.get("errorCode", "0")


async def test_hello_refuses_outdated_clients_and_players_during_maintenance(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    # These games send version 2.0, older than 2.10; the game names both refusals.
    await cli(capsys, "settings", "set", "bfbc2.connect.minimum_client_version", "2.10")
    assert await hello_error(server_url, "bfbc2-pc") == "-254"
    assert await hello_error(server_url, "bfbc2-pc-server", "server") == "0"
    await cli(capsys, "settings", "set", "bfbc2.connect.minimum_client_version", "2.0")
    assert await hello_error(server_url, "bfbc2-pc") == "0"
    await cli(capsys, "settings", "set", "bfbc2.connect.maintenance", "on")
    assert await hello_error(server_url, "bfbc2-pc") == "-253"
    assert await hello_error(server_url, "bfbc2-pc-server", "server") == "0"
    await cli(capsys, "settings", "reset", "bfbc2.connect.maintenance")
    assert await hello_error(server_url, "bfbc2-pc") == "0"
    with pytest.raises(SystemExit, match="takes no value"):
        await cli(capsys, "settings", "set", "bfbc2.connect.minimum_client_version", "newest")


async def test_hello_refuses_outdated_hooks_of_players_and_dedicated_servers(
    server_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    await cli(capsys, "settings", "set", "bfbc2.connect.minimum_hook_version", "0.2.0")
    old = "OpenFESLHook/0.1.0 (Windows)"
    assert await hello_error(server_url, "bfbc2-pc", user_agent=old) == "-254"
    assert await hello_error(server_url, "bfbc2-pc-server", "server", user_agent=old) == "-254"
    assert await hello_error(server_url, "bfbc2-pc") == "-254"
    pre_release = "OpenFESLHook/0.2.0-dev (Windows)"
    assert await hello_error(server_url, "bfbc2-pc", user_agent=pre_release) == "-254"
    current = "OpenFESLHook/0.2.0 (Windows)"
    assert await hello_error(server_url, "bfbc2-pc", user_agent=current) == "0"
