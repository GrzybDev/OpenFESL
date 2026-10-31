from dataclasses import dataclass

from openfesl.core.calendar import day_start, today
from openfesl.core.games import Game
from openfesl.core.referrals import ReferralCounts, ReferralStore
from openfesl.core.reward_rules import ReferralStep
from openfesl.web.referral_texts import NUMBERS
from openfesl.web.reward_texts import RewardTexts


@dataclass(frozen=True, slots=True)
class StepRow:
    """A step of Recruit a Friend after the sign-up, as the rewards page's section lists it.

    Attributes:
        name: The step's name.
        number: The number players see it by.
        goal: Its goal in words.
        gifts: What the recruiter and the recruit get in words, each with where its name comes
            from and the numbers of its items: the id of the game's text that names it, empty
            where the gift's own words or the admin's text name it.
    """

    name: str
    number: int
    goal: str
    gifts: list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class ReferralView:
    """Recruit a Friend of a game as the rewards page's section shows it.

    Attributes:
        running: Whether it runs.
        steps: Its steps after the sign-up.
        counts: How it goes.
    """

    running: bool
    steps: list[StepRow]
    counts: ReferralCounts


async def overview(game: Game, words: RewardTexts, store: ReferralStore) -> ReferralView | None:
    """Return a game's Recruit a Friend for its rewards page; none for a game without it.

    Args:
        game: The game.
        words: Writes the page's language, with the rewards as the instance sets them.
        store: Where the recruits are kept, which it counts.
    """
    rules = words.rules.referral
    if rules is None:
        return None
    steps = []
    for name, step in rules.steps.items():
        gifts = []
        for recruiter in (True, False):
            side = step.recruiter if recruiter else step.recruit
            named = words.referral_name(name, recruiter)
            shown = [side.name] if words.catalogue.text(side.name) == named else []
            shown += [words.texts.text("web.admin.items.number", id=i) for i, _ in side.gift.items]
            gifts.append((named, " · ".join(shown)))
        steps.append(StepRow(name, NUMBERS[name], goal(words, step), gifts))
    month = day_start(today(words.now, words.rules.zone).replace(day=1), words.rules.zone)
    return ReferralView(rules.enabled, steps, await store.counts(game.id, month))


def goal(words: RewardTexts, step: ReferralStep) -> str:
    """Return a step's goal in words, such as a hero at level 15 within 60 days of sign-up.

    Args:
        words: Writes the page's language.
        step: The step.
    """
    key = "web.admin.rewards.referral.goal" + (".window" if step.days else "")
    return words.texts.text(key, level=step.level, days=step.days or 0)
