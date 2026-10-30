from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from openfesl.core.accounts import Account
from openfesl.core.catalogues import Catalogues
from openfesl.core.games import Game, Games
from openfesl.core.heroes import Hero, Heroes
from openfesl.core.personas import PersonaStore
from openfesl.core.stats import PERSONA, Owner, StatStore, bucket_of, partition_of
from openfesl.protocol.rank import Period
from openfesl.web.hero_stats import equipped, facts, number_of
from openfesl.web.pages import Pages

HEROES = "/heroes"


class ProfilePages:
    """The public profiles of the heroes of games whose heroes are made on their site."""

    def __init__(
        self,
        pages: Pages,
        games: Games,
        heroes: Heroes,
        personas: PersonaStore,
        accounts: Callable[[int], Awaitable[Account | None]],
        stats: StatStore,
        catalogues: Catalogues,
    ) -> None:
        """Prepare the profiles of each game whose heroes have them.

        Args:
            pages: Renders the pages.
            games: The games, of which those with profile rules get the pages.
            heroes: Tell each hero's faction, class, level and picture.
            personas: Find a hero by its number.
            accounts: Look up an account by its number, for whether it is public and its name.
            stats: Hold the heroes' stats.
            catalogues: Name the items, with their icons, once imported.
        """
        self._pages = pages
        self._games = games
        self._heroes = heroes
        self._personas = personas
        self._accounts = accounts
        self._stats = stats
        self._catalogues = catalogues

    def routes(self) -> APIRouter:
        """Return the profile of each hero of the games whose heroes have one."""
        router = APIRouter()
        for game in self._games:
            if game.heroes and game.heroes.profile:
                router.add_api_route(
                    f"/{game.id}{HEROES}/{{hero_id:int}}",
                    self._hero_page(game),
                    response_class=HTMLResponse,
                )
        return router

    def _hero_page(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        rules = game.heroes
        assert rules is not None and rules.profile is not None
        profile = rules.profile

        async def show_hero(request: Request, hero_id: int) -> Response:
            found = await self._public_hero(game, hero_id)
            if found is None:
                return self._pages.render(
                    request, "Error", 404, game=game, code=404, kind="profile"
                )
            hero, account = found
            language = request.state.language
            catalogue = await self._catalogues.of(game, language)
            owner = Owner(PERSONA, hero.persona.id)
            values = await self._stats.owned(
                partition_of(game), owner, bucket_of(Period.ALL_TIME, 0)
            )
            title = None
            if profile.rank and (rank := profile.rank.of(number_of(values, profile.rank.key))):
                # The game's texts of the levels hold brackets and spaces that the site left out.
                names = [(catalogue.text(name) or "").strip("[] ") for name in rank if name]
                title = " ".join(names) if all(names) else None
            return self._pages.render(
                request,
                "heroes.Hero",
                game=game,
                hero=hero,
                player=account.display_name or "",
                title=title,
                equipped=equipped(game, values, catalogue, self._pages.shows_art(request, game)),
                facts=facts(rules, hero, values, catalogue, language),
                played=number_of(values, profile.time) > 0,
                imported=catalogue.imported,
            )

        return show_hero

    async def _public_hero(self, game: Game, hero_id: int) -> tuple[Hero, Account] | None:
        # A hero in play of an account that others see; any other answers as an unknown one, so
        # that its page tells nothing of the account.
        persona = await self._personas.find_by_id(hero_id)
        if persona is None or persona.namespace != game.namespace:
            return None
        account = await self._accounts(persona.account_id)
        if account is None or not _public(account):
            return None
        roster = await self._heroes.roster(game, account.id)
        hero = next((h for h in roster.heroes if h.persona.id == hero_id), None)
        return (hero, account) if hero and not hero.disabled else None


def _public(account: Account) -> bool:
    # As the player search finds them: in use, as logins also take one whose state has ended;
    # named; and players' own.
    named = bool(account.display_name)
    return account.login_error() is None and named and not account.server
