from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException

from openfesl.core.catalogues import Catalogues, file_path
from openfesl.core.figures import Figure, Figures
from openfesl.core.games import Game, Games
from openfesl.core.hero_rules import HeroRules
from openfesl.core.heroes import (
    Heroes,
    HeroRefused,
    Look,
    Refusal,
    random_look,
    read_look,
)
from openfesl.core.texts import localize, translate_count
from openfesl.web.account.session import signed_in
from openfesl.web.catalogue_files import catalogue_file_url
from openfesl.web.pages import Pages, listed
from openfesl.web.sessions import form_matches, form_token
from openfesl.web.site import FIGURE, NAME_CHECK, site_bar

CREATOR = "/user/createHero"


class CreatorPages:
    """The site's hero creator and the page of a hero just created, with what their scripts ask.

    The scripts check a wanted name and load the game's model of a hero with a look, where the
    game's art is shown to the viewer.
    """

    def __init__(
        self,
        pages: Pages,
        games: Games,
        heroes: Heroes,
        catalogues: Catalogues,
        figures: Figures,
    ) -> None:
        """Prepare the creator of each game whose heroes are made on its site.

        Args:
            pages: Renders the pages.
            games: The games, of which those with hero rules get a creator.
            heroes: Creates and lists the heroes.
            catalogues: Name the items of a new hero's starting kit, once imported.
            figures: Assemble the game's models of heroes, where an import keeps them.
        """
        self._pages = pages
        self._games = games
        self._heroes = heroes
        self._catalogues = catalogues
        self._figures = figures

    def routes(self) -> APIRouter:
        """Return each game's creator, its page of a created hero and its check of names."""
        router = APIRouter()
        for game in self._games:
            if game.heroes:
                base = f"/{game.id}"
                router.add_api_route(
                    base + CREATOR, self._creator(game), response_class=HTMLResponse
                )
                router.add_api_route(base + CREATOR, self._create(game), methods=["POST"])
                router.add_api_route(base + NAME_CHECK, self._check(game))
                router.add_api_route(base + FIGURE, self._figure(game))
                router.add_api_route(
                    base + "/heroes/{hero_id}/created",
                    self._created(game),
                    response_class=HTMLResponse,
                )
        return router

    def _creator(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_creator(request: Request) -> Response:
            look = random_look(_rules(game))
            return await self._render(request, game, look, _from_game(request.query_params))

        return show_creator

    def _create(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def create(request: Request) -> Response:
            form = await request.form()
            fields = {key: str(value) for key, value in form.items()}
            rules = _rules(game)
            player = signed_in(request)
            from_game = _from_game(fields)
            look = read_look(rules, fields) or random_look(rules)
            name = fields.get("name", "").strip()
            if player is None or not form_matches(request, form):
                return await self._render(request, game, look, from_game, name, notice="form")
            try:
                hero = await self._heroes.create(game, player.account_id, look, name)
            except HeroRefused as refused:
                return await self._render(
                    request, game, look, from_game, name, refused=refused.refusal
                )
            target = localize(f"/{game.id}/heroes/{hero.id}/created", request.state.language)
            return RedirectResponse(target + ("?from=game" if from_game else ""), 303)

        return create

    def _check(self, game: Game) -> Callable[[Request, str, str], Awaitable[Response]]:
        async def check_name(request: Request, name: str = "", current: str = "") -> Response:
            # A hero being renamed keeps its current name, which counts as taken by others.
            name = name.strip()
            same = current and name.casefold() == current.casefold()
            check = "same" if same else (await self._heroes.check(game, name)).value
            return self._pages.render(
                request, "heroes.NameCheck", game=game, name=name, check=check
            )

        return check_name

    def _figure(self, game: Game) -> Callable[[Request], Awaitable[Response]]:
        async def show_figure(request: Request) -> Response:
            look = read_look(_rules(game), request.query_params)
            figure = self._document(request, game, look) if look else None
            if figure is None:
                raise HTTPException(404)
            return JSONResponse(figure)

        return show_figure

    def _created(self, game: Game) -> Callable[[Request, int], Awaitable[Response]]:
        async def show_created(request: Request, hero_id: int) -> Response:
            bar = await site_bar(request)
            if bar is None or bar.roster is None:
                return RedirectResponse(
                    localize(f"/{game.id}{CREATOR}", request.state.language), 303
                )
            roster = bar.roster
            hero = next((h for h in roster.heroes if h.persona.id == hero_id), None)
            if hero is None:
                raise HTTPException(404)
            slot = roster.heroes.index(hero) + 1
            kits = await self._kits(game, request.state.language)
            return self._pages.render(
                request,
                "heroes.Created",
                game=game,
                roster=roster,
                hero=hero,
                slot=slot,
                kit=kits[(hero.faction, hero.hero_class)]
                if hero.faction and hero.hero_class
                else "",
                from_game=_from_game(request.query_params),
                figure=self._document(request, game, hero.look) if hero.look else None,
            )

        return show_created

    async def _render(
        self,
        request: Request,
        game: Game,
        look: Look,
        from_game: bool,
        name: str = "",
        refused: Refusal | None = None,
        notice: str = "",
    ) -> Response:
        bar = await site_bar(request)
        roster = bar.roster if bar else None
        status = 400 if notice or refused is Refusal.FORMAT else 409 if refused else 200
        context: dict[str, Any] = dict(
            game=game,
            rules=_rules(game),
            roster=roster,
            look=look,
            name=name,
            refused=refused.value if refused else "",
            notice=notice,
            from_game=from_game,
            kits={
                f"{faction}.{hero_class}": text
                for (faction, hero_class), text in (
                    await self._kits(game, request.state.language)
                ).items()
            },
            next=localize(f"/{game.id}{CREATOR}", request.state.language)
            + ("?from=game" if from_game else ""),
            figure=self._document(request, game, look),
            csrf=form_token(request),
        )
        return self._pages.render(request, "heroes.Creator", status, **context)

    def _document(self, request: Request, game: Game, look: Look) -> dict[str, Any] | None:
        # The game's model of a hero, where the game's art is shown to the viewer.
        figure = self._figures.figure(game, look) if self._pages.shows_art(request, game) else None
        return figure_document(game, figure) if figure else None

    async def _kits(self, game: Game, language: str) -> dict[tuple[str, str], str]:
        # What each faction's classes start with, named from the catalogue once it is imported.
        rules = _rules(game)
        catalogue = await self._catalogues.of(game, language)
        uses = {int(tag.partition("-")[0]): count for tag, count in rules.starting_items.items()}
        kits = {}
        for faction, loadouts in rules.loadouts.items():
            for hero_class, loadout in loadouts.items():
                emotes = _count("web.creator.kit.emotes", len(loadout.emotes), language)
                if catalogue.imported:
                    items = [
                        _named(catalogue.name(item) or str(item), uses.get(item), language)
                        for item in loadout.equipment
                    ]
                else:
                    items = [_count("web.creator.kit.items", len(loadout.equipment), language)]
                kits[(faction, hero_class)] = listed([*items, emotes], language)
        return kits


def figure_document(game: Game, figure: Figure) -> dict[str, Any]:
    """Return a hero as the game's menu draws it, as the stage's script reads it.

    Files are named by their addresses; the pose names its clip, and the clips that the stage
    plays in each role are named by role.

    Args:
        game: The hero's game.
        figure: The hero, assembled from the game's models.
    """

    def address(group: str, name: str) -> str:
        return catalogue_file_url(game.id, figure.revision, file_path(group, name))

    rig = figure.rig
    role, time = figure.pose
    return {
        "skeleton": address("models", rig.model),
        "roles": rig.roles,
        "pose": [next(iter(rig.roles.get(role, ())), role), time],
        "parts": [address("models", name) for name in figure.parts],
        "layers": [
            {"texture": address("textures", layer.texture), "at": layer.at, "tint": layer.tint}
            for layer in figure.layers
        ],
        "atlas": figure.atlas,
        "warp": address("textures", figure.warp),
        "light": figure.light,
        "camera": figure.camera.model_dump(),
    }


def _named(name: str, uses: int | None, language: str) -> str:
    if uses is None:
        return name
    return translate_count("web.creator.kit.uses", uses, language) % {"item": name, "count": uses}


def _count(key: str, count: int, language: str) -> str:
    return translate_count(key, count, language) % {"count": count}


def _rules(game: Game) -> HeroRules:
    assert game.heroes is not None
    return game.heroes


def _from_game(values: Mapping[str, str]) -> bool:
    # The game opened the creator, which its page and the pages after it say.
    return values.get("from") == "game"
