from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import AccountRecord
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.hero_boards import HeroBoards
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.personas import PersonaStore
from openfesl.core.relationships import Party, Relationships
from openfesl.core.stats import PERSONA, Owner, StatStore, bucket_of, partition_of
from openfesl.core.texts import localize
from openfesl.protocol.rank import Period
from openfesl.web.account.session import signed_in
from openfesl.web.boards import HISTORIC, LEADERBOARDS, PAGE_SIZE, Board, standings
from openfesl.web.hero_stats import duration, equipped, facts, lists, number_of
from openfesl.web.pages import Pages
from openfesl.web.sessions import form_token

HEROES = "/heroes"
PLAYER = "/player"


class ProfilePages:
    """The public profiles of the heroes and players of games whose heroes are made on their site.

    A hero or player that others may not see answers as one that never existed, so that its
    address tells nothing of the account.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        heroes: Heroes,
        personas: PersonaStore,
        accounts: Callable[[int], Awaitable[AccountRecord | None]],
        stats: StatStore,
        catalogues: Catalogues,
        relationships: Relationships,
        boards: HeroBoards,
    ) -> None:
        """Prepare the profiles of each game whose heroes have them.

        Args:
            pages: Renders the pages.
            games: The games, of which those with profile rules get the pages.
            heroes: Tell each hero's faction, class, level and picture.
            personas: Find a hero by its number.
            accounts: Look up a player's account by its number, for whether it is public, its
                name and since when it exists.
            stats: Hold the heroes' stats.
            catalogues: Name the items, with their icons, once imported.
            relationships: Tell how the signed-in account relates to a player as a friend.
            boards: Tell a hero's place on the all-time score board.
        """
        self._pages = pages
        self._games = games
        self._heroes = heroes
        self._personas = personas
        self._accounts = accounts
        self._stats = stats
        self._catalogues = catalogues
        self._relationships = relationships
        self._boards = boards

    def routes(self) -> APIRouter:
        """Return the profile of each hero and player of the games whose heroes have one."""
        router = APIRouter()
        for game in self._games:
            if game.heroes and game.heroes.profile:
                router.add_api_route(
                    f"/{game.id}{HEROES}/{{hero_id:int}}",
                    self._hero_page(game),
                    response_class=HTMLResponse,
                )
                router.add_api_route(
                    f"/{game.id}{PLAYER}/{{account_id:int}}",
                    self._player_page(game),
                    response_class=HTMLResponse,
                )
        return router

    def _hero_page(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        rules = game.heroes
        assert rules is not None and rules.profile is not None
        profile = rules.profile

        async def show_hero(request: Request, hero_id: int) -> Response:
            persona = await self._personas.find_by_id(hero_id)
            found = None
            if persona and persona.namespace == game.namespace:
                found = await self._public(game, persona.account_id)
            hero = next((h for h in found[1] if h.persona.id == hero_id), None) if found else None
            if found is None or hero is None:
                return self._missing(request, game)
            record, _ = found
            language = request.state.language
            catalogue = await self._catalogues.of(game, language)
            owner = Owner(PERSONA, hero.persona.id)
            values = await self._stats.owned(
                partition_of(game), owner, bucket_of(Period.ALL_TIME, 0)
            )
            title = None
            if profile.rank and (named := profile.rank.of(number_of(values, profile.rank.key))):
                # The game's texts of the levels hold brackets and spaces that the site left out.
                names = [(catalogue.text(name) or "").strip("[] ") for name in named if name]
                title = " ".join(names) if all(names) else None
            art = self._pages.shows_art(request, game)
            # The hero's place on the all-time score board, the board's page that lists it and when
            # it was ranked; like the 2012 site, none for a hero beyond the board's places.
            rank = None
            if "leaderboards" in game.pages:
                board = Board(period=HISTORIC)
                ranked = await standings(self._boards, game, board)
                for line in ranked.lines:
                    if line.hero_id == hero.persona.id:
                        page = (line.place - 1) // PAGE_SIZE + 1
                        path = f"/{game.id}{LEADERBOARDS}{board.changed(page=page).query()}"
                        address = f"{localize(path, language)}#hero-{hero.persona.id}"
                        rank = (line.place, address, ranked.ranked)
            return self._pages.render(
                request,
                "heroes.Hero",
                game=game,
                hero=hero,
                player=record.account,
                title=title,
                rank=rank,
                unranked=number_of(values, profile.time) < profile.listed_after,
                equipped=equipped(game, values, catalogue, art),
                facts=facts(rules, hero, values, catalogue, language),
                played=number_of(values, profile.time) > 0,
                imported=catalogue.imported,
                **lists(game, values, catalogue, art, language),
            )

        return show_hero

    def _player_page(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        rules = game.heroes
        assert rules is not None and rules.profile is not None
        profile = rules.profile

        async def show_player(request: Request, account_id: int) -> Response:
            found = await self._public(game, account_id)
            if found is None:
                return self._missing(request, game)
            record, heroes = found
            owners = [Owner(PERSONA, hero.persona.id) for hero in heroes]
            keys = [profile.time, profile.games]
            totals = await self._stats.values(
                partition_of(game), owners, keys, bucket_of(Period.ALL_TIME, 0)
            )
            time, games = (
                sum(stored.value for (_, key), stored in totals.items() if key == wanted)
                for wanted in keys
            )
            viewer = signed_in(request)
            relationship = None
            if viewer and viewer.account_id != account_id:
                mine = await self._relationships.roster(game, Party(viewer.account_id))
                relationship = next((r for r in mine if r.member == Party(account_id)), None)
            return self._pages.render(
                request,
                "heroes.Player",
                game=game,
                account=record,
                heroes=heroes,
                time=duration(time, request.state.language),
                games=int(games),
                member=Party(account_id),
                relationship=relationship,
                csrf=form_token(request) if viewer else "",
            )

        return show_player

    async def _public(self, game: Game, account_id: int) -> tuple[AccountRecord, list[Hero]] | None:
        # A player's account that others see, with its heroes in play.
        record = await self._accounts(account_id)
        if record is None or not record.account.public:
            return None
        roster = await self._heroes.roster(game, account_id)
        return record, [hero for hero in roster.heroes if not hero.disabled]

    def _missing(self, request: Request, game: Game) -> Response:
        return self._pages.render(request, "Error", 404, game=game, code=404, kind="profile")
