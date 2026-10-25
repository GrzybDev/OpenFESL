import asyncio
import base64
import re
import struct
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.message import Message

import anyio
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.typing import Subprotocol

from openfesl.protocol.fields import Fields, format_fields, parse_fields
from openfesl.protocol.packet import Packet
from openfesl.protocol.relay import (
    SUBPROTOCOL,
    Channel,
    Close,
    ConnectionKey,
    Data,
    Frame,
    decode_frame,
    encode_frame,
)
from openfesl.server.cli import main

type Answer = dict[str, str]
type Update = tuple[int, str, str, str, str]

FESL = ConnectionKey(Channel.FESL, 1)
THEATER = ConnectionKey(Channel.THEATER, 1)

# What the R34 dedicated server sends: no lobby, no attributes, empty UGID and SECRET.
CREATE_GAME = [
    ("LID", "-1"),
    ("RESERVE-HOST", "0"),
    ("NAME", "Invented Server #1"),
    ("PORT", "19567"),
    ("HTTYPE", "A"),
    ("TYPE", "G"),
    ("QLEN", "0"),
    ("DISABLE-AUTO-DEQUEUE", "0"),
    ("HXFR", "0"),
    ("INT-PORT", "19567"),
    ("INT-IP", "192.168.1.10"),
    ("MAX-PLAYERS", "32"),
    ("B-maxObservers", "0"),
    ("B-numObservers", "0"),
    ("UGID", ""),
    ("SECRET", ""),
    ("B-version", "ROMEPC795745"),
    ("JOIN", "O"),
]


@dataclass(frozen=True)
class TheaterAnswer:
    """A Theater packet's fields and the error code its header carries, if any."""

    error: str | None
    fields: Answer


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

    async def close(self, key: ConnectionKey) -> None:
        await self.websocket.send(struct.pack(">BBH", 3, key.channel, key.number))

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

    async def theater(
        self, key: ConnectionKey, command: str, fields: Sequence[tuple[str, str]] = ()
    ) -> TheaterAnswer:
        """Send a Theater request with the connection's next TID and return its answer."""
        tid = self._numbers.get(key, 0) + 1
        self._numbers[key] = tid
        await self.send_theater(key, command, [*fields, ("TID", str(tid))], kind=0x40)
        while True:
            frame = await self.next_frame()
            if (
                isinstance(frame, Data)
                and frame.connection == key
                and frame.packet.command == command
                and text(frame.packet).get("TID") == str(tid)
            ):
                return theater_answer(frame.packet)
            self.frames.append(frame)

    async def send_theater(
        self, key: ConnectionKey, command: str, fields: Fields, kind: int = 0
    ) -> None:
        """Send a Theater packet that waits for no answer, as the game sends UGAM or ECHO."""
        packet = Packet(command, kind, 0, format_fields(fields))
        await self.websocket.send(encode_frame(Data(key, packet)))

    async def theater_push(self, key: ConnectionKey, command: str) -> TheaterAnswer:
        """Take the first Theater packet of a command on a connection, waiting for it."""
        while True:
            for frame in self.frames:
                if (
                    isinstance(frame, Data)
                    and frame.connection == key
                    and frame.packet.command == command
                ):
                    self.frames.remove(frame)
                    return theater_answer(frame.packet)
            self.frames.append(await self.next_frame())

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

    async def closed(self, key: ConnectionKey) -> None:
        """Wait until the server closes one of the game's connections."""
        with anyio.fail_after(5):
            while not [
                frame
                for frame in self.frames
                if isinstance(frame, Close) and frame.connection == key
            ]:
                self.frames.append(await self.next_frame())


