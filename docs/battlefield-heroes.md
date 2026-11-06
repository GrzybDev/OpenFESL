# Battlefield Heroes

OpenFESL serves Battlefield Heroes on PC, the client and its dedicated server, which is the same `BFHeroes.exe` started
to host; the [README](../README.md#what-works) names the tested versions. The game is free to play, so no product key
or entitlement is needed to log in. Its heroes are made on the site's pages, and its items, texts, icons and hero models
come from a catalogue that the operator extracts from a copy of the game and imports.

The prices and amounts below are the defaults of the game data in `src/openfesl/games/bfheroes.toml` and the files it
includes; the admin panel changes most of them. The game's web pages are under `/bfheroes`.

## How the game connects

Started without a session, `BFHeroes.exe` opens OpenFESLHook's
[launcher](https://github.com/GrzybDev/OpenFESLHook#battlefield-heroes), which signs in at `POST /launcher/session`
with the account's e-mail address and game password to play, or asks for the dedicated servers' session to host
([launcher sign-in](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md#launcher-sign-in)). The
session it gets starts the game as the account's default hero, or its oldest playable one; an account without a hero
is refused with `no-hero`.

The site's Play Now page, `/bfheroes/play-now`, starts the game for the signed-in account without its password. The
page makes a one-time start link, `openfesl-bfheroes://launch?role=play&code=<code>`, which OpenFESLHook's launcher
opens where a copy of the game is registered for such links; the launcher signs in with
`{"game": "bfheroes", "role": "play", "code": "<code>"}` instead of the account and password. A link works for two
minutes and only to play, and opening the page again ends the earlier one. Accounts made through a sign-in service,
which have no game password until one is set on the account page, start the game this way too. Start links and
launcher sessions are kept in the server's memory and end when it restarts.

The game's Magma web services at `www.battlefieldheroes.com` reach the service as the rest of the game's HTTP does,
with that host in `X-OpenFESL-Host` (see [How games connect](../README.md#how-games-connect)). The service answers
them for the session the game was started with: its login token, the heroes' items and their uses, the wallets, the
store's offers and purchases, the refund of abilities, friends and the player search, the account checks and profile
refreshes, the portraits that the game uploads, the in-game store, the hot deals of the game's home screen, and the
game's ad windows, which show a notice that points to the site in place of ads.

A dedicated server sends some Magma calls without a session: the name of a player in its game, the Valor Points and
Hero Points that a round earned, and the treasure chests that heroes picked up. They carry the relay token of the
server's WebSocket, and the service answers them only when the token's session is a dedicated server of the game
that hosts a game players see, and only about the players it hosts or reported.

The game opens some of its website's addresses in the system's browser: the hero creator when the account has no
hero, and the Funds page. OpenFESLHook opens them at `/open/<host>/<path>`
([pages in the browser](https://github.com/GrzybDev/OpenFESLHook/blob/main/docs/relay-protocol.md#pages-in-the-browser)),
which leads to the site's page in the address's language, or else the browser's.

## Dedicated servers

A dedicated server is `BFHeroes.exe` started to host through OpenFESLHook's launcher, or by hand with the session
`POST /launcher/session` gives for `{"game": "bfheroes", "role": "host"}`. Servers share an account and a persona of
the service, `bfh.server`, so hosting needs no account of a player, and what the launcher sends for one is ignored.
A server's Magma calls about its players need the relay token of its game process, which OpenFESLHook adds. Its games
are approved, revoked and listed like other games' servers ([Administration](../README.md#administration)), and
`connect.minimum_client_version` and
`connect.maintenance` keep it out as they keep players out ([Policies](../README.md#policies)).

At the end of a round, a server reports the heroes' stats, credits Valor Points, at most 1,000 to an account within
ten minutes, and stacks the treasure chests that heroes picked up, at most five at a time. It also credits Hero
Points, which the service records and credits as 0, since heroes earn Hero Points with their levels.

## The catalogue

OpenFESL ships no game files. `openfesl catalogue extract FOLDER` reads the catalogue from a copy of the game whose
`BFHeroes.exe` is in `FOLDER` and leaves the copy as it is. It needs no server or database, so it runs from a checkout
on the computer that holds the copy:

```sh
uv run openfesl catalogue extract path/to/game
```

| From the copy | What the catalogue takes |
|---|---|
| `mods/bfheroes/Objects/Items_server.zip` | The definitions of the appearance items, weapons, abilities and their levels, emotes, boosters and missions, with the fields that the game data's rules and the store's prices read; the default customisation and the colours of skin and hair; the scripts of the parts that heroes wear |
| `mods/bfheroes/Localization/strings.csv` | The items' names and descriptions and the texts of the hot deals, rewards, profiles and Daily Claw, in each of the 14 languages of the language table |
| `mods/bfheroes/Menu_client.zip` | The items' icons, as WebP pictures |
| `mods/bfheroes/Objects/Items_client.zip`, `mods/bfheroes/Objects/Soldiers_server.zip` and `mods/bfheroes/Common_client.zip` | The meshes and textures of the parts that heroes wear and the skeletons and animations of the menu's heroes, converted to glTF and WebP |
| `BFHeroes.exe` | The daily mission of each day of the year and the XP of each level |

It writes `bfheroes-catalogue.zip` in the current folder, or the file that `--out` names, replacing an existing one.

`openfesl catalogue import FILE` in the server's container, or the admin panel's catalogues page, which uploads a file
of up to 256 MiB in the background and shows its progress, replaces the game's catalogue at once. The database keeps
its items and texts, and the `FILES_PATH` folder its icons, hero models and textures; without `FILES_PATH` an import
keeps none of those. A running server takes an import made elsewhere within seconds.

The catalogue gives the items their names, descriptions and icons, the creator its hero models and animations, the
store its products, and the service the checks of what heroes wear and equip, the daily missions and the XP of each
level. The first import fills the store from the game data; later ones keep the prices that the admins set. Until
then the store sells nothing, and the pages have no item names, icons or hero models to show.

The catalogues page also chooses who sees the game's art, its icons and hero models and the portraits that the game
renders of heroes: everyone, signed-in players and the game itself (the default), or nobody, admins included. Others
see neutral placeholders, and the files are served only to those who see them.

## Heroes

- The game makes no heroes: with none, it opens the site's creator, `/bfheroes/user/createHero`, which makes a hero
  of a faction and class with a skin tone, hair colour, hair and facial hair, and its faction's and class's starting
  kit; with an imported catalogue that holds hero models, it shows the hero as the game's menu draws it
- Names are 2 to 16 letters, digits, `_` or `-`, unique ignoring case
- An account has 4 hero slots and buys more for 2,499 Funds each; a rename costs 5,899 Funds. The sales of 2012 come
  back each year: slots at 50% off on 2-6 July and 13-17 November and at 30% off on 1-5 October, renames at 50% off on
  6-10 July and 15-19 November
- The heroes page chooses the default hero, which the game and Play Now play
- Heroes reach level 30 and earn one Hero Point on each even level
- The game writes its hero's look, equipment, emotes and missions itself, which the service accepts only with items
  that the hero holds and, with an imported catalogue, that fit the hero's faction, class and level
- The game renders a portrait of each hero and uploads it; the service keeps the latest one for the hero's cards and
  profile

## Currencies

| Currency | Held by | Comes from | Spent on |
|---|---|---|---|
| Funds | The account, shared by its heroes | A starting allowance of 2,500, rewards, gift codes, Recruit a Friend and the admins' grants; never sold, as the service takes no payments | Store items, hot deals, random boxes, treasure chest keys, hero slots, renames and paid grabs of the Daily Claw |
| Valor Points | The account, shared by its heroes | Rounds on dedicated servers, gift codes, Recruit a Friend and the admins' grants | Store items priced in Valor Points, such as weapon rentals |
| Hero Points | Each hero | One on each even level | Ability levels, which a refund of the hero's abilities pays back |

Each wallet keeps a ledger of its changes.

## The store

- The game data lists what the original store last sold most items for, in Funds and Valor Points, to own them for
  good, rent them for days or buy packs of uses; an item it does not list follows the first rule of its kind in each
  currency, and what a box gives of a listed item is priced from its listed price. Items that no rule sells, such as
  most pets, the pieces that only boxes give and missions, and those it excludes, such as the clothes every hero
  holds, are not sold alone
- In the game, the store opens in the game's own browser with its categories, items and prices for the hero playing,
  and a purchase there tells the game what was bought
- On the site, the store front lists every item on sale for a chosen hero, and each item's page shows what each hero
  holds or lacks and buys it for one of them
- Hot deals are the admins' own offers, with their own texts, stock and end, and a rotation that changes by itself in
  sections: the Deal of the Day, new sets each Monday, sets on sale twice a week, seasonal sets, random boxes, themed
  boxes, event boxes and a popular list
- The site's hot deals page shows them with countdowns, its front page each faction's deal of the day, and the 1.89
  client's home screen shows them with pictures that the service draws or the admins upload
- A random box gives one drop that the hero may get, by its tiers' weights, never an item that the hero owns for
  good; a box that promises its worth skips drops worth less than its price, and a hero who may get none of its drops
  gets its fallback, such as a pack of tonics
- Dedicated servers stack the treasure chests that heroes pick up in rounds; a treasure chest key, 499 Funds, opens
  one on the site, which reveals the item, and keys are sold 1, 3 or 5 at a time
- The treasure chests page lists each hero's chests and what chests give the heroes of each faction, with the chances

## Rewards and gifts

- The rewards page lists each reward with its progress and the claims made; an account claims each reward once, and
  a calendar event's once each year it runs, choosing which hero gets its items, while its Funds and keys go to the
  account
- The yearly calendar: Valentine's Day (13 to 15 February), Easter (Good Friday to Easter Monday), the game's
  birthday (25 June to 1 July), a daily trick-or-treat bag and two missions at Halloween (19 to 31 October), a daily
  Bento Box (21 to 27 November) with the Rising Sun mission, which is off by default, and a daily gift at Christmas (1
  to 24 December)
- A new hero claims its class's starter pack within 30 days of its creation
- Funds milestones: 600 for the account's first hero, 500, 1,500 and 3,000 for levels 10, 20 and 30 of each hero, and
  1,000 for 100 finished ranked matches
- The account's first treasure chest brings one treasure chest key
- The days of the rewards and the Daily Claw's free grab start at midnight in the time zone that the admin settings
  choose, UTC by default
- An admin can pause every claim with a note; the daily gifts of paused days stay claimable until their event ends
- Gift codes give items to one hero, a roll of a box, Funds, Valor Points or treasure chest keys, with a name that
  players see, a limit of accounts and an end; a batch holds personal codes for one account each; accounts redeem
  them on the redeem page, `/bfheroes/store/redeem`
- Recruit a Friend: each account has a personal link, and an account made through it becomes its recruit. When a
  recruit's hero reaches level 7, the recruiter gets 1,350 Valor Points and the recruit a day of the Basic XP Boost;
  when one reaches level 15 within 60 days of the recruit's registration, the recruiter gets 3,500 Funds and the
  recruit 1,000 Valor Points
- The Daily Claw gives one free grab a day for one of the account's heroes, and paid grabs at 599 Funds each with
  the same prizes and chances; a hero is offered the prizes that fit its faction and class and that it does not own
  for good
- The Daily Claw's page lists the latest rare prizes, and its machine opens by itself over the site's pages while a
  free grab waits, until it is closed for the day

## Friends and profiles

- Friends are kept between accounts, not heroes: requests are sent and answered in the game and on the friends page,
  and the game's list shows up to 100 of them
- The player search, in the game and on the site, finds players by display name and heroes by name
- Dedicated servers keep VIP lists of accounts, which their hosts change in the game; the friends page lists the
  servers whose list holds the account and leaves them
- Leaderboards rank heroes by score, kills per death, wins per loss, time played, accuracy and capture points, for all
  time and by week, each also for one faction or class; a hero shows once it played 2 hours in the board's period
- Each hero has a public profile with its Soldier Info, equipped items, maps, weapons, vehicles, heroic moments and
  missions, and each player one with its heroes and their totals, from which friend requests are sent

## The admin panel

Moderators see these views and may rename, disable and enable heroes; every other change needs the admin role, and
each is recorded in the audit log.

- Accounts: each account's heroes, with a page per hero to rename, disable or enable it, make it the default, remove
  its portrait, and grant, change or revoke its items; grants of hero slots and grants or takes of keys and chests;
  the wallets with their ledgers and grants or takes with a note; purchases, recruits, which can be removed, and gift
  code redemptions
- Catalogues: each game's imported catalogue, the import and who sees the game's art
- Store: each item's prices with a preview of its store tile, the rules by kind and applying a changed rule, the
  accounts' prices, allowances and per-round caps, and every purchase
- Boxes: the drop tables of the random boxes and the treasure chests, the worth rule, switching boxes off sale and
  chest opening off, and the Daily Claw's settings and prizes
- Hot deals: the rotation's switch and sections, each section's pools of pinned and excluded offers and a preview of
  its next step, the pins of the game's home screen, a log of the steps, the admins' offers with their texts, stock,
  restocking and end, and uploaded pictures
- Rewards: the calendar and rules, each event's days, gifts, texts and mission goals, the switches, the milestones'
  Funds, Recruit a Friend's switch and steps, the pause of every claim, a preview of the players' rewards page on a
  date, and this year's claims
- Product keys: gift codes and batches of personal codes with their download, changing, disabling and enabling them,
  and each code's redemptions
- Settings: the time zone of the rewards' days
