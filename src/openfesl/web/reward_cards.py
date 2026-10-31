from dataclasses import dataclass

from starlette.requests import Request

from openfesl.core.calendar import today
from openfesl.core.catalogues import Catalogues
from openfesl.core.entitlements import EntitlementStore
from openfesl.core.games import Game
from openfesl.core.reward_rules import Gift, Goal, RewardRules
from openfesl.core.reward_runs import Run
from openfesl.core.rewards import FIRST_CHEST, REFERRAL, Reward, RewardState
from openfesl.core.store import ProductStore
from openfesl.web import referral_texts
from openfesl.web.reward_claims import Claiming, RewardClaims
from openfesl.web.reward_texts import RewardTexts, reward_texts

# The days before a starter pack's last one from which its card says in red that time runs out.
SOON = 3
# The page's sections in order: the daily gifts of the events that run, their other gifts, their
# missions, the new heroes' starter packs, the milestones, Recruit a Friend and the first chest's
# key.
SECTIONS = ("today", "events", "missions", "heroes", "milestones", "referral", "key")


@dataclass(frozen=True, slots=True)
class Line:
    """A row of a reward's card: how far a hero or the account got, or what came of a claim.

    Attributes:
        name: Whose row it is.
        text: How far it got, or when it was claimed.
        done: Whether the row's reward was claimed.
        share: How far it got towards the goal, from 0 to 1, for its bar; none without a bar.
        claim: How the row's own reward is claimed, once reached.
    """

    name: str
    text: str
    done: bool = False
    share: float | None = None
    claim: Claiming | None = None


@dataclass(frozen=True, slots=True)
class Card:
    """A reward as its card on the rewards page shows it.

    Attributes:
        id: The card's id on the page.
        band: Whom it is for: a faction, a hero or the account.
        faction: The faction whose colours the band wears; none for the account's.
        glyph: The glyph of what it gives.
        icon: The address of the icon of the first item it gives, where the page shows the
            game's art; none for any other.
        title: Its name.
        text: What it is about.
        gifts: What it gives, a line each.
        when: Its day of a daily event or until when it runs; empty when it always does.
        lines: The heroes' or the account's progress or claims, a row each.
        state: Whether and until when it may be claimed, or what came of its claim.
        tone: The state's kind, which picks its glyph and colour.
        claim: How it is claimed now; none when it may not be, or its rows are claimed.
        needs: What gets it the hero it lacks: create a hero in a free slot, or buy a slot.
        mark: The glyph of its band where it is for no faction.
        name: A milestone's name, which states its goal, and that each hero counts for it
            where each does.
        each: Whether each hero counts for a milestone on its own.
        amount: The Funds a milestone gives, for each hero where each counts.
        best: How far the hero got that came closest to a milestone that each hero counts for,
            while none reached it.
    """

    id: str
    band: str
    faction: str | None
    glyph: str
    icon: str | None
    title: str
    text: str
    gifts: list[str]
    when: str
    lines: list[Line]
    state: str
    tone: str
    claim: Claiming | None = None
    needs: str | None = None
    mark: str = "user"
    name: str = ""
    each: bool = False
    amount: str = ""
    best: Line | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """A section of the rewards page, under its plaque.

    Attributes:
        key: Its name, which the ids of its texts hold.
        lead: What it says under its plaque.
        cards: Its rewards.
    """

    key: str
    lead: str
    cards: list[Card]


