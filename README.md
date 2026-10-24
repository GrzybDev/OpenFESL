# OpenFESL

OpenFESL is an open-source master server for PC games built on EA's FESL and Theater online services. It starts with
Battlefield: Bad Company 2 and keeps every game's specifics in game data, so more games can follow. Games reach it
through [OpenFESLHook](https://github.com/GrzybDev/OpenFESLHook), a DLL placed next to the game or its dedicated server.

An original copy of the game is needed; OpenFESL contains no game files.

## What works

For Battlefield: Bad Company 2 on PC, the game and its dedicated server:

- Accounts: registration with the terms the game shows, logins with remembered logins, personas, account details and
  password changes
- Product keys and the entitlements they grant
- Stats, ranked stats and leaderboards, and the records players keep per persona (dog tags, clan tag)
- Friends, block and mute lists, presence, and messages between players, delivered at the next login when the
  recipient is offline
- Theater: lobbies, hosted games, game lists and lookups, joining with queues, and PlayNow
- Dedicated servers identified by `ProviderId.dat`, either approved one by one or all allowed to host
- The game's HTTP: the news and configuration files it downloads, and the locker

## How games connect

OpenFESLHook opens one WebSocket per game process to the server's root path with the subprotocol `openfesl.v1` and
carries the game's FESL, Theater and HTTP traffic over it. The hook's
[relay protocol](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md) describes the frames.
The game's HTTP requests go to the same host over `https://` (or `http://` for a `ws://` address), with the game's
host in the `X-OpenFESL-Host` header.

## Running locally

With Docker:

```sh
docker compose up --build
```

The server listens on `ws://127.0.0.1:8000` with its database next to it. Point the hook at it in `OpenFESLHook.ini`:

```ini
[Server]
Url=ws://127.0.0.1:8000
```

## Configuration

The server reads its settings from the environment.

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | The PostgreSQL database, such as `postgresql://openfesl:password@database/openfesl`; the server connects through asyncpg | required |
| `BEHIND_TRUSTED_PROXY` | `true` when every request comes through a proxy that sets `CLIENT_IP_HEADER`; any client could set the header otherwise | `false` |
| `CLIENT_IP_HEADER` | The header that carries the client's address behind that proxy, such as `CF-Connecting-IP` | none |
| `LOG_LEVEL` | The lowest level logged; `DEBUG` logs every packet with secrets masked | `INFO` |

The database schema is created and upgraded when the server starts.

## Deploying

- Run one container: sessions reach each other in memory, so the server runs as one process with one worker. Stop
  the old container before the new one starts.
- Serve it over TLS (`wss://`) through a proxy that passes WebSocket upgrades and keeps connections open for hours.
  The server pings the hook to find dead connections.
- On shutdown the server closes every session with code 1001, and the hook reconnects when the game does.
- `openfesl health` checks that the server in the container answers; the image uses it as its health check.

## Administration

The `openfesl` command in the server's container manages what the admin decides:

| Command | What it does |
|---|---|
| `openfesl hosting [approved\|open]` | Shows or sets whose games players see: only approved dedicated servers' (the default) or every server's |
| `openfesl providers list` | Lists the providers, the operators of dedicated servers, with their ids |
| `openfesl providers add NAME [--out PATH]` | Adds a provider and writes the `ProviderId.dat` its servers read, next to `Win32Game.cfg` in the server's folder |
| `openfesl providers revoke ID` | Stops every server of a provider from hosting |
| `openfesl servers list` | Lists the dedicated servers that tried to host, with their state and provider |
| `openfesl servers approve UGID` | Lets players see a server's games |
| `openfesl servers revoke UGID` | Stops a server from hosting |
| `openfesl keys import` | Adds the games' product keys that the database lacks, keeping the others |

## Development

Python 3.14 and [uv](https://docs.astral.sh/uv/). The tests start PostgreSQL in Docker through Testcontainers.

```sh
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy
uv run lint-imports
```

## Licence

OpenFESL is licensed under the GNU General Public License v3.0 or later; see [LICENSE.md](LICENSE.md).

OpenFESL is not affiliated with or endorsed by Electronic Arts.
