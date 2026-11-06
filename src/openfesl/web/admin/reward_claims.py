from dataclasses import dataclass, replace
from datetime import date, datetime
from math import ceil
from typing import Any, Protocol, Self

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import QueryParams

from openfesl.core.calendar import day_start, today
from openfesl.core.reward_claims import ListedClaim
from openfesl.core.reward_rules import Gift
from openfesl.core.rewards import FIRST_CHEST, REFERRAL, claimed
from openfesl.web.admin.auth import AdminSessions
from openfesl.web.admin.rewards import RewardAdminViews
from openfesl.web.pages import page_number, query_of

PAGE_SIZE = 50


class ClaimLog(Protocol):
    """Lists the rewards that accounts claimed."""

    async def kinds(self, game: str, since: datetime) -> dict[str, int]:
        """Return how many rewards of each kind accounts claimed since a moment.

        Args:
            game: The game's data-file name.
            since: The moment.
        """

    async def listed(
        self, game: str, since: datetime, kind: str, offset: int, limit: int
    ) -> tuple[int, list[ListedClaim]]:
        """Return the rewards that accounts claimed since a moment, the latest first.

        Args:
            game: The game's data-file name.
            since: The moment.
            kind: The start of the rewards' keys; every reward when empty.
            offset: How many claims to skip.
            limit: The most to return.
        """


@dataclass(frozen=True, slots=True)
class ClaimListing:
    """Which claims the list shows, as its address says; what is empty does not filter.

    Attributes:
        reward: The kind of the rewards, such as an event's key, starter or milestone.
        page: The page of the list, from 1.
    """

    reward: str = ""
    page: int = 1

    @classmethod
    def read(cls, query: QueryParams) -> Self:
        """Read the list's address; what it does not name is the default.

        Args:
            query: The address's query.
        """
        return cls(query.get("reward", "")[:64], page_number(query.get("page", "")))

    def changed(self, **changes: Any) -> ClaimListing:
        """Return the list with some choices changed.

        Args:
            changes: The new values by attribute.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        return query_of(("reward", self.reward), ("page", str(self.page) if self.page > 1 else ""))


@dataclass(frozen=True, slots=True)
class ClaimRow:
    """A claim as the list shows it.

    Attributes:
        listed: The claim with its account and hero.
        chip: The reward's number in the panel, such as E4, M2 or K1; empty for a starter pack.
        name: The reward's name.
        given: What it gave the hero and the account, in words.
    """

    listed: ListedClaim
    chip: str
    name: str
    given: str


def reward_claim_routes(
    sessions: AdminSessions, views: RewardAdminViews, log: ClaimLog
) -> APIRouter:
    """Return the list of this year's claims of each game's rewards, which every role reads.

    The list is the newest first, 50 a page, filtered by the kind of reward with how many claims
    each kind has; a box's roll shows the item it gave.

    Args:
        sessions: Signs admins in and renders the panel's pages.
        views: The games' rewards.
        log: Lists the claims.
    """
    router = APIRouter()

    @router.get("/admin/rewards/{game_id}/claims", response_class=HTMLResponse)
    async def claims(request: Request, game_id: str) -> HTMLResponse:
        admin = sessions.admin(request)
        game = views.game(game_id)
        listing = ClaimListing.read(request.query_params)
        words = await views.words(game, request.state.language)
        texts, rules = words.texts, words.rules
        since = day_start(date(today(words.now, rules.zone).year, 1, 1), rules.zone)
        counts = await log.kinds(game.id, since)
        offset = (listing.page - 1) * PAGE_SIZE
        total, found = await log.listed(game.id, since, listing.reward, offset, PAGE_SIZE)
        events, milestones = list(rules.events), list(rules.milestones)
        rows = []
        for listed in found:
            claim = listed.claim
            kind, _, rest = claim.key.partition(":")
            chip = ""
            if kind in rules.events:
                chip = f"E{events.index(kind) + 1}"
            elif kind == "milestone" and (key := rest.partition(":")[0]) in milestones:
                chip = f"M{milestones.index(key) + 1}"
            elif kind == FIRST_CHEST:
                chip = "K1"
            named = claimed(rules, claim)
            name = words.past(named).name
            if name == named.name and kind in rules.events:
                # Before the game's texts are imported, the event's own name stands in.
                name = texts.text(rules.events[kind].title)
            gifts = [words.item(*item) for item in claim.items]
            gifts += words.extra(Gift(funds=claim.funds, points=claim.points, keys=claim.keys))
            rows.append(ClaimRow(listed, chip, name, ", ".join(gifts)))
        kinds = [*rules.events, "starter", "milestone", FIRST_CHEST, REFERRAL]
        filters = []
        for kind in kinds:
            if counts.get(kind) or kind == listing.reward:
                event = rules.events.get(kind)
                label = (
                    f"E{events.index(kind) + 1} {texts.text(event.title)}"
                    if event
                    else texts.text(f"web.admin.rewards.claims.kind.{kind}")
                )
                filters.append((kind, label, counts.get(kind, 0)))
        return await sessions.render(
            request,
            "admin.RewardClaims",
            admin=admin,
            of=game,
            listing=listing,
            rows=rows,
            total=total,
            year=sum(counts.values()),
            filters=filters,
            pages=ceil(total / PAGE_SIZE),
            size=PAGE_SIZE,
            pause=await views.pause(game),
            paused=rules.paused,
        )

    return router