class RewardCards:
    """The cards that pages show of a game's rewards."""

    def __init__(self, words: RewardTexts, claims: RewardClaims, products: ProductStore) -> None:
        """Show a game's rewards to an account, or to a visitor signed out.

        Args:
            words: What the page says of the rewards.
            claims: How the rewards are claimed.
            products: Hold the boxes that gifts give from.
        """
        self.words = words
        self.claims = claims
        self._products = products

    async def sections(self, available: list[Reward], running: list[Run]) -> list[Section]:
        """Return the page's sections with their rewards' cards, leaving out empty ones.

        Args:
            available: The rewards the account sees now.
            running: The events that run now.
        """
        texts = self.words.texts
        events = {run.key: run for run in running}
        found: dict[str, list[Card]] = {key: [] for key in SECTIONS}
        for reward in available:
            kind = reward.key.partition(":")[0]
            if kind == "milestone":
                continue
            if kind == REFERRAL:
                found["referral"].append(await self._referral(reward))
                continue
            card = await self._card(reward, events)
            run = events.get(reward.event or "")
            if run:
                daily, goal = run.event.daily, reward.goal is not None
                found["today" if daily else "missions" if goal else "events"].append(card)
            else:
                found["heroes" if kind == "starter" else "key"].append(card)
        found["milestones"] = await self.milestones(available) if available else []
        leads = {key: texts.text(f"web.rewards.section.{key}.lead") for key in SECTIONS[2:5]}
        leads["key"] = texts.text("web.rewards.section.key.lead")
        leads["referral"] = referral_texts.lead(self.words, available)
        today_lead, events_lead = [], []
        for run in running:
            event = self.words.event(run)
            if run.event.daily:
                today_lead.append(
                    texts.text(
                        "web.rewards.section.today.lead",
                        event=event.name,
                        day=run.day,
                        days=run.days,
                        summary=event.summary,
                    )
                )
            elif not any(part.goal for part in run.event.parts.values()):
                events_lead.append(
                    texts.text(
                        "web.rewards.section.events.lead",
                        event=event.name,
                        dates=event.dates,
                        summary=event.summary,
                    )
                )
        leads |= {"today": " ".join(today_lead), "events": " ".join(events_lead)}
        return [Section(key, leads[key], cards) for key, cards in found.items() if cards]

    async def _card(self, reward: Reward, events: dict[str, Run]) -> Card:
        words, texts, gift = self.words, self.words.texts, reward.gift
        run = events.get(reward.event or "")
        when = ""
        if run and reward.kept:
            last = words.dated(words.last_day(reward))
            when = texts.text("web.rewards.when.kept", day=reward.day, days=run.days, date=last)
        elif run and reward.day and reward.ends:
            ends = words.clock(reward.ends)
            when = texts.text("web.rewards.when.day", day=reward.day, days=run.days, ends=ends)
        elif run and reward.ends and reward.goal is None:
            when = texts.text("web.rewards.when.until", date=words.dated(words.last_day(reward)))
        # The goal of the reward's part of its event, whose text says how far its hero got.
        parts = run.event.parts.values() if run else []
        goal = next((part.goal for part in parts if part.faction == reward.faction), None)
        lines = []
        if goal and reward.hero and (hero := words.hero(reward.hero)):
            lines.append(self._progress(hero.persona.name, reward, goal.progress))
        state, tone, needs = self._state(reward, goal)
        claim = (
            await self.claims.claiming(reward) if reward.state is RewardState.CLAIMABLE else None
        )
        owner = words.hero(reward.hero) if reward.goal is None else None
        gifts = [words.item(item_id, ownership) for item_id, ownership in gift.items]
        box = await self._products.box_by_key(words.game.id, gift.box) if gift.box else None
        if box and box.worth_rule:
            worth = texts.priced(box.value, box.currency)
            gifts.append(texts.text("web.rewards.gift.box.worth", worth=worth))
        elif box:
            gifts.append(texts.text("web.rewards.gift.box"))
        return Card(
            id=f"reward-{reward.key}-{reward.faction or 'account'}".replace(":", "-"),
            band=words.band(reward),
            faction=reward.faction or (owner.faction if owner else None),
            glyph=_glyph(gift),
            icon=words.icon(gift.items[0][0]) if gift.items else None,
            title=words.title(reward),
            text=words.description(reward),
            gifts=gifts + words.extra(gift),
            when=when,
            lines=lines,
            state=state,
            tone=tone,
            claim=claim,
            needs=needs,
        )

    async def _referral(self, reward: Reward) -> Card:
        # A step of Recruit a Friend for the recruiter or the recruit: what reaching it means,
        # how far the recruit's best hero got while it waits, and whether it is ready, claimed
        # or ended.
        words, texts, referral = self.words, self.words.texts, reward.referral
        assert referral is not None
        gift, leader = reward.gift, referral.recruit.leader
        state, tone = referral_texts.standing(words, reward)
        lines = []
        if referral.step not in referral.recruit.reached and reward.goal and not reward.claim:
            goal = reward.goal
            if leader:
                shown = texts.text("web.rewards.progress.level", count=leader.level, goal=goal)
                lines.append(Line(leader.name, shown, share=min(leader.level / goal, 1.0)))
            else:
                lines.append(Line(texts.text("web.bar.no_hero"), "", share=0.0))
        claim = (
            await self.claims.claiming(reward) if reward.state is RewardState.CLAIMABLE else None
        )
        return Card(
            id=f"reward-{reward.key}".replace(":", "-"),
            band=referral_texts.band(words, reward),
            faction=None,
            glyph="valor" if gift.points else _glyph(gift),
            icon=None,
            title=words.title(reward),
            text=referral_texts.story(words, reward),
            gifts=[],
            when="",
            lines=lines,
            state=state,
            tone=tone,
            claim=claim,
            mark="user-plus",
        )

    def _progress(self, name: str, reward: Reward, text: str) -> Line:
        count, goal = reward.progress or 0, reward.goal or 1
        texts = self.words.texts
        shown = texts.text(text, count=texts.amount(count), goal=texts.amount(goal))
        return Line(name, shown, share=min(count / goal, 1.0))

    def _state(self, reward: Reward, goal: Goal | None) -> tuple[str, str, str | None]:
        # The card's state line, its kind, and what gets the hero that a locked reward needs.
        words, texts, rules = self.words, self.words.texts, self.words.rules
        if reward.state is RewardState.CLAIMED:
            assert reward.claim is not None
            said = words.given(reward.claim)
            if reward.day and reward.ends:
                said += " " + texts.text("web.rewards.state.next", time=words.clock(reward.ends))
            return said, "claimed", None
        if reward.state is RewardState.EXPIRED and reward.ends and rules.starter:
            on = words.dated(words.last_day(reward))
            expired = texts.count("web.rewards.state.expired", rules.starter.days, date=on)
            return expired, "expired", None
        hero = words.hero(reward.hero)
        if reward.state is RewardState.CLAIMABLE:
            if reward.key.startswith("starter:") and reward.ends and hero:
                last = words.last_day(reward)
                date, left = words.dated(last), (last - today(words.now, rules.zone)).days
                if left >= SOON:
                    starter = texts.text(
                        "web.rewards.state.starter", hero=hero.persona.name, date=date
                    )
                    return starter, "ready", None
                if left:
                    return (
                        texts.count("web.rewards.state.soon", left, date=date),
                        "soon",
                        None,
                    )
                return texts.text("web.rewards.state.last_day", date=date), "soon", None
            if reward.goal is not None and hero:
                ends = (
                    f"{words.day(reward.ends)}, {words.clock(reward.ends)}" if reward.ends else ""
                )
                met = texts.text("web.rewards.state.goal_met", hero=hero.persona.name, when=ends)
                return met, "ready", None
            names = [hero.persona.name for p in reward.heroes if (hero := words.hero(p))]
            if names:
                ready = texts.text("web.rewards.state.ready", heroes=texts.alternatives(names))
                return ready, "ready", None
            return texts.text("web.rewards.state.ready.account"), "ready", None
        if goal and reward.heroes:
            ends = f"{words.day(reward.ends)}, {words.clock(reward.ends)}" if reward.ends else ""
            return texts.text("web.rewards.state.progress", when=ends), "waiting", None
        if reward.key == FIRST_CHEST:
            return texts.text("web.rewards.state.chest"), "waiting", None
        if not reward.faction or reward.hero is not None or words.roster is None:
            return texts.text("web.rewards.state.unfit"), "locked", None
        faction = texts.factions(frozenset({reward.faction}))
        if goal and goal.hero_class:
            hero_class = texts.classes(frozenset({goal.hero_class}))
            said = texts.text(
                "web.rewards.state.needs.class", faction=faction, hero_class=hero_class
            )
        else:
            said = texts.text("web.rewards.state.needs", faction=faction)
        if words.roster.free:
            slot = texts.text("web.rewards.state.slot", slot=len(words.roster.heroes) + 1)
            return f"{said} {slot}", "locked", "create"
        heroes = words.heroes
        assert heroes is not None
        price = texts.priced(heroes.slot_price, heroes.currency)
        full = texts.text("web.rewards.state.full", count=words.roster.slots, price=price)
        return f"{said} {full}", "locked", "slot"

    async def milestones(self, available: list[Reward]) -> list[Card]:
        """Return a card for each milestone with the rows of the heroes or the account.

        A hero that counts on its own gets a row, else the account's heroes together get one;
        without rewards, as a visitor signed out sees them, the cards have no rows.

        Args:
            available: The rewards the account sees now.
        """
        words, texts, rules = self.words, self.words.texts, self.words.rules
        grouped: dict[str, list[Reward]] = {
            key: [] for key, milestone in rules.milestones.items() if milestone.enabled
        }
        for reward in available:
            kind, _, rest = reward.key.partition(":")
            if kind == "milestone":
                grouped[rest.partition(":")[0]].append(reward)
        cards = []
        for key, rewards in grouped.items():
            milestone = rules.milestones[key]
            lines = []
            for reward in rewards:
                hero = words.hero(reward.hero)
                whose = "web.rewards.row.all" if milestone.stats else "web.rewards.band.account"
                name = hero.persona.name if hero else texts.text(whose)
                if reward.claim:
                    # Claimed once for good, maybe in an earlier year, which the day states.
                    day = words.day(reward.claim.at, "yMMMd")
                    lines.append(Line(name, texts.text("web.rewards.row.claimed", date=day), True))
                elif reward.state is RewardState.CLAIMABLE:
                    ready = texts.text("web.rewards.row.ready")
                    lines.append(Line(name, ready, claim=await self.claims.claiming(reward)))
                else:
                    lines.append(self._progress(name, reward, milestone.progress))
            named = texts.text(milestone.name).format(goal=texts.amount(milestone.goal))
            funds = words.funds(milestone.gift.funds)
            # Where each hero counts and none reached it yet, the one that got furthest.
            reached = any(line.done or line.claim for line in lines)
            best = None
            if milestone.each and lines and not reached:
                top = max(lines, key=lambda line: line.share or 0)
                best = Line(
                    top.name,
                    texts.text("web.funds.rewards.best", hero=top.name, progress=top.text),
                    share=top.share,
                )
            cards.append(
                Card(
                    id=f"reward-milestone-{key}",
                    band=texts.text("web.rewards.band.account"),
                    faction=None,
                    glyph="flag" if milestone.stats and not milestone.each else "star",
                    icon=None,
                    title=words.milestone_title(milestone),
                    text=texts.text(milestone.description) if milestone.description else "",
                    gifts=[],
                    when="",
                    lines=lines,
                    state=texts.text(
                        "web.rewards.state.funds", currency=texts.currency(rules.currency)
                    ),
                    tone="funds",
                    name=texts.text("web.funds.rewards.each", name=named)
                    if milestone.each
                    else named,
                    each=milestone.each,
                    amount=texts.text(
                        "web.funds.rewards.amount.each"
                        if milestone.each
                        else "web.funds.rewards.amount",
                        amount=funds,
                    ),
                    best=best,
                )
            )
        return cards


async def reward_cards(
    request: Request,
    game: Game,
    rules: RewardRules,
    catalogues: Catalogues,
    entitlements: EntitlementStore,
    products: ProductStore,
    art: bool,
) -> RewardCards:
    """Return the cards a page shows of a game's rewards to its viewer, in the page's language.

    Args:
        request: The page's request, whose bar holds the signed-in account's heroes.
        game: The game.
        rules: How the game gives rewards, with the instance's settings.
        catalogues: Name and draw the items the rewards give, once imported.
        entitlements: Hold what the heroes own, which says what each would get.
        products: Hold the boxes that gifts give from.
        art: Whether the page shows the game's art to its viewer.
    """
    words = await reward_texts(request, game, rules, catalogues, art)
    return RewardCards(words, RewardClaims(words, entitlements, products), products)


def _glyph(gift: Gift) -> str:
    # The glyph of what a reward gives: a box's drop, a hero's items, keys or Funds.
    if gift.box and not gift.items:
        return "box"
    if gift.items:
        return "gift"
    return "key" if gift.keys else "coin"
