from collections import Counter
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from random import Random
from xml.etree import ElementTree

import pytest
from accounts import select_value
from heroes import MAGMA, wallets
from store import ROYAL_SOLDIER, buy, items, listed, shopper, stock, without_rotation
from support import fetch

from openfesl.core.box_tables import expected
from openfesl.core.drops import Box, Drop, below_value, odds, pick
from openfesl.core.inventory import COUNTED, PERMANENT, RENTED, Ownership
from openfesl.core.store import Bundle, Product
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio


def product(number: int, item: int, ownership: Ownership, price: int) -> Product:
    return Product(number, item, ownership, "_PF", price, None, number)


HAT = product(1, 10, Ownership(PERMANENT), 2600)
COAT = product(2, 11, Ownership(PERMANENT), 2600)
RIFLE = product(3, 12, Ownership(RENTED, days=7), 672)
TONICS = product(4, 13, Ownership(COUNTED, uses=110), 770)
# A box of 629 Funds: a hat or a coat in six rolls of ten, a rifle for a week in four.
BOX = Box(
    1,
    "invented-drop",
    "box",
    629,
    True,
    None,
    None,
    "_PF",
    (Drop(0, 60, HAT), Drop(0, 60, COAT), Drop(1, 40, RIFLE)),
    TONICS,
)


def test_a_box_picks_a_tier_by_its_weight_and_a_drop_of_it_evenly() -> None:
    random = Random(20121019)

    given = Counter(pick(BOX, lambda product: True, random) for _ in range(3000))
    # A hero who owns the hat for good never gets it, and one who may get none gets tonics.
    without_hat = {pick(BOX, lambda product: product != HAT, random) for _ in range(200)}
    nothing = pick(BOX, lambda product: False, random)

    assert set(given) == {HAT, COAT, RIFLE}
    assert 1650 < given[HAT] + given[COAT] < 1950 and abs(given[HAT] - given[COAT]) < 150
    assert without_hat == {COAT, RIFLE}
    assert nothing == TONICS


def test_a_box_promises_every_drop_is_worth_its_price_unless_it_says_otherwise() -> None:
    cheap = product(5, 14, Ownership(RENTED, days=1), 130)
    valor = Product(6, 15, Ownership(PERMANENT), "_DV", 5000, None, 6)
    trial = Box(2, "trial", "box", 629, True, None, None, "_PF", (Drop(0, 1, cheap),), valor)

    assert below_value(BOX) == []
    # A drop worth less, and one priced in another currency, break the promise.
    assert below_value(trial) == [cheap, valor]
    assert (
        below_value(Box(3, "trial", "box", 629, False, None, None, "_PF", trial.drops, None)) == []
    )


@pytest.mark.usefixtures("prompt_actions")
async def test_a_bought_box_gives_one_item_the_hero_may_use_and_never_one_it_owns(
    base: str,
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys)
    await without_rotation(database_url)
    # Of the game data's Royal Soldier Supply Drop, the invented catalogue holds the pet alone,
    # beside the tonics the box gives a hero who owns every drop.
    box = await select_value(
        database_url, "SELECT id FROM store_box WHERE key = 'royal-soldier-supply-drop'"
    )
    assert isinstance(box, int)
    drop = Bundle("Royal Soldier Supply Drop", None, "_PF", 629, faction="royal", box=box)
    number = await products.add_bundle("bfheroes", drop)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    national, other = await shopper(server_url, "bob@example.test", "Mudlark")

    _, _, deals = await fetch(server_url, "/en/game/hotDeals", MAGMA)
    answers = []
    for _ in range(2):
        path = f"/ofb/purchase/{hero}/OFB-DBFH:{number}"
        answers.append(
            ElementTree.fromstring((await fetch(server_url, path, MAGMA, cookie, b""))[2])
        )
    refused = await buy(server_url, national, other, number)

    random = ElementTree.fromstring(deals).find(
        "product/attributes/attribute[@name='randomContent']"
    )
    assert random is not None and random.get("value") == "YES"
    # Each answer lists the one product the box gave, as the game adds it.
    assert [answer.tag for answer in answers] == ["products", "products"]
    assert [[listed(given) for given in answer] for answer in answers] == [
        ["OFB-DBFH:5 521-unlm 900_PF"],
        ["OFB-DBFH:12 2153-lmct x110 770_PF"],
    ]
    assert refused == ("error", "NOT_FOR_HERO")
    assert (await wallets(server_url, cookie, hero))["_PF"] == 2500 - 2 * 629
    assert await items(server_url, cookie, hero) == ["2005-lmct 10", "521-unlm 0", "2153-lmct 110"]
    assert (
        await select_value(
            database_url,
            "SELECT string_agg(outcome::text, ' ' ORDER BY id) FROM purchase WHERE kind = 'box'",
        )
        == '{"items": [521]} {"items": [2153]}'
    )


def test_a_box_that_promises_its_worth_skips_the_drops_below_its_price() -> None:
    random = Random(20121019)
    cheap = product(5, 14, Ownership(RENTED, days=1), 130)
    box = replace(BOX, drops=(*BOX.drops, Drop(1, 40, cheap)))
    # The rifle's tier left with the cheap rental alone, as a price cut below the box's leaves it.
    cut = replace(BOX, drops=(*BOX.drops[:2], Drop(1, 40, cheap)))

    given = {pick(box, lambda product: True, random) for _ in range(500)}
    loose = {pick(replace(box, worth_rule=False), lambda product: True, random) for _ in range(500)}

    assert cheap not in given and cheap not in odds(box, lambda product: True)
    assert cheap in loose
    # A tier of drops below the price keeps its weight and gives the fallback, so the rare
    # tier does not become certain.
    assert odds(cut, lambda product: True) == {
        HAT: Fraction(3, 10),
        COAT: Fraction(3, 10),
        TONICS: Fraction(2, 5),
    }
    assert expected(cut) == Fraction(60 * 2600 + 40 * 770, 100 * 629)
    assert pick(replace(box, drops=(Drop(0, 1, cheap),)), lambda product: True, random) == TONICS
