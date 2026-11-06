from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException

from openfesl.core.admins import Admin, Role
from openfesl.core.deal_uploads import DealUploads, offer_of
from openfesl.core.deals import BIG, HOME, Deal
from openfesl.core.game_art import GameArt
from openfesl.core.games import Game
from openfesl.core.store import ProductStore
from openfesl.formats.deal_art import fits
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.deals import DealAdminViews
from openfesl.web.admin.store import SECTION, store_url
from openfesl.web.deal_art import DealArt
from openfesl.web.sessions import check_form, checked_form, or_404, toast
from openfesl.web.store_texts import StoreTexts

# The largest picture an upload takes; the game's own are a few hundred kilobytes at most.
MOST_PICTURE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class HomeDeal:
    """A hot deal as the game's home screen shows it, with the pictures it downloads.

    Attributes:
        deal: The deal, named in the page's language.
        offer: The name of the offer it sells, which uploaded pictures and pins go with.
        big: Whether the home screen shows its big picture rather than its small one.
        uploaded: Which of its pictures were uploaded: True for the big one, False for the small.
        pinned: Whether the admin pinned its offer, rather than the rule putting it there.
    """

    deal: Deal
    offer: str
    big: bool
    uploaded: set[bool]
    pinned: bool


class DealHomeViews:
    """Puts together the hot deals that the games' home screens show, with their pictures."""

    def __init__(
        self,
        deals: DealAdminViews,
        art: DealArt,
        uploads: DealUploads,
        products: ProductStore,
        game_art: GameArt,
    ) -> None:
        """Read the home screens' deals and keep the pictures and pins that the admin sets.

        Args:
            deals: The games' hot-deal rotations, which the home screens show the first deals of.
            art: Makes the deals' pictures, or gives the uploaded ones.
            uploads: Where the pictures that the admin uploaded are kept.
            products: Hold the bundles, sold now or not, whose pictures are uploaded.
            game_art: Says to whom each game's art is shown, the game among them.
        """
        self.deals = deals
        self.art = art
        self.uploads = uploads
        self._products = products
        self.game_art = game_art

    async def home(self, game: Game, language: str) -> list[HomeDeal]:
        """Return the hot deals that the game's home screen shows, with their pictures.

        Args:
            game: The game, which has hot deals.
            language: The language of the deals' names.
        """
        rules = await self.deals.rules(game)
        uploaded = await self.uploads.uploaded(game.id)
        shown = (await self.deals.deals.hot_deals(game, language))[:HOME]
        return [
            HomeDeal(
                deal,
                offer_of(deal.listed.bundle),
                index < BIG,
                uploaded.get(offer_of(deal.listed.bundle), set()),
                offer_of(deal.listed.bundle) in rules.pinned,
            )
            for index, deal in enumerate(shown)
        ]

    async def deal(self, game: Game, number: int, language: str) -> HomeDeal:
        """Return a hot deal of a game's store by its bundle number, whether it is sold now or not.

        Args:
            game: The game, which has hot deals.
            number: The deal's bundle number.
            language: The language of the deal's name.

        Raises:
            HTTPException: 404 for a number the store has no bundle by.
        """
        listed = or_404(await self._products.bundle(game.id, number))
        rules = await self.deals.rules(game)
        offer = offer_of(listed.bundle)
        uploaded = (await self.uploads.uploaded(game.id)).get(offer, set())
        deal = await self.deals.deals.deal(game, listed, language)
        return HomeDeal(deal, offer, False, uploaded, offer in rules.pinned)


