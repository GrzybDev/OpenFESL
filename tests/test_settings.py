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
