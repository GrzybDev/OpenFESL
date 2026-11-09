# OpenFESL

OpenFESL is an open-source master server for PC games built on EA's FESL and Theater online services. It serves
Battlefield: Bad Company 2 and Battlefield Heroes and keeps every game's specifics in game data, so more games can
follow. Games reach it through [OpenFESLHook](https://github.com/GrzybDev/OpenFESLHook), a DLL placed next to the game
or its dedicated server.

An original copy of the game is needed; OpenFESL contains no game files. Battlefield Heroes' items, texts, icons and
hero models come from a [catalogue](docs/battlefield-heroes.md#the-catalogue) extracted from a copy the operator owns.

## What works

For every game, on the site and in the admin panel:

- Accounts on the site: registration with an e-mail address, game password, display name, country, birth date and
  the terms, and sign-in with the address and game password, paused for an address after repeated failures, or
  through the configured OpenID Connect and OAuth2 services
- Address confirmation by a mailed link, game password resets by a mailed link, a display name for accounts made in a
  game, chosen at their first sign-in on the site, and new versions of the terms to accept
- Linking a sign-in service to the account whose address the service verified, once the account is proven by its game
  password, another of its services or a mailed link
- The account page: display name, country, each game's personas, the game password, linked sign-in services, sessions
  with sign-out everywhere, and a deletion request, which deactivates the account
- Official dedicated servers: approved servers that the administrators mark as their own, shown as official on the
  pages that list servers and, where the game has one, with the game's own server mark, which the settings turn off
  per game
- Web pages in English and Polish, starting at OpenFESL's menu of games
- Each game's players online, counted every minute and kept as the peak of every hour for seven days and of every
  day for good
- An admin panel: servers waiting for approval and warnings, the dedicated servers with their official marks and
  their providers with `ProviderId.dat` downloads, accounts with their states, sessions, names, remembered logins and
  entitlements, product keys, each game's imported catalogue and who sees its art, the hosting mode, the site's policy,
  each game's icon for Discord presence with a preview, its own server mark and its policies, and an audit log.
  Moderators decide about servers and accounts; changing providers, official marks, entitlements, product keys,
  catalogues and settings needs the admin role

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
- The game's web pages: its players online, its servers with their settings and players, a leaderboard, player pages
  with stats, awards and dog tags, the peaks of its players online, and guides to playing and to hosting a dedicated
  server
- Discord Rich Presence for players whose hook asks for it: the menus, a server's queue, joining, and the match with
  its mode, map, server and players, in the language of the player's game, with the game's icon and OpenFESL's;
  servers that host games get none

For Battlefield Heroes on PC, the client and its dedicated server. Only versions 1.46.222034.0 and 1.89.239937.0 are
tested; other versions are expected to work, but that is not guaranteed.
[docs/battlefield-heroes.md](docs/battlefield-heroes.md) describes each part and what admins control:

- Starting the game from OpenFESLHook's launcher or the site's Play Now links, and the game's Magma web services
- Heroes made in the site's creator, which shows the game's own models once an imported catalogue brings them, with
  renames, bought hero slots and the default hero that the game plays
- Stats from the game and its dedicated servers, Hero Points for levels and Valor Points from rounds
- Funds, Valor Points and Hero Points, of which Funds come from a starting allowance, rewards, gift codes and
  Recruit a Friend and are never sold
- The store in the game's own browser and on the site, priced from the game data and the admins' rules, with hot
  deals, random boxes, and treasure chests that keys open
- The rewards calendar, starter packs and milestones, gift codes, Recruit a Friend and the Daily Claw
- Friends, the player search and dedicated servers' VIP lists, in the game and on the site
- The portraits the game renders of its heroes, heroes' leaderboards, and heroes' and players' public profiles
- Dedicated servers that share a server account, approved like other games' servers
- The game's catalogue of items, texts, icons and hero models, extracted from a copy of the game and imported
- The game's web pages: a front page, Play Now, the guide to playing, heroes, the store, hot deals, treasure chests,
  the Daily Claw, redeeming codes, Funds, rewards, Recruit a Friend, friends, the player search, leaderboards and
  profiles
- Admin panel views of each account's heroes, items, wallets, purchases and recruits, the store's prices, boxes and
  hot deals, the rewards, gift codes, Recruit a Friend and the Daily Claw

## How games connect

OpenFESLHook opens one WebSocket per game process to the server's root path with the subprotocol `openfesl.v1` and
carries the game's FESL and Theater traffic over it. The hook's
[relay protocol](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md) describes the frames.
The game's HTTP requests go to the same host over `https://` (or `http://` for a `ws://` address), with the game's
host in the `X-OpenFESL-Host` header. The server answers the WebSocket's upgrade with a token in
`X-OpenFESL-Relay-Token`, which the hook adds as `X-OpenFESL-Relay` to the game's HTTP requests while the WebSocket is
open ([connection](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md#connection)); it tells
which game process sent a request that carries no session, and the server masks it in its log. A game the hook starts
from its launcher, because it was started without a session, signs in at `POST /launcher/session` first and gets the
session it is started with; the relay protocol's
[launcher sign-in](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md#launcher-sign-in)
describes the request and its answers. Battlefield Heroes also reaches its Magma web services this way, and its
launcher signs in with the one-time code of a Play Now link too;
[Battlefield Heroes](docs/battlefield-heroes.md#how-the-game-connects) describes both.

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
| `SMTP_HOST` | The mail server that sends the links that confirm addresses, reset game passwords and prove an account before a sign-in service is linked to it, and the mail about a registration with an address in use and about a deletion request; without one no mail goes out and the site offers no game password reset | none |
| `SMTP_PORT` | The mail server's port | `587` |
| `SMTP_SECURITY` | `starttls` upgrades a plain connection, `tls` encrypts from the start (usually port 465), `none` leaves it plain | `starttls` |
| `SMTP_USER` | The name to log in to the mail server with, if it needs a login | none |
| `SMTP_PASSWORD` | The password of that login | none |
| `SMTP_SENDER` | The address mail comes from; required for mail to go out | none |
| `PUBLIC_URL` | The address the server's web pages are reached at, where the links in mail lead and sign-in services send the browser back to, such as `https://fesl.example.com`; required for mail to go out and for the images of Discord presence, which Discord fetches only from a public HTTPS address. The guides to playing and hosting give the relay address that follows from it, such as `wss://fesl.example.com`, or the request's own without it | none |
| `HOOK_DEFAULT_URL` | The relay address OpenFESLHook's releases connect to without an `OpenFESLHook.ini`; where this server's relay address differs, the guides show the lines of that file | `wss://fesl.grzyb.app` |
| `CONTACT_EMAIL` | The address at which the operators of dedicated servers reach this server's administrators, such as for a provider file; the guide to hosting shows it | none |
| `SOURCE_URL` | The address of the source code of the version this server runs, offered in the site's footer; a server that runs a changed version has to name that version's source code here | `https://github.com/GrzybDev/OpenFESL` |
| `SESSION_SECRET` | The key that signs the session cookie of the admin panel and the site's accounts; without one each start of the server picks its own, which signs everyone out | none |
| `ADMIN_OIDC_NAME` | The name of the admin panel's sign-in provider on its button | `Authentik` |
| `ADMIN_OIDC_ISSUER` | The OpenID Connect issuer the admins sign in through, whose discovery document names its endpoints, such as `https://auth.example.com/application/o/openfesl/`; without it and the client's id and secret nobody signs in to the panel | none |
| `ADMIN_OIDC_CLIENT_ID` | The panel's client id at the provider | none |
| `ADMIN_OIDC_CLIENT_SECRET` | The panel's client secret there | none |
| `ADMIN_GROUP` | The provider's group whose members are admins | none |
| `MODERATOR_GROUP` | The provider's group whose members are moderators, unless they are admins | none |
| `DISCORD_APPLICATION_ID` | The id of the Discord application whose Rich Presence shows what players do in their games, sent to the hooks that ask for it; without one no presence is sent | none |
| `TURNSTILE_SITE_KEY` | The site key of Cloudflare Turnstile, whose check guards the site's forms that take an address or a game password or send mail: sign-in, registration, new confirmation links, game password resets and the proof of an account before a sign-in service is linked to it; without it and the secret key no form is checked | none |
| `TURNSTILE_SECRET_KEY` | The secret key of Cloudflare Turnstile, with which the server checks a form's token; a token that Cloudflare does not confirm, or no answer from Cloudflare, fails the form, which comes back with a new check | none |
| `SIGN_IN_SERVICES` | The services that players sign in to the site through, as a JSON list described below; their tiles show in this order | none |
| `FILES_PATH` | The folder where the server keeps the icons, hero models and textures of the games' imported catalogues, in `catalogues/<game>/<import>`, removing those of earlier imports; without one an import keeps none of them | none |

The database schema is created and upgraded when the server starts.

### Sign-in services

Each entry of `SIGN_IN_SERVICES` is an object with the fields below. Register the site at each service as a
confidential client with the redirect URI `<PUBLIC_URL>/sign-in/callback`, or the request's own address without
`PUBLIC_URL`; the site signs in with the authorization code flow and PKCE. A linked identity signs its account in; a
new one whose verified address belongs to an account is linked to it once the account is proven, and any other new
one goes to the step that creates an account.

| Field | Description | Default |
|---|---|---|
| `id` | The service's id in forms and the database, 1 to 32 lowercase letters, digits or hyphens that starts with a letter or digit, such as `example`; it never changes, as linked accounts keep it | required |
| `name` | Its name on the pages | required |
| `kind` | `oidc` for OpenID Connect, whose discovery document names the endpoints and whose ID token names the account, or `oauth2` for plain OAuth2, whose endpoints the fields below name | `oidc` |
| `glyph` | The neutral glyph its tile shows: `chat`, `code`, `globe` or `key` | `key` |
| `client_id` | The site's client id at the service | required |
| `client_secret` | The site's client secret there | required |
| `scope` | The scopes it asks for, separated by spaces, which the sign-in page names | required |
| `issuer` | An `oidc` service's issuer | required for `oidc` |
| `authorize_url` | An `oauth2` service's authorization endpoint | required for `oauth2` |
| `token_url` | An `oauth2` service's token endpoint | required for `oauth2` |
| `userinfo_url` | An `oauth2` service's endpoint that answers the signed-in account | required for `oauth2` |
| `emails_url` | An endpoint that lists the account's addresses with whether each is verified, for services whose account answer leaves that out; a verified one marked primary is taken first | none |
| `subject_claim` | The claim that holds the account's id there, which never changes | `sub` |
| `name_claim` | The claim that holds the account's name, which the step that creates an account suggests as its display name | `preferred_username` |
| `email_claim` | The claim that holds the account's e-mail address | `email` |
| `verified_claim` | The claim that says whether the service verified that address | `email_verified` |

```sh
SIGN_IN_SERVICES='[{"id": "example", "name": "Example ID", "issuer": "https://auth.example.com/application/o/openfesl/", "client_id": "openfesl", "client_secret": "secret", "scope": "openid profile email"}]'
```

## Deploying

- Run one container: sessions reach each other in memory, so the server runs as one process with one worker. Stop
  the old container before the new one starts.
- Serve it over TLS (`wss://`) through a proxy that passes WebSocket upgrades and keeps connections open for hours.
  The server pings the hook to find dead connections.
- On shutdown the server closes every session with code 1001, and the hook reconnects when the game does.
- Keep the `FILES_PATH` folder on a volume that the image's user (uid 1000) can write, so that the catalogues' files
  outlast the container.
- The admin panel's catalogue upload sends the whole file in one request of up to 256 MiB, which the proxy has to let
  through; `openfesl catalogue import` in the container needs no upload.
- The image checks its health with `python -m openfesl.server.health`, which loads only the standard library so
  it answers within its timeout on small machines such as a Raspberry Pi.

## Admin panel

The admin panel at `/admin/` signs admins in through one OpenID Connect provider, such as Authentik, with the
authorization code flow and PKCE. Register it there as a confidential client with the redirect URI
`<PUBLIC_URL>/admin/sign-in/callback` and the scopes `openid profile`; the panel reads the account's groups from the
`groups` claim of its ID token. It keeps only the account's id, display name and role, in a signed session cookie
that is `Secure` when `PUBLIC_URL` starts with `https://`. An admin signs in again after 12 hours.

## Administration

The `openfesl` command in the server's container manages what the admin decides:

| Command | What it does |
|---|---|
| `openfesl hosting [approved\|open]` | Shows or sets whose games players see: only approved dedicated servers' (the default) or every server's |
| `openfesl providers list` | Lists the providers, the operators of dedicated servers, with their ids |
| `openfesl providers add NAME [--out PATH]` | Adds a provider and writes the `ProviderId.dat` its servers read, next to `Win32Game.cfg` in the server's folder |
| `openfesl providers revoke ID` | Stops every server of a provider from hosting |
| `openfesl servers list` | Lists the dedicated servers that tried to host, with their state, `official` for those marked so, and provider |
| `openfesl servers approve UGID` | Lets players see a server's games |
| `openfesl servers revoke UGID` | Stops a server from hosting |
| `openfesl servers official UGID` | Marks an approved server as run by this service's administrators: the pages that list servers show it as official, and its games carry the game's own server mark where the game has one and the settings show it |
| `openfesl servers unofficial UGID` | Removes a server's official mark |
| `openfesl keys import` | Adds the games' product keys that the database lacks, keeping the others |
| `openfesl keys limit CODE [--max-uses N \| --unlimited] [--once-per-account \| --any]` | Shows or changes how often a product key may be redeemed: by how many accounts, and whether an account that redeemed it is refused when it enters it again (otherwise that grants nothing new). At the login's key prompt the game names a used-up key as already in use and one the account redeemed as already entitled; the menu shows its notice of a failed redeem. Keys start unlimited |
| `openfesl settings list` | Lists the site's policy and every game's policies with their values |
| `openfesl settings get KEY` | Shows a policy's value; the key names the game and the policy, such as `bfbc2.accounts.concurrent_login`, or is the site's `site.accounts.email_verification` |
| `openfesl settings set KEY VALUE` | Overrides the default for this server |
| `openfesl settings reset KEY` | Returns a policy to its default |
| `openfesl accounts state LOGIN active\|disabled\|banned\|deactivated [--until DATE]` | Sets whether an account may log in; the game's login form names the state, and a player online is signed out, with the game's ban notice for a ban, and leaves the games it plays in. `--until` ends the state at a date such as `2026-12-01` or `2026-12-01T18:00+01:00`, UTC unless it names an offset |
| `openfesl accounts verify LOGIN` | Confirms an account's e-mail address by hand, so that it may log in |
| `openfesl personas state NAME active\|disabled` | Sets whether a player's persona may play; the game names a disabled persona as disabled by customer support when its player picks it or another player wants its name |
| `openfesl kick NAME` | Kicks a player's persona from the games it plays in, whose hosts drop it; a persona waiting in a game's queue or for its host is refused instead |
| `openfesl icons set GAME FILE` | Stores a game's icon, such as `bfbc2`'s, from a PNG file or the largest PNG image of an ICO file (an ICO file of bitmaps only and an image over 1 MB are refused). The server keeps it in the database and serves it at `/<game>/icon.png` as the large image of the game's Discord presence; without one the presence shows the Discord application's icon. The admin panel's settings store, replace and remove it too, and warn when `PUBLIC_URL` is no public HTTPS address |
| `openfesl icons remove GAME` | Removes a game's icon |
| `openfesl catalogue extract FOLDER [--out FILE]` | Reads a game's catalogue from the copy of the game whose executable is in `FOLDER` and writes it to `FILE`, `<game>-catalogue.zip` in the current folder by default, replacing an existing one. It needs no server or database, so it runs from a checkout on the computer that holds the copy; [the catalogue](docs/battlefield-heroes.md#the-catalogue) lists what it reads |
| `openfesl catalogue import FILE` | Replaces a game's catalogue with the one that `catalogue extract` wrote to `FILE`, keeping its icons and hero models in `FILES_PATH`; the admin panel's catalogues page imports too |

The `keys limit`, `servers official`, `servers unofficial`, `settings`, `accounts`, `personas`, `kick`, `icons` and
`catalogue import` commands record each action with its time. A running server applies account changes, kicks, icons,
official marks and catalogue imports within seconds.

## Policies

Where a game handles more than one behaviour, the game's data file in `src/openfesl/games` sets the default and
`openfesl settings` overrides it. The server reads the policies on every use, so a change applies at once.
The data file also holds the age limits of the countries that differ from 13 to register and 18 to register
without a parent's consent. A birth date is checked and never stored. It also lists the entitlements that a dedicated
server whose game players see may grant the players in it; for Battlefield: Bad Company 2 these are the two that the
game's data marks automatic, which its servers grant every joining player who lacks them.

| Policy | Values | Battlefield: Bad Company 2 | Battlefield Heroes |
|---|---|---|---|
| `registration.age_check` | `on`: a birth date under the country's registration age is refused; `off`: birth dates are not checked | `on` | `on` |
| `registration.under_parental_age` | `allow`: players old enough to register but under the country's age for registering without a parent's consent register; `refuse`: they are refused, and the game disconnects | `allow` | `allow` |
| `accounts.email_verification` | `on`: an account registered in the game logs in only once the link mailed to its address is opened; until then the game shows that the account waits for authorization. A link is valid for a day, and a login after that sends a new one. `off`: such accounts log in at once, and so do those still waiting | `off` | `off` |
| `accounts.concurrent_login` | `replace`: a login signs the account's other game out; `refuse`: a second login is refused, and the game shows its notice about too many login attempts | `replace` | `replace` |
| `connect.minimum_client_version` | The oldest version of the game's client that may connect, such as `1.0`, compared number by number; older clients are told that an update is required. Empty lets every version in | empty | empty |
| `connect.minimum_hook_version` | The oldest version of OpenFESLHook that may connect, such as `0.2.0`, compared number by number, with a pre-release such as `0.2.0-dev` older than its release; players and dedicated servers whose hook is older or names no version are told that an update is required. Empty lets every hook in | empty | empty |
| `connect.maintenance` | `on`: players are told that the service is busy, while dedicated servers still connect; `off`: everyone connects | `off` | `off` |

Battlefield Heroes' dedicated servers greet FESL as its clients do, so `connect.minimum_client_version` and
`connect.maintenance` keep them out too.

The site has one policy of its own, `site.accounts.email_verification`, which `openfesl settings` and the admin
panel's settings change. `on`, the default, lets an account registered on the site's pages sign in on the site and log
in to games only once the link mailed to its address is opened, and the site registers no accounts while the server
sends no mail; `off` lets such accounts in at once, those still waiting included. The site's registration checks a
birth date against the age limits and registration policies of every game, and the highest limit applies.

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

The tests that drive the pages in a browser use Microsoft Edge through Playwright and skip where Edge is not
installed; some of them also skip without a build of the pages. The tests against a copy of Battlefield Heroes are
opt-in, only read the copy and skip without these variables:

| Variable | What it enables |
|---|---|
| `OPENFESL_TEST_BFHEROES_DIR` | The folder of `BFHeroes.exe` of a copy of Battlefield Heroes 1.46.222034.0 or 1.89.239937.0: extracting and importing its catalogue, reading every mesh, skeleton and animation file of the copy to its end and converting its hero items to glTF, and checking that the store's, hot deals' and rewards' data name the copy's items and texts and that every box is worth its price. The hot deals and rewards need 1.89 and skip for 1.46 |
| `OPENFESL_TEST_BFHEROES_PORTRAIT` | With the copy, a saved copy of the portrait that the game rendered of a National Army commando in the starting outfit, with the seventh skin tone and neither hair nor facial hair; the game writes each portrait it renders over `mods/bfheroes/mugshot.png` in its folder. The creator's stage has to draw that hero as the game's menu does, which also needs Edge and a build of the pages |

## Licence

OpenFESL is licensed under the GNU Affero General Public License v3.0 or later; see [LICENSE.md](LICENSE.md). A server
that runs a changed version has to offer that version's source code to its users, which the site's footer does
through `SOURCE_URL`. The web pages ship third-party components under their own licences, listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

OpenFESL is not affiliated with or endorsed by Electronic Arts.