async def cli(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    """Run an admin command as the admin does in another process, returning what it printed."""
    await anyio.to_thread.run_sync(main, list(args))
    return capsys.readouterr().out


def text(packet: Packet) -> Answer:
    """Return a packet's fields with their text decoded as UTF-8."""
    return {
        key: value.encode("latin-1").decode("utf-8") for key, value in parse_fields(packet.payload)
    }


def theater_answer(packet: Packet) -> TheaterAnswer:
    code = (packet.kind << 24 | packet.number).to_bytes(4, "big")
    return TheaterAnswer(code.decode("ascii") if any(code) else None, text(packet))


def registration(
    nuid: str, password: str, terms: str, born: str = "1990-05-28", country: str = "PL"
) -> Fields:
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
        ("country", country),
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


async def log_in_again(game: Game, key: ConnectionKey, nuid: str, name: str) -> str:
    """Log a player that registered earlier in again as one of its personas, returning its key."""
    await game.hello(key, "bfbc2-pc")
    await game.request(key, "acct", login(nuid, "secret-pass"))
    playing = await game.request(key, "acct", [("TXN", "NuLoginPersona"), ("name", name)])
    return playing["lkey"]


async def log_in_server(game: Game, key: ConnectionKey) -> str:
    """Log a dedicated server in to FESL with its persona and return the persona's key."""
    await game.hello(key, "bfbc2-pc-server", client_type="server")
    await game.request(key, "acct", login("bfbc2.server.pc@ea.com", "from-game-data"))
    persona = [("TXN", "NuLoginPersona"), ("name", "bfbc2.server.p")]
    return (await game.request(key, "acct", persona))["lkey"]


async def enter_theater(game: Game, key: ConnectionKey, client_string: str, lkey: str) -> None:
    """Open a Theater connection and log in with a persona key, as the game's SDK does."""
    await game.open(key, f"{client_string}.theater.ea.com", port=18326)
    connect = [("PROT", "2"), ("PROD", client_string), ("PLAT", "PC")]
    connected = await game.theater(key, "CONN", connect)
    assert connected.error is None
    assert (await game.theater(key, "USER", [("LKEY", lkey), ("NAME", "")])).error is None


async def host_game(server: Game, changes: Mapping[str, str] | None = None) -> Answer:
    """Log a dedicated server in to Theater and list CREATE_GAME with some fields changed."""
    await enter_theater(server, THEATER, "bfbc2-pc-server", await log_in_server(server, FESL))
    sent = {**dict(CREATE_GAME), **(changes or {})}
    created = await server.theater(THEATER, "CGAM", list(sent.items()))
    assert created.error is None
    return created.fields


async def enter_as_player(player: Game, nuid: str, name: str) -> None:
    """Register a player with a persona and log it in to Theater."""
    _, lkey = await log_in_persona(player, FESL, nuid, name)
    await enter_theater(player, THEATER, "bfbc2-pc", lkey)


# What a player's game sends about itself: its external port and its LAN address.
JOIN = [("PORT", "50111"), ("R-INT-IP", "192.168.1.20"), ("R-INT-PORT", "3659"), ("PTYPE", "P")]


def holds(fields: Answer, expected: Answer) -> bool:
    return fields.items() >= expected.items()


def ids(created: Answer) -> list[tuple[str, str]]:
    """Return the LID and GID fields that name a game the host listed."""
    return [("LID", created["LID"]), ("GID", created["GID"])]


async def asked_about(server: Game) -> str:
    """Wait for the host to be asked about a player and return the player's PID."""
    return (await server.theater_push(THEATER, "EGRQ")).fields["PID"]


async def let_in(server: Game, player: Game, created: Answer) -> str:
    """Join a player to a game that has room, the host allowing it, and report it entered."""
    assert (await player.theater(THEATER, "EGAM", [*ids(created), *JOIN])).error is None
    pid = await asked_about(server)
    allowed = [*ids(created), ("ALLOWED", "1"), ("PID", pid)]
    assert (await server.theater(THEATER, "EGRS", allowed)).error is None
    assert (await player.theater_push(THEATER, "EGEG")).error is None
    assert (await server.theater(THEATER, "PENT", [*ids(created), ("PID", pid)])).error is None
    return pid


@asynccontextmanager
async def game(
    server_url: str, headers: Mapping[str, str] | None = None, *, user_agent: str | None = None
) -> AsyncIterator[Game]:
    """Connect a game process to the server through a relay WebSocket, as the hook does.

    Args:
        server_url: The server's WebSocket URL.
        headers: Further handshake headers, such as the address a proxy forwards.
        user_agent: The User-Agent the hook names itself with; none is sent when omitted.
    """
    async with connect(
        server_url,
        subprotocols=[Subprotocol(SUBPROTOCOL)],
        additional_headers=headers,
        user_agent_header=user_agent,
    ) as websocket:
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


def markup(body: bytes) -> str:
    """Return a page's markup without the whitespace that its templates' layout adds.

    Args:
        body: The page.
    """
    # Only ASCII whitespace: a no-break space belongs to the page's text.
    text = re.sub(r"\s+", " ", body.decode(), flags=re.ASCII)
    return re.sub(r"\s*([<>])\s*", r"\1", text, flags=re.ASCII)


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


def update_stats(*owners: tuple[str, str, list[Update]]) -> Fields:
    """Return an UpdateStats as a dedicated server writes it.

    Each owner comes as its id, its type and its updates: update type, key, value, text and
    periods. No id stands for the session's persona.
    """
    fields: Fields = [("TXN", "UpdateStats")]
    for number, (owner, owner_type, updates) in enumerate(owners):
        if owner:
            fields += [(f"u.{number}.o", owner), (f"u.{number}.ot", owner_type)]
        for index, (kind, key, value, text, periods) in enumerate(updates):
            prefix = f"u.{number}.s.{index}."
            fields += [
                (prefix + "ut", str(kind)),
                (prefix + "k", key),
                (prefix + "v", value),
                (prefix + "t", text),
                (prefix + "pt", periods),
            ]
        fields.append((f"u.{number}.s.[]", str(len(updates))))
    return [*fields, ("u.[]", str(len(owners)))]
