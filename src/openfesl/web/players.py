from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from math import ceil
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from openfesl.core.census import Census
from openfesl.core.dogtags import DogTag, read_dog_tags
from openfesl.core.games import Game, Games
from openfesl.core.personas import Persona, PersonaStore
from openfesl.core.rankings import RankingStore
from openfesl.core.records import RecordStore
from openfesl.core.stats import PERSONA, Owner, bucket_of, partition_of
from openfesl.protocol.rank import Period
from openfesl.web.pages import Pages

TAGS_PAGE_SIZE = 50
# Each tab of a player's page, by the last part of its address, and the page that draws it.
TABS = {"stats": "PlayerStats", "awards": "PlayerAwards", "dog-tags": "PlayerDogTags"}


@dataclass(frozen=True, slots=True)
class Paged:
    """A page of a list whose address names nothing but the page.

    Attributes:
        page: The page, from 1.
    """

    page: int = 1

    def changed(self, **changes: Any) -> Paged:
        """Return the list at another page.

        Args:
            changes: The new page, as page.
        """
        return replace(self, **changes)

    def query(self) -> str:
        """Return the query of the list's address, with a leading ? unless it is empty."""
        return "" if self.page == 1 else f"?page={self.page}"


def player_routes(
    pages: Pages,
    games: Games,
    census: Census,
    stats: RankingStore,
    personas: PersonaStore,
    records: RecordStore,
) -> APIRouter:
    """Return the pages of each game's players: their stats, awards and the dog tags they took.

    Args:
        pages: Renders the pages.
        games: The games, each with its players' pages under its id.
        census: Knows who is online and on which server.
        stats: Holds the players' stats.
        personas: Finds players by name.
        records: Holds the records of players' games, such as their dog tags.
    """
    router = APIRouter()
    for game in games:
        for tab in TABS:
            suffix = "" if tab == "stats" else f"/{tab}"
            router.add_api_route(
                f"/{game.id}/players/{{name}}{suffix}",
                _page(pages, census, stats, personas, records, game, tab),
                response_class=HTMLResponse,
            )
    return router


def _page(
    pages: Pages,
    census: Census,
    stats: RankingStore,
    personas: PersonaStore,
    records: RecordStore,
    game: Game,
    tab: str,
) -> Callable[[Request, str], Awaitable[HTMLResponse]]:
    partition = partition_of(game)
    sums = game.stats.profile()

    async def show_player(request: Request, name: str) -> HTMLResponse:
        persona = await personas.find(game.namespace, name)
        activity = census.activity(game)
        if persona is None:
            return pages.render(
                request, "PlayerUnknown", 404, game=game, activity=activity, wanted=name
            )
        bucket = bucket_of(Period.ALL_TIME, 0)
        lines = await stats.lines(partition, sums, "score", bucket, [persona.id], slice(0, 1))
        values = lines[0].values if lines else dict.fromkeys(sums, 0.0)
        tags = await _dog_tags(records, game, persona)
        context: dict[str, Any] = {
            "game": game,
            "activity": activity,
            "persona": persona,
            "tab": tab,
            "values": values,
            "rank": game.stats.rank(values["score"]),
            "tags": len(tags),
        }
        if tab == "stats":
            places = census.places(game)
            keys = sorted({key for keys in game.stats.sums().values() for key in keys})
            context |= {
                "place": lines[0].place if lines else None,
                "everyone": await stats.count(partition, keys, bucket, None),
                "online": persona.id in places,
                "server": places.get(persona.id),
            }
        elif tab == "awards":
            awards = game.stats.awards
            codes = [*awards.pins, *awards.insignias]
            owner = Owner(PERSONA, persona.id)
            found = await stats.values(partition, [owner], [awards.key(c) for c in codes], bucket)
            counts = {
                code: int(stored.value)
                for code in codes
                if (stored := found.get((owner, awards.key(code)))) and stored.value >= 1
            }
            pins = [pin for pin in awards.pins if pin in counts]
            pins.sort(key=lambda pin: -counts[pin])
            context |= {
                "pins": pins,
                "pins_total": sum(counts[code] for code in pins),
                # Earned insignias come first, each group in the game's order.
                "insignias": sorted(awards.insignias, key=lambda code: code not in counts),
                "earned": sum(code in counts for code in awards.insignias),
                "counts": counts,
            }
        else:
            page_text = request.query_params.get("page", "")
            pages_count = max(ceil(len(tags) / TAGS_PAGE_SIZE), 1)
            page = min(int(page_text) if page_text.isdecimal() else 1, pages_count) or 1
            start = (page - 1) * TAGS_PAGE_SIZE
            context |= {
                "listed": tags[start : start + TAGS_PAGE_SIZE],
                "view": Paged(page),
                "pages": pages_count,
                "first": start + 1,
            }
        return pages.render(request, TABS[tab], **context)

    return show_player


async def _dog_tags(records: RecordStore, game: Game, persona: Persona) -> list[DogTag]:
    name = game.stats.dog_tag_record
    record = await records.find(partition_of(game), persona.id, name) if name else None
    return read_dog_tags(record.entries) if record else []
