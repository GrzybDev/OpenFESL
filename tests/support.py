import asyncio
import base64
import struct
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email.message import Message

from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

from openfesl.protocol.fields import Fields, format_fields, parse_fields
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import (
    SUBPROTOCOL,
    ConnectionKey,
    Data,
    Frame,
    decode_frame,
    encode_frame,
)

type Answer = dict[str, str]


class Game:
    """A game process that talks to the server through one relay WebSocket, as the hook does."""

    def __init__(self, websocket: ClientConnection) -> None:
        self.websocket = websocket
        self.frames: list[Frame] = []
        self._numbers: dict[ConnectionKey, int] = {}

    async def open(self, key: ConnectionKey, host: str, port: int = 18390) -> None:
        body = struct.pack(">HHB", port, 50000 + key.number, len(host)) + host.encode("ascii")
        await self.websocket.send(struct.pack(">BBH", 1, key.channel, key.number) + body)

    async def hello(
        self, key: ConnectionKey, client_string: str, client_type: str = "", locale: str = "en_US"
    ) -> Answer:
        await self.open(key, f"{client_string}.fesl.ea.com")
        return await self.request(
            key,
            "fsys",
            [
                ("TXN", "Hello"),
                ("clientString", client_string),
                ("sku", "PC"),
                ("locale", locale),
                ("clientPlatform", "PC"),
                ("clientVersion", "2.0"),
                ("SDKVersion", "5.1.2.0.0"),
                ("protocolVersion", "2.0"),
                ("fragmentSize", "8096"),
                ("clientType", client_type),
            ],
        )

    async def request(self, key: ConnectionKey, command: str, fields: Fields) -> Answer:
        """Send a request and return its answer, keeping every other frame in `frames`."""
        number = self._numbers.get(key, 0) + 1
        self._numbers[key] = number
        packet = Packet(command, 0xC0, number, format_fields(fields))
        await self.websocket.send(encode_frame(Data(key, packet)))
        pieces: list[bytes] = []
        while True:
            frame = await self.next_frame()
            if (
                not isinstance(frame, Data)
                or frame.connection != key
                or frame.packet.number != number
            ):
                self.frames.append(frame)
            elif frame.packet.kind & 0x20:
                # Joined by decodedSize, as the game joins an answer that came in fragments.
                piece = dict(parse_fields(frame.packet.payload))
                pieces.append(base64.b64decode(piece["data"]))
                if sum(map(len, pieces)) == int(piece["decodedSize"]):
                    return text(Packet(command, 0x80, number, b"".join(pieces)))
            else:
                return text(frame.packet)

    async def push(self, txn: str) -> Answer:
        """Take the first push of a transaction, waiting for it when it has not arrived yet."""
        while True:
            for frame in self.frames:
                if isinstance(frame, Data) and (fields := text(frame.packet)).get("TXN") == txn:
                    self.frames.remove(frame)
                    return fields
            self.frames.append(await self.next_frame())

    def pushed(self, txn: str) -> Answer | None:
        """Return the first push of a transaction among the frames that arrived so far."""
        pushes = (text(frame.packet) for frame in self.frames if isinstance(frame, Data))
        return next((fields for fields in pushes if fields.get("TXN") == txn), None)

    async def next_frame(self) -> Frame:
        return decode_frame(await self.websocket.recv(decode=False))


def text(packet: Packet) -> Answer:
    """Return a packet's fields with their text decoded as UTF-8."""
    return {
        key: value.encode("latin-1").decode("utf-8") for key, value in parse_fields(packet.payload)
    }


def registration(nuid: str, password: str, terms: str, born: str = "1990-05-28") -> Fields:
    year, month, day = born.split("-")
    return [
        ("TXN", "NuAddAccount"),
        ("nuid", nuid),
        ("password", password),
        ("globalOptin", "0"),
        ("thirdPartyOptin", "0"),
        ("parentalEmail", ""),
        ("DOBDay", day),
        ("DOBMonth", month),
        ("DOBYear", year),
        ("zipCode", "00-001"),
        ("country", "PL"),
        ("language", "pl"),
        ("tosVersion", terms),
    ]


def login(nuid: str, password: str, *extra: tuple[str, str]) -> Fields:
    return [
        ("TXN", "NuLogin"),
        ("returnEncryptedInfo", "1"),
        ("nuid", nuid),
        ("password", password),
        ("macAddr", "$0a0b0c0d0e0f"),
        *extra,
    ]


def entitle_game(nuid: str, password: str, key: str) -> Fields:
    return [("TXN", "NuEntitleGame"), ("key", key), ("nuid", nuid), ("password", password)]


async def log_in_player(game: Game, key: ConnectionKey, nuid: str) -> Answer:
    """Register a player with the current terms and log it in on a new FESL connection."""
    await game.hello(key, "bfbc2-pc")
    terms = (await game.request(key, "acct", [("TXN", "NuGetTos")]))["version"]
    await game.request(key, "acct", registration(nuid, "secret-pass", terms))
    await game.request(key, "acct", entitle_game(nuid, "secret-pass", "ACTIVATE-GAME"))
    return await game.request(key, "acct", login(nuid, "secret-pass"))


async def log_in_persona(game: Game, key: ConnectionKey, nuid: str, name: str) -> tuple[int, str]:
    """Log a new player in as a new persona and return the persona's number and session key."""
    await log_in_player(game, key, nuid)
    await game.request(key, "acct", [("TXN", "NuAddPersona"), ("name", name)])
    playing = await game.request(key, "acct", [("TXN", "NuLoginPersona"), ("name", name)])
    return int(playing["userId"]), playing["lkey"]


@asynccontextmanager
async def game(server_url: str) -> AsyncIterator[Game]:
    async with connect(server_url, subprotocols=[Subprotocol(SUBPROTOCOL)]) as websocket:
        yield Game(websocket)


async def fetch(server_url: str, path: str, host: str | None = None) -> tuple[int, Message, bytes]:
    """Send a GET to the server, as the hook forwards the game's HTTP when a host is given."""
    request = urllib.request.Request(server_url.replace("ws://", "http://", 1).rstrip("/") + path)
    if host is not None:
        request.add_header("X-OpenFESL-Host", host)

    def send() -> tuple[int, Message, bytes]:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    return await asyncio.to_thread(send)


def block(owner: int, member: int) -> Fields:
    """Return the AddAssociations that puts a member on its owner's block list."""
    return [
        ("TXN", "AddAssociations"),
        ("type", "PlasmaBlock"),
        ("listFullBehavior", "RollLeastRecentlyModified"),
        ("addRequests.[]", "1"),
        ("addRequests.0.owner.id", str(owner)),
        ("addRequests.0.owner.type", "1"),
        ("addRequests.0.member.id", str(member)),
        ("addRequests.0.member.type", "1"),
        ("addRequests.0.mutual", "0"),
    ]
