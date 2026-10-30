from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from openfesl.core.calendar import day_start, today
from openfesl.core.entitlements import ACTIVE, EntitlementStore
from openfesl.core.hero_items import held, holding
from openfesl.core.heroes import Hero
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED
from openfesl.core.reward_claims import Given
from openfesl.core.reward_rules import Gift
from openfesl.core.rewards import Reward, RewardRefusal, gives_box
from openfesl.core.store import ProductStore
from openfesl.web.reward_texts import RewardTexts
from openfesl.web.store_texts import StoreTexts


@dataclass(frozen=True, slots=True)
class Choice:
    """One of the account's heroes that a reward's items may go to, as the claim form offers it.

    Attributes:
        hero: The hero.
        status: What the hero would get.
        checked: Whether the form picks it at first: the default hero, or else the first.
    """

    hero: Hero
    status: str
    checked: bool


@dataclass(frozen=True, slots=True)
class Claiming:
    """How a reward is claimed.

    Attributes:
        key: What the reward is claimed by.
        faction: The faction of its part; empty for the account's own.
        title: The reward's name.
        label: What follows Claim for screen readers, naming the reward and whose it is.
        hero: The hero its items go to without a choice; none when the account picks one or it
            gives a hero nothing.
        choices: The heroes its items may go to, with what each would get; empty without a
            choice.
        hint: Which heroes may have it and which one the form picks; empty without choices.
        extra: What the account gets besides, a line each.
        funds: The Funds the account gets.
        paused: Whether claims are paused, so that it waits.
    """

    key: str
    faction: str
    title: str
    label: str
    hero: Hero | None
    choices: list[Choice]
    hint: str
    extra: list[str]
    funds: int
    paused: bool


