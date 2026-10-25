import asyncio
import itertools
import logging
import re
from collections.abc import Awaitable, Callable

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect, WebSocketState

from openfesl.core.connection import Connection, Session
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import (
    SUBPROTOCOL,
    Close,
    ConnectionKey,
    Data,
    FrameError,
    Open,
    decode_frame,
    encode_frame,
)

logger = logging.getLogger(__name__)

SEND_LIMIT = 1024
RECEIVE_LIMIT = 256

_PROTOCOL_ERROR = 1002
_UNSUPPORTED_DATA = 1003
_POLICY_VIOLATION = 1008

_session_numbers = itertools.count(1)
_links: set[_Link] = set()

# How the hook names itself in the handshake, such as OpenFESLHook/0.1.0 (Windows).
_HOOK_AGENT = re.compile(r"OpenFESLHook/(\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?)(?:\s|$)")

type SessionStarter = Callable[[WebSocket, str | None], Session]


async def close_sessions(code: int, timeout: float = 5) -> None:
    """End every open relay session with a close code, such as 1001 when the server stops.

    Args:
        code: The WebSocket close code.
        timeout: How long to wait for the sessions to end, in seconds.
    """
    links = list(_links)
    for link in links:
        link.end(code, "the server is stopping")
    if links:
        await asyncio.wait(
            [asyncio.create_task(link.ended.wait()) for link in links], timeout=timeout
        )


def relay_endpoint(start_session: SessionStarter) -> Callable[[WebSocket], Awaitable[None]]:
    """Return the WebSocket endpoint that OpenFESLHook connects to.

    Args:
        start_session: Creates the core's session for an accepted WebSocket, given the
            version of the hook that opened it, when its User-Agent names one.
    """

    async def endpoint(websocket: WebSocket) -> None:
        if SUBPROTOCOL not in websocket.scope.get("subprotocols", []):
            # Closing before the handshake makes the server answer 403.
            await websocket.close(_PROTOCOL_ERROR)
            return
        await websocket.accept(SUBPROTOCOL)
        agent = _HOOK_AGENT.match(websocket.headers.get("user-agent", ""))
        session = start_session(websocket, agent[1] if agent else None)
        await _Link(websocket, session).run()

    return endpoint


class _Inbound:
    """The packets of one game connection waiting for the core, handled in order by one task."""

    def __init__(self) -> None:
        self.packets: asyncio.Queue[Packet | None] = asyncio.Queue()

    async def serve(self, link: _Link, key: ConnectionKey, connection: Connection) -> None:
        """Hand the packets to the core until the connection closes.

        Args:
            link: The session the connection belongs to.
            key: The connection.
            connection: The core's side of it.
        """
        try:
            while (packet := await self.packets.get()) is not None:
                await connection.receive(packet)
        except Exception:
            logger.exception("Session %d: closing %s after an error", link.number, _name(key))
            link.close_connection(key)
        finally:
            await connection.close()


class _Outbox:
    """The core's way back to one game connection."""

    def __init__(self, link: _Link, key: ConnectionKey) -> None:
        self._link = link
        self._key = key

    def send(self, packet: Packet) -> None:
        self._link.send(Data(self._key, packet))

    async def send_paced(self, packet: Packet) -> None:
        await self._link.send_paced(Data(self._key, packet))

    def close(self) -> None:
        self._link.close_connection(self._key)

    @property
    def busy(self) -> bool:
        return self._link.busy


