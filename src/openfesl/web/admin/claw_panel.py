from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from openfesl.core.calendar import day_start, today
from openfesl.core.catalogues import Catalogue
from openfesl.core.claw import Claw
from openfesl.core.claw_rules import ClawRules
from openfesl.core.drops import Box
from openfesl.core.games import Game
from openfesl.core.wallets import MOST_BALANCE
from openfesl.web.pages import whole_number
from openfesl.web.store_texts import StoreTexts

# The Claw's settings below bfheroes.claw. and the form's field each comes from.
FIELDS = {
    "enabled": "runs",
    "free": "free",
    "paid.enabled": "paid",
    "paid.currency": "currency",
    "paid.price": "price",
    "feed.enabled": "feed",
    "feed.length": "length",
}


@dataclass(frozen=True, slots=True)
class ClawForm:
    """The Claw's settings as the boxes page's form holds them.

    Attributes:
        values: Each field's value as typed or saved, by the form's field name.
        refused: Why fields were refused, by field name.
    """

    values: dict[str, str]
    refused: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ClawPanel:
    """The Claw as the boxes page's entry shows it.

    Attributes:
        rules: Its rules as the instance sets them.
        name: Its name: the game's offer text where the catalogue has it.
        box: The store's box of its prizes, once the store has it.
        tiers: How many tiers its prizes are in.
        day: When the rewards' day starts, as the page writes it.
        counts: The day's free grabs, paid grabs and what the paid ones cost.
        form: The settings' form, as saved or as a refused save sent it.
        currencies: The currencies that paid grabs may cost, which the account holds.
    """

    rules: ClawRules
    name: str
    box: Box | None
    tiers: int
    day: str
    counts: tuple[int, int, int]
    form: ClawForm
    currencies: list[str]


def claw_form(rules: ClawRules) -> ClawForm:
    """Return the settings' form of a Claw as saved.

    Args:
        rules: The Claw's rules as the instance sets them.
    """
    return ClawForm(
        {
            "runs": "1" if rules.enabled else "",
            "free": str(rules.free),
            "paid": "1" if rules.paid.enabled else "",
            "currency": rules.paid.currency,
            "price": str(rules.paid.price),
            "feed": "1" if rules.feed.enabled else "",
            "length": str(rules.feed.length),
        }
    )


def claw_name(game: Game, catalogue: Catalogue, texts: StoreTexts) -> str:
    """Return the name of a game's Claw: the game's offer text, else OpenFESL's own.

    Args:
        game: The game, which has a Claw.
        catalogue: The game's catalogue in the page's language.
        texts: Writes the page's language.
    """
    assert game.claw is not None
    return catalogue.text(game.claw.name) or texts.text("web.claw.name")


async def claw_panel(
    claw: Claw, game: Game, catalogue: Catalogue, language: str, form: ClawForm | None = None
) -> ClawPanel | None:
    """Return the Claw of a game as the boxes page shows it, if the game has one.

    Args:
        claw: Runs the games' Claws.
        game: The game.
        catalogue: The game's catalogue in the page's language.
        language: The page's language.
        form: The settings' form as a refused save sent it; the saved settings when none.
    """
    rules = await claw.rules(game)
    if rules is None:
        return None
    texts = StoreTexts(game, language, datetime.now(UTC))
    box = await claw.box(game)
    zone = await claw.zone(game)
    start = day_start(today(datetime.now(UTC), zone), zone)
    shown = [wallet.currency for wallet in game.site.wallets] if game.site else []
    return ClawPanel(
        rules,
        claw_name(game, catalogue, texts),
        box,
        len({drop.tier for drop in box.drops}) if box else 0,
        texts.text("web.store.utc", time=f"{start:%H:%M}"),
        await claw.today(game, rules),
        form or claw_form(rules),
        shown,
    )


def read_form(form: Mapping[str, Any], currencies: list[str], texts: StoreTexts) -> ClawForm:
    """Return the settings' form as sent, with why its fields are refused.

    Args:
        form: The sent form.
        currencies: The currencies that paid grabs may cost.
        texts: Writes the page's language.
    """
    values = {name: str(form.get(name, "")).strip() for name in (*FIELDS.values(), "note")}
    refused = {}
    for name in ("free", "price", "length"):
        number = whole_number(values[name], MOST_BALANCE)
        if number is not None:
            values[name] = str(number)
        else:
            key = "price" if name == "price" else "number"
            refused[name] = texts.text(f"web.admin.boxes.claw.refused.{key}")
    if values["currency"] not in currencies:
        refused["currency"] = texts.text("web.admin.boxes.claw.refused.currency")
    if not values["note"]:
        refused["note"] = texts.text("web.admin.keys.refused.note")
    return ClawForm(values, refused)