class RewardClaims:
    """How pages claim a game's rewards, and what they say of what a claim gave."""

    def __init__(
        self, words: RewardTexts, entitlements: EntitlementStore, products: ProductStore
    ) -> None:
        """Claim a game's rewards for the account that a page's words are for.

        Args:
            words: What the page says of the rewards.
            entitlements: Hold what the heroes own, which says what each would get.
            products: Hold the boxes that gifts give from.
        """
        self.words = words
        self._entitlements = entitlements
        self._products = products

    async def claiming(self, reward: Reward) -> Claiming:
        """Return how a reward is claimed: for which hero, picked or not, and what each gets.

        Args:
            reward: The reward, which the account may claim now.
        """
        words, texts, gift = self.words, self.words.texts, reward.gift
        title = words.title(reward)
        # A starter pack goes to its hero alone; other items to one of the heroes they fit.
        alone = words.hero(reward.hero) if reward.goal is None else None
        heroes = [hero for p in reward.heroes if (hero := words.hero(p))] if not alone else []
        default = words.roster.default_id if words.roster else None
        picked = default if default in reward.heroes else next(iter(reward.heroes), None)
        rules = words.game.heroes
        assert rules is not None
        choices = []
        for hero in heroes:
            # What the hero would get: one item at random from the gift's box, where its items
            # do not fit the hero, or else each item new, added to or owned for good.
            said = [texts.text("web.rewards.outcome.box")]
            if not gives_box(words.catalogue.contents, gift, hero):
                kept = [
                    entry
                    for entry in await self._entitlements.of_persona(hero.persona.id)
                    if entry.status == ACTIVE
                    and (entry.expires is None or entry.expires > words.now)
                ]
                holds = held(rules, hero.faction, hero.hero_class, kept)
                said = []
                for item_id, ownership in gift.items:
                    name = words.catalogue.name(item_id) or str(item_id)
                    owned = holding(item_id, kept).type if item_id in holds else None
                    if owned == PERMANENT and ownership.type != COUNTED:
                        fallback = words.item(*words.rules.fallback)
                        said.append(
                            texts.text("web.rewards.outcome.owned", item=name, fallback=fallback)
                        )
                    else:
                        key = "web.rewards.outcome.adds" if owned else "web.rewards.outcome.new"
                        term = texts.ownership(ownership, inline=True)
                        said.append(texts.text(key, item=name, term=term))
            choices.append(Choice(hero, "; ".join(said), hero.persona.id == picked))
        hint = ""
        if choices and reward.faction:
            faction = texts.factions(frozenset({reward.faction}))
            hint = texts.text("web.rewards.claim.hint", faction=faction)
            if default in reward.heroes:
                hint += " " + texts.text("web.rewards.claim.default")
        return Claiming(
            reward.key,
            reward.faction or "",
            title,
            texts.text("web.rewards.claim.label", reward=title, band=words.band(reward)),
            alone,
            choices,
            hint,
            words.extra(gift),
            gift.funds,
            words.rules.paused,
        )

    async def reveal(self, hero: Hero, gift: Gift, given: Given) -> dict[str, str | None] | None:
        """Return what the next page reveals of the item that a gift's box gave a hero.

        The hero holds the item as given by now, so a rental it already had says how many days
        it has left.

        Args:
            hero: The hero.
            gift: The gift.
            given: What the claim gave the hero.

        Returns:
            The hero's and the item's names, what the hero holds of it and the item's icon; none
            when the claim gave the gift's own items rather than rolling its box.
        """
        if not given.rolled:
            return None
        [(item_id, ownership)] = given.items
        words, texts = self.words, self.words.texts
        box = await self._products.box_by_key(words.game.id, gift.box) if gift.box else None
        term = texts.ownership(ownership, inline=True)
        left = 0
        if ownership.type == RENTED and ownership.days:
            kept = await self._entitlements.of_persona(hero.persona.id, ownership.tag(item_id))
            expires = kept[0].expires if kept else None
            # Counted from after the grant, so that a rental new to the hero has its own days.
            after = StoreTexts(words.game, texts.language, datetime.now(UTC))
            left = after.days_left(expires) if expires else 0
        line = term
        if box and box.fallback and box.fallback.item_id == item_id:
            worth = texts.priced(box.value, box.currency)
            line = texts.text("web.rewards.reveal.fallback", hero=hero.persona.name, worth=worth)
        elif ownership.days and left > ownership.days:
            days = texts.count("web.store.price.days", left)
            line = texts.text(
                "web.rewards.reveal.added", hero=hero.persona.name, term=term, left=days
            )
        elif box and box.worth_rule:
            worth = texts.priced(box.value, box.currency)
            line = texts.text("web.rewards.reveal.worth", term=term, worth=worth)
        return {
            "hero": hero.persona.name,
            "item": words.catalogue.name(item_id) or str(item_id),
            "line": line,
            "icon": words.icon(item_id),
        }

    def refused(
        self, key: str, reward: Reward | None, refusal: RewardRefusal | None, hero: str
    ) -> tuple[str, dict[str, str]]:
        """Return the message that says why a claim gave nothing, with its values.

        Args:
            key: What the claim named the reward by.
            reward: The reward as the account sees it now; none once it no longer runs.
            refusal: Why the claim gave nothing; none when the reward no longer ran.
            hero: The name of the hero it was claimed for; empty for none.
        """
        words, texts = self.words, self.words.texts
        if reward is None or refusal is RewardRefusal.UNKNOWN:
            # Its event or its day of a daily event ended, as the key it was claimed by says.
            kind, _, rest = key.partition(":")
            year, _, day = rest.partition(":")
            event = words.rules.events.get(kind)
            if event and year.isdecimal():
                name, zone = texts.text(event.title), words.rules.zone
                period = event.window.period(int(year), zone)
                if words.now >= period.end:
                    return "web.rewards.refused.ended", {
                        "event": name,
                        "time": words.clock(period.end),
                    }
                if day.isdecimal():
                    ends = day_start(today(period.start, zone) + timedelta(int(day)), zone)
                    return "web.rewards.refused.ended.day", {
                        "event": name,
                        "day": day,
                        "time": words.clock(ends),
                    }
            return "web.rewards.refused.gone", {}
        title = words.title(reward)
        if refusal is RewardRefusal.CLAIMED:
            # The claim made before, which the reward as read again holds.
            claim = reward.claim
            assert claim is not None
            values = {"reward": title, "date": words.day(claim.at), "time": words.clock(claim.at)}
            if not (claimed := words.hero(claim.hero)):
                return "web.rewards.refused.claimed", values
            values["hero"] = claimed.persona.name
            if reward.day and reward.faction and not reward.kept:
                faction = texts.factions(frozenset({reward.faction}))
                return "web.rewards.refused.claimed.today", values | {"faction": faction}
            return "web.rewards.refused.claimed.hero", values
        return f"web.rewards.refused.{refusal}", {"reward": title, "hero": hero}
