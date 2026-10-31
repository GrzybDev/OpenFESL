from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from openfesl.core.calendar import next_start, today
from openfesl.core.deal_picks import NewDeal
from openfesl.core.deal_rules import DealRules, DealSection, OfferSection
from openfesl.core.deals import HotDeals
from openfesl.core.games import Game
from openfesl.core.store import AUTO, Bundle, ListedBundle


@dataclass(frozen=True, slots=True)
class Step:
    """A section's next step as the rotation would make it now.

    Attributes:
        at: When it comes.
        deals: The deals its periods that start then bring; another price, pool or section's
            pick before then may change them.
        ending: The section's bundles on sale now that end then.
        short: The factions it brings fewer deals of than the section's size asks, with how
            many, where it starts a period that picks a few of each faction's.
    """

    at: datetime
    deals: list[NewDeal]
    ending: list[ListedBundle]
    short: dict[str, int]


class DealSteps:
    """The steps of the games' hot-deal rotations: what each section's next one brings."""

    def __init__(self, deals: HotDeals, clock: Callable[[], datetime] | None = None) -> None:
        """Read the steps of the rotations that hot deals run.

        Args:
            deals: The hot deals, which run the rotations.
            clock: Tells the time; the system's own when none.
        """
        self._deals = deals
        self._clock = clock or (lambda: datetime.now(UTC))

    async def preview(self, game: Game) -> dict[str, Step]:
        """Return what each section that runs brings and ends at its next step, by section.

        The step draws the same picks when what it reads stays as it is now; other sections'
        steps at the same moment are not made yet, so their picks are not left out.

        Args:
            game: The game.
        """
        now = self._clock()
        rules = await self._deals.rules(game)
        if rules is None or not rules.enabled:
            return {}
        listed = await self._deals.current(game)
        changes = {
            name: at
            for name, section in rules.sections.items()
            if section.enabled and (at := next_change(rules, section, now))
        }
        steps = {}
        for name, deals in (await self._deals.draw(game, rules, changes)).items():
            section, at = rules.sections[name], changes[name]
            ending = [
                found
                for found in listed
                if found.bundle.section == name
                and found.bundle.source == AUTO
                and found.bundle.ends is not None
                and found.bundle.ends <= at
            ]
            # Only a step that starts a period of the section's schedule picks its deals anew.
            picks = (
                section.schedule is not None and next_start(section.schedule, now, rules.zone) == at
            )
            fewer = short(game, section, [deal.bundle for deal in deals]) if picks else {}
            steps[name] = Step(at, deals, ending, fewer)
        return steps


def next_change(rules: DealRules, section: DealSection, now: datetime) -> datetime | None:
    """Return when a section next brings or ends deals, if it ever does.

    That is its schedule's next period, or the start or the end of one of its offers' windows.

    Args:
        rules: The game's hot-deal rules.
        section: The section.
        now: The moment after which it changes.
    """
    found = [next_start(section.schedule, now, rules.zone)] if section.schedule else []
    year = today(now, rules.zone).year
    offers = rules.offers(section) if isinstance(section, OfferSection) else ()
    for window in {given.window for given in offers if given.window is not None}:
        if running := window.running(now, rules.zone):
            found.append(running.end)
        runs = (window.period(year + ahead, rules.zone) for ahead in (0, 1))
        found += [run.start for run in runs if run.start > now][:1]
    return min(found, default=None)


def short(game: Game, section: DealSection, deals: Iterable[Bundle]) -> dict[str, int]:
    """Return the factions that a step of a section brings fewer deals of than its size asks.

    Only a section that picks a few deals of each faction's falls short.

    Args:
        game: The game, whose heroes' factions the deals are for.
        section: The section.
        deals: The deals that the step brings.

    Returns:
        How many deals each of those factions gets.
    """
    if section.size is None or game.heroes is None:
        return {}
    if isinstance(section, OfferSection) and not section.sets:
        return {}
    counts = dict.fromkeys(game.heroes.factions, 0)
    for deal in deals:
        if deal.faction in counts:
            counts[deal.faction] += 1
    return {faction: count for faction, count in counts.items() if count < section.size}