class _Link:
    """One OpenFESLHook WebSocket, its game connections and the frames waiting to go out."""

    def __init__(self, websocket: WebSocket, session: Session) -> None:
        self.number = next(_session_numbers)
        self.inbound: dict[ConnectionKey, _Inbound] = {}
        self._serving: set[asyncio.Task[None]] = set()
        self._websocket = websocket
        self._session = session
        self._outgoing: asyncio.Queue[Data | Close] = asyncio.Queue(SEND_LIMIT)
        self._dequeued = asyncio.Event()
        self._failure: tuple[int, str] | None = None
        self._failed = asyncio.Event()
        self.ended = asyncio.Event()

    async def run(self) -> None:
        """Relay frames until the hook or the server ends the session."""
        logger.info("Session %d: started", self.number)
        _links.add(self)
        reader = asyncio.create_task(self._read())
        writer = asyncio.create_task(self._write())
        failed = asyncio.create_task(self._failed.wait())
        try:
            await asyncio.wait((reader, writer, failed), return_when=asyncio.FIRST_COMPLETED)
            if self._failure and self._websocket.client_state is WebSocketState.CONNECTED:
                code, reason = self._failure
                logger.warning("Session %d: ending it, %s", self.number, reason)
                await self._websocket.close(code)
        finally:
            tasks = [reader, writer, failed, *self._serving]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for task in (reader, writer):
                error = None if task.cancelled() else task.exception()
                if error and not isinstance(error, WebSocketDisconnect | OSError):
                    logger.error("Session %d: relay failed", self.number, exc_info=error)
            await self._session.end()
            _links.discard(self)
            self.ended.set()
            logger.info("Session %d: ended", self.number)

    @property
    def busy(self) -> bool:
        """Whether at least half of the queue waits for the hook."""
        return self._outgoing.qsize() >= self._outgoing.maxsize // 2

    def send(self, frame: Data | Close) -> None:
        """Queue a frame for the hook, or end the session when the hook falls too far behind.

        Args:
            frame: A packet for a game connection, or the request to close it.
        """
        try:
            self._outgoing.put_nowait(frame)
        except asyncio.QueueFull:
            self._fail(_POLICY_VIOLATION, f"{SEND_LIMIT} frames are waiting for the hook")

    async def send_paced(self, frame: Data) -> None:
        """Queue a frame of a long burst, such as a game list, once at most half of the queue waits.

        The other half stays free for every other frame, which never waits.

        Args:
            frame: A packet for a game connection.
        """
        while self.busy:
            self._dequeued.clear()
            await self._dequeued.wait()
        self.send(frame)

    def end(self, code: int, reason: str) -> None:
        """Close the WebSocket with a code, ending the session.

        Args:
            code: The WebSocket close code.
            reason: Why, for the log.
        """
        self._fail(code, reason)

    def close_connection(self, key: ConnectionKey) -> None:
        """Stop handling a game connection after its queued packets, and ask the hook to close it.

        Args:
            key: The game connection.
        """
        if inbound := self.inbound.pop(key, None):
            inbound.packets.put_nowait(None)
            self.send(Close(key))

    async def _read(self) -> None:
        while True:
            message = await self._websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if (data := message.get("bytes")) is None:
                self._fail(_UNSUPPORTED_DATA, "the hook sent a text message")
                return
            try:
                frame = decode_frame(data)
            except FrameError as error:
                self._fail(_PROTOCOL_ERROR, str(error))
                return
            self._dispatch(frame)
            if self._failure:
                return

    def _dispatch(self, frame: Open | Data | Close) -> None:
        match frame:
            case Open(key):
                if key in self.inbound:
                    self._fail(_PROTOCOL_ERROR, f"{_name(key)} was opened twice")
                    return
                logger.info(
                    "Session %d: %s opened to %s:%d from port %d",
                    self.number,
                    _name(key),
                    frame.host,
                    frame.port,
                    frame.game_port,
                )
                # Known before the core sees it, so the core may close it at once.
                incoming = self.inbound[key] = _Inbound()
                connection = self._session.open(frame, _Outbox(self, key))
                task = asyncio.create_task(incoming.serve(self, key, connection))
                self._serving.add(task)
                task.add_done_callback(self._serving.discard)
            case Data(key, packet):
                inbound = self.inbound.get(key)
                if inbound is None:
                    # The server may have closed the connection while the game was still sending.
                    logger.debug("Session %d: dropped data for closed %s", self.number, _name(key))
                elif inbound.packets.qsize() >= RECEIVE_LIMIT:
                    self._fail(_POLICY_VIOLATION, f"{RECEIVE_LIMIT} packets wait on {_name(key)}")
                else:
                    inbound.packets.put_nowait(packet)
            case Close(key):
                if inbound := self.inbound.pop(key, None):
                    logger.info("Session %d: %s closed by the game", self.number, _name(key))
                    inbound.packets.put_nowait(None)

    async def _write(self) -> None:
        while True:
            frame = await self._outgoing.get()
            self._dequeued.set()
            await self._websocket.send_bytes(encode_frame(frame))

    def _fail(self, code: int, reason: str) -> None:
        if self._failure is None:
            self._failure = (code, reason)
            self._failed.set()


def _name(key: ConnectionKey) -> str:
    return f"{key.channel.name.lower()} connection {key.number}"