def deal_home_routes(sessions: AdminSessions, views: DealHomeViews) -> APIRouter:
    """Return the hot deals that each game's home screen shows, their pins and their pictures.

    Every role sees them and the pictures the game downloads; pinning, uploading and resetting
    need the admin role. A pinned offer comes first in its section and right after the home
    screen's big deals; an uploaded picture goes with the deal's offer and takes the generated
    one's place.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the pages show and keep the uploads and pins.
    """
    router = APIRouter()
    path = "/admin/store/{game_id}/deals/art/{number}"

    @router.get("/admin/store/{game_id}/deals/home", response_class=HTMLResponse)
    async def home(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        return await home_page(sessions, views, request, admin, views.deals.stores.game(game_id))

    @router.post("/admin/store/{game_id}/deals/home/{number}/pin")
    async def pin(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        form = await checked_form(request)
        game = views.deals.stores.game(game_id)
        shown = await views.deal(game, number, request.state.language)
        on = bool(form.get("pinned"))
        bundle = shown.deal.listed.bundle
        await views.deals.pin(game, shown.offer, bundle.faction, on, admin)
        toast(request, f"web.admin.toast.deals.pin.{'on' if on else 'off'}", deal=shown.deal.name)
        return RedirectResponse(store_url(request, game, "/deals/home"), 303)

    @router.get(path + "_{size}.png")
    async def picture(request: Request, game_id: str, number: int, size: str) -> Response:
        sessions.admin(request)
        game = views.deals.stores.game(game_id)
        if size not in ("big", "small"):
            raise HTTPException(404)
        # The pictures the game downloads, as it counts as signed in through its session.
        shown = views.game_art.shown(game, signed_in=True)
        made = or_404(await views.art.picture(game, number, size == "big", shown))
        return Response(made, media_type="image/png", headers={"Cache-Control": "no-store"})

    @router.post(path)
    async def upload(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.deals.stores.game(game_id)
        form = await request.form(max_files=2, max_fields=3)
        try:
            check_form(request, form)
            files = {
                big: (sent.filename or "", await sent.read(MOST_PICTURE + 1))
                for big, sent in ((True, form.get("big")), (False, form.get("small")))
                if isinstance(sent, UploadFile) and sent.filename
            }
        finally:
            await form.close()
        # An admin offer's editor sends its pictures too, and gets its own page back.
        offer = bool(form.get("offer"))
        back = f"/deals/offers/{number}" if offer else "/deals/home"
        shown = await views.deal(game, number, request.state.language)
        texts = StoreTexts(game, request.state.language)
        for big, (name, data) in files.items():
            if len(data) > MOST_PICTURE or not fits(data, big):
                key = f"web.admin.deals.art.refused.{'big' if big else 'small'}"
                if offer:
                    toast(request, key, error=True, file=name)
                    return RedirectResponse(store_url(request, game, back), 303)
                why = (number, texts.text(key, file=name))
                return await home_page(sessions, views, request, admin, game, why)
        for big, (_, data) in files.items():
            await views.uploads.put(game.id, shown.offer, big, data, admin)
        if files:
            toast(request, "web.admin.toast.deals.art", deal=shown.deal.name)
        return RedirectResponse(store_url(request, game, back), 303)

    @router.post(path + "/reset")
    async def reset(request: Request, game_id: str, number: int) -> Response:
        admin = sessions.admin(request, Role.ADMIN, SECTION)
        game = views.deals.stores.game(game_id)
        form = await checked_form(request)
        shown = await views.deal(game, number, request.state.language)
        if await views.uploads.remove(game.id, shown.offer, admin):
            toast(request, "web.admin.toast.deals.art.reset", deal=shown.deal.name)
        back = f"/deals/offers/{number}" if form.get("offer") else "/deals/home"
        return RedirectResponse(store_url(request, game, back), 303)

    return router


async def home_page(
    sessions: AdminSessions,
    views: DealHomeViews,
    request: Request,
    admin: Admin,
    game: Game,
    art: tuple[int, str] | None = None,
) -> HTMLResponse:
    """Render the hot deals of a game's home screen, with a refused picture if there is one.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: Put together what the page shows.
        request: The request the page answers.
        admin: The signed-in admin.
        game: The game, which has hot deals.
        art: The deal whose uploaded picture was refused, by its bundle's number, and why.
    """
    return await sessions.render(
        request,
        "admin.DealHome",
        400 if art else 200,
        admin=admin,
        of=game,
        home=await views.home(game, request.state.language),
        art=art,
    )
