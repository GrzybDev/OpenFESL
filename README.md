# OpenFESL

OpenFESL is an open-source master server for PC games built on EA's FESL and Theater online services. It starts with
Battlefield: Bad Company 2 and keeps every game's specifics in game data, so more games can follow. Games reach it
through [OpenFESLHook](https://github.com/GrzybDev/OpenFESLHook), a DLL placed next to the game or its dedicated server.

An original copy of the game is needed; OpenFESL contains no game files.

## What works

For Battlefield: Bad Company 2 on PC, the game and its dedicated server:

- Accounts: registration with the terms the game shows and the countries' age limits, optional e-mail verification,
  logins with remembered logins, personas, account details and password changes
- Product keys, optionally limited in uses, and the entitlements they grant
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

The server listens on `ws://127.0.0.1:8000` with its database next to it and serves its web pages at
`http://127.0.0.1:8000`. Point the hook at it in `OpenFESLHook.ini`:

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
| `SMTP_HOST` | The mail server that sends new accounts the link confirming their address; without one no mail goes out | none |
| `SMTP_PORT` | The mail server's port | `587` |
| `SMTP_SECURITY` | `starttls` upgrades a plain connection, `tls` encrypts from the start (usually port 465), `none` leaves it plain | `starttls` |
| `SMTP_USER` | The name to log in to the mail server with, if it needs a login | none |
| `SMTP_PASSWORD` | The password of that login | none |
| `SMTP_SENDER` | The address mail comes from; required for mail to go out | none |
| `PUBLIC_URL` | The address the server's web pages are reached at, where the links in mail lead, such as `https://fesl.example.com`; required for mail to go out. The guides to playing and hosting give the relay address that follows from it, such as `wss://fesl.example.com`, or the request's own without it | none |
| `HOOK_DEFAULT_URL` | The relay address OpenFESLHook's releases connect to without an `OpenFESLHook.ini`; where this server's relay address differs, the guides show the lines of that file | `wss://fesl.grzyb.app` |
| `CONTACT_EMAIL` | The address at which the operators of dedicated servers reach this server's administrators, such as for a provider file; the guide to hosting shows it | none |

The database schema is created and upgraded when the server starts.

## Deploying

- Run one container: sessions reach each other in memory, so the server runs as one process with one worker. Stop
  the old container before the new one starts.
- Serve it over TLS (`wss://`) through a proxy that passes WebSocket upgrades and keeps connections open for hours.
  The server pings the hook to find dead connections.
- On shutdown the server closes every session with code 1001, and the hook reconnects when the game does.
- The image checks its health with `python -m openfesl.server.health`, which loads only the standard library so
  it answers within its timeout on small machines such as a Raspberry Pi.

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
| `openfesl keys limit CODE [--max-uses N \| --unlimited] [--once-per-account \| --any]` | Shows or changes how often a product key may be redeemed: by how many accounts, and whether an account that redeemed it is refused when it enters it again (otherwise that grants nothing new). At the login's key prompt the game names a used-up key as already in use and one the account redeemed as already entitled; the menu shows its notice of a failed redeem. Keys start unlimited |
| `openfesl settings list` | Lists every game's policies with their values |
| `openfesl settings get KEY` | Shows a policy's value; the key names the game and the policy, such as `bfbc2.accounts.concurrent_login` |
| `openfesl settings set KEY VALUE` | Overrides the game's default for this server |
| `openfesl settings reset KEY` | Returns a policy to the game's default |
| `openfesl accounts state LOGIN active\|disabled\|banned\|deactivated [--until DATE]` | Sets whether an account may log in; the game's login form names the state, and a player online is signed out, with the game's ban notice for a ban, and leaves the games it plays in. `--until` ends the state at a date such as `2026-12-01` or `2026-12-01T18:00+01:00`, UTC unless it names an offset |
| `openfesl accounts verify LOGIN` | Confirms an account's e-mail address by hand, so that it may log in |
| `openfesl personas state NAME active\|disabled` | Sets whether a player's persona may play; the game names a disabled persona as disabled by customer support when its player picks it or another player wants its name |
| `openfesl kick NAME` | Kicks a player's persona from the games it plays in, whose hosts drop it; a persona waiting in a game's queue or for its host is refused instead |

The `keys limit`, `settings`, `accounts`, `personas` and `kick` commands record each action with its time. A running
server applies account changes and kicks within seconds.

## Policies

Where a game handles more than one behaviour, the game's data file in `src/openfesl/games` sets the default and
`openfesl settings` overrides it. The server reads the policies on every use, so a change applies at once.
The data file also holds the age limits of the countries that differ from 13 to register and 18 to register
without a parent's consent. A birth date is checked and never stored. It also lists the entitlements that a dedicated
server whose game players see may grant the players in it; Bad Company 2 lists none.

| Policy | Values | Bad Company 2 |
|---|---|---|
| `registration.age_check` | `on`: a birth date under the country's registration age is refused; `off`: birth dates are not checked | `on` |
| `registration.under_parental_age` | `allow`: players old enough to register but under the country's age for registering without a parent's consent register; `refuse`: they are refused, and the game disconnects | `allow` |
| `accounts.email_verification` | `on`: a new account logs in only once the link mailed to its address is opened; until then the game shows that the account waits for authorization. A link is valid for a day, and a login after that sends a new one. `off`: new accounts log in at once, and so do those still waiting | `off` |
| `accounts.concurrent_login` | `replace`: a login signs the account's other game out; `refuse`: a second login is refused, and the game shows its notice about too many login attempts | `replace` |
| `connect.minimum_client_version` | The oldest version of the game's client that may connect, such as `1.0`, compared number by number; older clients are told that an update is required. Empty lets every version in | empty |
| `connect.minimum_hook_version` | The oldest version of OpenFESLHook that may connect, such as `0.2.0`, compared number by number, with a pre-release such as `0.2.0-dev` older than its release; players and dedicated servers whose hook is older or names no version are told that an update is required. Empty lets every hook in | empty |
| `connect.maintenance` | `on`: players are told that the service is busy, while dedicated servers still connect; `off`: everyone connects | `off` |

## Development

Python 3.14 and [uv](https://docs.astral.sh/uv/), and Node.js 24 with pnpm for the web pages' fonts, scripts and
stylesheets. The tests start PostgreSQL in Docker through Testcontainers.

```sh
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy
uv run lint-imports
uv run djlint --check --lint src/openfesl/web
```

Vite builds the web pages' files from the locked packages into `src/openfesl/web/static/build`, where the server
finds them; without a build the pages show without their stylesheet and scripts. `pnpm dev` rebuilds them on every
change.

```sh
corepack enable
pnpm install
pnpm build
pnpm prettier --check .
```

## Licence

OpenFESL is licensed under the GNU General Public License v3.0 or later; see [LICENSE.md](LICENSE.md). The web pages
ship third-party components under their own licences, listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

OpenFESL is not affiliated with or endorsed by Electronic Arts.
