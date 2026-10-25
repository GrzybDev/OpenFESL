import asyncio
import re
from collections.abc import AsyncIterator
from datetime import timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage

import anyio
import pytest
from aiosmtpd.smtp import SMTP, Envelope, Session
from support import Game, cli, fetch, game, login, registration

from openfesl.core import verification
from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)
PUBLIC_URL = "https://openfesl.example.test"


class Mailbox:
    """A mail server that keeps what it is sent."""

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []
        # Cleared, the mail server holds what it is sent, as a slow one does.
        self.answering = asyncio.Event()
        self.answering.set()

    async def handle_DATA(self, server: SMTP, session: Session, envelope: Envelope) -> str:
        await self.answering.wait()
        assert isinstance(envelope.content, bytes)
        message = message_from_bytes(envelope.content, policy=policy.default)
        assert isinstance(message, EmailMessage)
        self.messages.append(message)
        return "250 OK"

    async def receive(self, to: str) -> tuple[EmailMessage, str]:
        """Wait for the next message to an address and return it with the path of its link."""
        with anyio.fail_after(5):
            while not self.messages:
                await anyio.sleep(0.02)
        message = self.messages.pop(0)
        assert message["To"] == to
        found = re.search(re.escape(PUBLIC_URL) + r"(/(?:pl/)?verify/\S+)", message.get_content())
        assert found is not None
        return message, found[1]


@pytest.fixture
async def mailbox(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Mailbox]:
    # Before the server starts, which reads its mail server from the environment.
    box = Mailbox()
    loop = asyncio.get_running_loop()
    server = await loop.create_server(lambda: SMTP(box), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    environment = {
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(port),
        "SMTP_SECURITY": "none",
        "SMTP_SENDER": "openfesl@example.test",
        "PUBLIC_URL": PUBLIC_URL,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    yield box
    server.close()
    await server.wait_closed()


async def register(player: Game, nuid: str) -> dict[str, str]:
    terms = (await player.request(FESL, "acct", [("TXN", "NuGetTos")]))["version"]
    return await player.request(FESL, "acct", registration(nuid, "secret-pass", terms))


async def log_in(player: Game, nuid: str) -> str:
    return (await player.request(FESL, "acct", login(nuid, "secret-pass")))["errorCode"]


async def test_a_new_account_logs_in_once_the_link_sent_to_its_address_confirmed_it(
    mailbox: Mailbox,
    server_url: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await cli(capsys, "settings", "set", "bfbc2.accounts.email_verification", "on")
    async with game(server_url) as player:
        await player.hello(FESL, "bfbc2-pc", locale="pl_PL")
        refused = await register(player, "not-an-address")
        assert refused["errorContainer.0.fieldName"] == "email"

        # The game gives up after 20 s, so its answer does not wait for the mail server.
        mailbox.answering.clear()
        with anyio.fail_after(5):
            assert "errorCode" not in await register(player, "ann@example.test")
        mailbox.answering.set()
        mail, link = await mailbox.receive("ann@example.test")
        # In the language of the game it registered from, as is the page its link opens.
        assert mail["Content-Language"] == "pl"
        assert link.startswith("/pl/")
        # The game shows that the account waits for authorization.
        assert await log_in(player, "ann@example.test") == "105"
        assert (await fetch(server_url, link))[0] == 200
        # On to the game's key prompt, as for any new account.
        assert await log_in(player, "ann@example.test") == "120"
        assert (await fetch(server_url, link))[0] == 404
        # A game in a language the texts lack gets the whole mail in English.
        async with game(server_url) as other:
            await other.hello(FESL, "bfbc2-pc", locale="de_DE")
            assert "errorCode" not in await register(other, "zoe@example.test")
            mail, _ = await mailbox.receive("zoe@example.test")
            assert mail["Content-Language"] == "en"

        await register(player, "bob@example.test")
        await mailbox.receive("bob@example.test")
        await cli(capsys, "accounts", "verify", "bob@example.test")
        assert await log_in(player, "bob@example.test") == "120"

        # A link that expired leaves the account waiting until a login sends a new one.
        monkeypatch.setattr(verification, "VALID_FOR", timedelta(0))
        await register(player, "eve@example.test")
        _, expired = await mailbox.receive("eve@example.test")
        monkeypatch.setattr(verification, "VALID_FOR", timedelta(days=1))
        assert (await fetch(server_url, expired))[0] == 410
        assert await log_in(player, "eve@example.test") == "105"
        _, renewed = await mailbox.receive("eve@example.test")
        assert await log_in(player, "eve@example.test") == "105"
        assert (await fetch(server_url, renewed))[0] == 200
        assert await log_in(player, "eve@example.test") == "120"

        # Without the policy no mail goes out, and accounts still waiting may log in.
        await register(player, "dan@example.test")
        await mailbox.receive("dan@example.test")
        await cli(capsys, "settings", "reset", "bfbc2.accounts.email_verification")
        assert await log_in(player, "dan@example.test") == "120"
        assert "errorCode" not in await register(player, "kim@example.test")
        assert await log_in(player, "kim@example.test") == "120"
    assert not mailbox.messages
