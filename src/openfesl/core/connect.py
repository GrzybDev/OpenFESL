import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from openfesl.core.fesl import SYSTEM_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Games
from openfesl.core.policies import GamePolicies
from openfesl.protocol.fsys import (
    DomainPartition,
    GetPingSitesAnswer,
    Goodbye,
    Hello,
    HelloAnswer,
    MemCheck,
    Ping,
)
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

# The Hello errors the game names: its notices that the service is busy and that it needs an
# update. Any other code shows its generic connection failure.
BUSY = -253
UPDATE_REQUIRED = -254

PING_INTERVAL = 120.0
MEMCHECK_INTERVAL = 300.0
THEATER_HOST = "{}.theater.ea.com"


def connect_routes(games: Games, policies: GamePolicies) -> dict[tuple[str, str], Route[Any]]:
    """Return the handlers of the fsys service, which greets the game and keeps it connected.

    Args:
        games: The games the server serves.
        policies: Each game's policies, which say which clients may connect.
    """

    async def hello(connection: FeslConnection, request: Hello) -> HelloAnswer:
        found = games.find(request.client_string)
        if connection.hello is not None or found is None:
            logger.warning("Refusing a FESL Hello from %s", request.client_string)
            raise TransactionError(SYSTEM_ERROR)
        game, client = found
        allowed = (await policies.of(game)).connect
        # Dedicated servers have versions of their own and retry without end when refused.
        if not client.server_account:
            if allowed.maintenance:
                logger.info("Refusing a FESL Hello from %s: maintenance", request.client_string)
                raise TransactionError(BUSY)
            if allowed.outdated_client(request.client_version):
                logger.info(
                    "Refusing a FESL Hello from %s %s: outdated",
                    request.client_string,
                    request.client_version,
                )
                raise TransactionError(UPDATE_REQUIRED)
        # Dedicated servers run the same hook, so an outdated one keeps them out too.
        hook_version = connection.session.hook_version
        if allowed.outdated_hook(hook_version):
            logger.info(
                "Refusing a FESL Hello from %s: hook version %s, at least %s required",
                request.client_string,
                hook_version or "missing",
                allowed.minimum_hook_version,
            )
            raise TransactionError(UPDATE_REQUIRED)
        connection.greet(request, game, client)
        logger.info(
            "FESL Hello from %s %s (%s)",
            request.client_string,
            request.client_version,
            request.client_type or "client",
        )
        _keep_alive(connection)
        return HelloAnswer(
            domain_partition=DomainPartition(
                domain=game.partition.domain, sub_domain=game.partition.sub_domain
            ),
            activity_timeout_secs=0,
            cur_time=datetime.now(UTC),
            theater_ip=THEATER_HOST.format(request.client_string),
            theater_port=client.theater_port,
        )

    async def goodbye(connection: FeslConnection, request: Goodbye) -> None:
        logger.info("FESL Goodbye: %s %s", request.reason, request.message)

    async def reply(connection: FeslConnection, request: Message) -> None:
        pass

    async def ping_sites(connection: FeslConnection, request: Message) -> GetPingSitesAnswer:
        # The browser would ping each site's address itself; with none it finishes at once.
        return GetPingSitesAnswer()

    async def acknowledge(connection: FeslConnection, request: Message) -> Message:
        return Message()

    return {
        ("fsys", "Hello"): Route(Hello, hello),
        ("fsys", "Goodbye"): Route(Goodbye, goodbye),
        ("fsys", "MemCheck"): Route(Message, reply),
        ("fsys", "Ping"): Route(Message, reply),
        ("fsys", "GetPingSites"): Route(Message, ping_sites),
        # The SDK reads nothing from the answer and no flow of it sends this.
        ("fsys", "Suicide"): Route(Message, acknowledge),
    }


def _keep_alive(connection: FeslConnection) -> None:
    # The game sends nothing until it has a MemCheck, and closes a connection that stays silent
    # for its activity timeout, so both go out on timers; the first MemCheck right away.
    def memcheck() -> None:
        connection.push("fsys", "MemCheck", MemCheck(salt=secrets.randbits(32)))

    def ping() -> None:
        connection.push("fsys", "Ping", Ping())

    for task in (
        asyncio.create_task(_every(MEMCHECK_INTERVAL, memcheck, now=True)),
        asyncio.create_task(_every(PING_INTERVAL, ping, now=False)),
    ):
        connection.on_close(task.cancel)


async def _every(interval: float, send: Callable[[], None], *, now: bool) -> None:
    if now:
        send()
    while True:
        await asyncio.sleep(interval)
        send()
