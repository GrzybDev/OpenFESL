import asyncio
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from openfesl.core.fesl import SYSTEM_ERROR, FeslConnection, Route, TransactionError
from openfesl.core.games import Games
from openfesl.protocol.fsys import DomainPartition, Goodbye, Hello, HelloAnswer, MemCheck, Ping
from openfesl.protocol.message import Message

logger = logging.getLogger(__name__)

PING_INTERVAL = 120.0
MEMCHECK_INTERVAL = 300.0
THEATER_HOST = "{}.theater.ea.com"


def connect_routes(games: Games) -> dict[tuple[str, str], Route[Any]]:
    """Return the handlers of the fsys service, which greets the game and keeps it connected.

    Args:
        games: The games the server serves.
    """

    async def hello(connection: FeslConnection, request: Hello) -> HelloAnswer:
        found = games.find(request.client_string)
        if connection.hello is not None or found is None:
            logger.warning("Refusing a FESL Hello from %s", request.client_string)
            raise TransactionError(SYSTEM_ERROR)
        game, client = found
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

    return {
        ("fsys", "Hello"): Route(Hello, hello),
        ("fsys", "Goodbye"): Route(Goodbye, goodbye),
        ("fsys", "MemCheck"): Route(Message, reply),
        ("fsys", "Ping"): Route(Message, reply),
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
