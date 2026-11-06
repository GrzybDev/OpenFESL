from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.catalogues import Catalogues, game_text
from openfesl.core.deal_rules import ItemSection
from openfesl.core.deal_steps import LoggedStep, StepDeal
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.deals import DealAdminViews
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class LogRow:
    """A step of the hot-deal rotation as its log lists it.

    Attributes:
        step: The step.
        came: The deals it brought, in words.
        left: The deals that ended at it, in words.
    """

    step: LoggedStep
    came: list[str]
    left: list[str]


def log_routes(sessions: AdminSessions, views: DealAdminViews, catalogues: Catalogues) -> APIRouter:
    """Return the log of each game's hot-deal rotation, which every role reads.

    The rotation's steps are not admin actions, so the log keeps them apart from the admin's
    log, for 90 days, each with what came in and what left, and whether a faction got fewer
    deals than its section asks.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: The games' hot-deal rotations.
        catalogues: The games' imported catalogues, which name the deals' items.
    """
    router = APIRouter()

    @router.get("/admin/store/{game_id}/deals/log", response_class=HTMLResponse)
    async def log(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.stores.game(game_id)
        language = request.state.language
        rotation = await views.rotation(game)
        texts = StoreTexts(game, language)
        catalogue = await catalogues.of(game, language)

        def said(key: str, deal: StepDeal) -> str:
            # A deal by its items where its section puts items together, else by its name.
            if isinstance(rotation.rules.sections.get(key), ItemSection):
                name = ", ".join(catalogue.name(item) or str(item) for item in deal.items)
            else:
                name = game_text(game, catalogue, deal.name, language, deal.faction)
            if deal.faction is None:
                return name
            faction = texts.factions(frozenset({deal.faction}))
            return texts.text("web.admin.deals.log.deal", name=name, faction=faction)

        rows = [
            LogRow(
                step,
                [said(step.section, deal) for deal in step.came],
                [said(step.section, deal) for deal in step.left],
            )
            for step in await views.steps.log(game)
        ]
        return await sessions.render(
            request, "admin.DealLog", admin=admin, of=game, rotation=rotation, rows=rows
        )

    return router
