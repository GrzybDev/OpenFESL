import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from xml.etree import ElementTree

import pytest
from accounts import execute, select_value
from heroes import MAGMA, wallets
from living import NATIONAL, ROYAL, Invented, import_catalogue, setting, worn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER, attribute, buy, deals, grant, shopper
from support import fetch

from openfesl.core.calendar import Schedule, periods
from openfesl.core.catalogues import Catalogues
from openfesl.core.deal_rules import DealRules
from openfesl.core.deal_steps import DealSteps
from openfesl.core.deal_uploads import offer_of
from openfesl.core.deals import HotDeals
from openfesl.core.games import Game, load_games
from openfesl.core.store import Bundle
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.deals import SqlDealStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

HAT, NECK, JACKET, LEGS = "0", "2", "4", "6"

# An invented catalogue: a cap, a scarf and a coat of each faction, which the Deal of the Day
# puts together; seven pairs of Royal trousers, each a set of its own; two boosts; a claw attack,
# which only boxes and sets give; and the tonic that the store's boxes give a hero who owns all
# their drops, which makes the store keep them.
INVENTED: Invented = {
    101: worn("Invented Cap", ROYAL, HAT),
    102: worn("Invented Scarf", ROYAL, NECK),
    103: worn("Invented Coat", ROYAL, JACKET),
    201: worn("Invented Helmet", NATIONAL, HAT),
    202: worn("Invented Collar", NATIONAL, NECK),
    203: worn("Invented Tunic", NATIONAL, JACKET),
    **{310 + n: worn(f"Invented Trousers {n}", ROYAL, LEGS) for n in range(1, 8)},
    9000: ("Invented XP Boost", "convenience", {}),
    9005: ("Invented Valor Boost", "convenience", {}),
    9100: ("Invented Claw", "ability", {"widget": "true"}),
    2153: ("Invented Tonic", "ability", {"consumable": "true"}),
}
# The game's own name of the boost pair, which the game data lists among the popular deals.
BOOST_PAIR = "WEST_Custom_Bundle_XPVPBoostBundle"
# The game's own names of the Widget Supply Drops, which may give a boost.
WIDGET_DROPS = ("WEST_Custom_Bundle_WidgetSupplyDrop", "WEST_Custom_Bundle_WidgetSupplyDrop2")
DAILY = "web.deals.daily.name"
TEXTS = {BOOST_PAIR: "Invented Boost Pair"} | dict.fromkeys(WIDGET_DROPS, "Invented Widget Drop")

# The invented catalogue's deals: the Deal of the Day, a set each Monday that comes back no
# sooner than six weeks after it left, and the boost pair always.
RULES = DealRules.model_validate(
    {
        "currency": "_PF",
        "price_ending": 99,
        "sections": {
            "daily": {
                "kind": "items",
                "schedule": {},
                "pieces": 3,
                "slots": ["hat", "neck", "jacket"],
                "discount": 30,
                "name": DAILY,
            },
            "new": {
                "kind": "offers",
                "schedule": {"every": 7},
                "size": 1,
                "discount": [50, 50],
                "gap": 42,
                "sets": True,
            },
            "popular": {
                "kind": "offers",
                "schedule": {"every": 7},
                "offers": [
                    {"name": BOOST_PAIR, "members": ["9000 30d", "9005 30d"], "price": 8399}
                ],
            },
        },
        "sets": [{"name": f"Invented Set {n}", "members": [310 + n]} for n in range(1, 8)],
    }
)
# The store's themed boxes, a family of a Royal and a National box each, three families at a
# time for six weeks; and the Royal Trick-or-Treat bag, sold through Halloween.
FAMILIES = ("mechanical", "cosmic", "experimental", "spare-parts", "pandora", "spoils-of-war")
BOX_RULES = DealRules.model_validate(
    {
        "currency": "_PF",
        "sections": {
            "themed": {
                "kind": "offers",
                "schedule": {"every": 14, "lasts": 42},
                "size": 1,
                "gap": 42,
                "offers": [
                    {"name": f"{side} {family}", "box": f"{side}-{family}", "pool": family}
                    for family in FAMILIES
                    for side in ("royal", "national")
                ],
            },
            "events": {
                "kind": "offers",
                "offers": [
                    {
                        "name": "Invented Bag",
                        "box": "royal-trick-or-treat",
                        "window": {"dates": ["10-19", "10-31"]},
                    }
                ],
            },
        },
    }
)
# A Sunday, a second before midnight in UTC.
SUNDAY = datetime(2026, 11, 1, 23, 59, 59, tzinfo=UTC)
MONDAY = datetime(2026, 11, 2, tzinfo=UTC)

type Line = tuple[str | None, str, int, int | None]


class Rotation:
    """The invented catalogue's hot deals, read at a time that the test sets."""

    def __init__(self, game: Game, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.game = game
        self.sessions = sessions
        self.catalogues = Catalogues(SqlCatalogueStore(sessions), None, load_games())
        self.products = SqlProductStore(sessions)
        self.now = SUNDAY
        self.deals = self.server()
        self.steps = DealSteps(self.deals, SqlDealStore(sessions), lambda: self.now)

    def server(self) -> HotDeals:
        """Return the hot deals as a server keeps them, which has made nothing yet."""
        store, settings = SqlDealStore(self.sessions), SqlSettingStore(self.sessions)
        return HotDeals(self.products, store, settings, self.catalogues, lambda: self.now)

    async def listed(self) -> list[Line]:
        """Return the hot deals as section, name, price and original price, in order."""
        return [
            (found.section, found.name, found.price, found.original_price)
            for found in [listed.bundle for listed in await self.deals.current(self.game)]
        ]


@pytest.fixture
async def rotation(
    prompt_actions: None,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> AsyncIterator[Rotation]:
    await import_catalogue(server_url, tmp_path, capsys, INVENTED, TEXTS)
    engine = create_async_engine(database_url)
    found = Rotation(bfheroes.model_copy(update={"deals": RULES}), async_sessionmaker(engine))
    await found.catalogues.load()
    yield found
    await engine.dispose()


async def test_the_preview_of_each_sections_next_step_shows_what_the_step_brings(
    rotation: Rotation,
) -> None:
    before = await rotation.listed()
    steps = await rotation.steps.preview(rotation.game)
    rotation.now = MONDAY
    after = await rotation.listed()

    def lines(found: list[Bundle]) -> list[Line]:
        return [
            (bundle.section, bundle.name, bundle.price, bundle.original_price) for bundle in found
        ]

    # The step draws what the preview drew, and ends every deal of Sunday's periods.
    assert {key: step.at for key, step in steps.items()} == dict.fromkeys(
        ("daily", "new", "popular"), MONDAY
    )
    previewed = [
        replace(deal.bundle, section=key) for key, step in steps.items() for deal in step.deals
    ]
    assert sorted(lines(previewed), key=str) == sorted(after, key=str)
    ending = [listed.bundle for step in steps.values() for listed in step.ending]
    assert sorted(lines(ending), key=str) == sorted(before, key=str)
    # The invented sets are all Royal, so the step of new sets falls short of National ones.
    assert {key: step.short for key, step in steps.items()} == {
        "daily": {},
        "new": {"national": 0},
        "popular": {},
    }


async def test_the_log_shows_each_step_with_what_came_in_and_left_and_short_factions(
    rotation: Rotation,
) -> None:
    sunday = await rotation.listed()
    rotation.now = MONDAY
    monday = await rotation.listed()
    steps = await rotation.steps.log(rotation.game)

    def names(listed: list[Line], section: str) -> list[str]:
        return [name for found, name, _, _ in listed if found == section]

    # Newest first, Monday's sections in their order, each with what Sunday's ended.
    assert [(step.section, step.at) for step in steps][:3] == [
        ("daily", MONDAY),
        ("new", MONDAY),
        ("popular", MONDAY),
    ]
    daily, new, popular = steps[:3]
    assert [deal.name for deal in new.came] == names(monday, "new")
    assert [deal.name for deal in new.left] == names(sunday, "new")
    assert [deal.name for deal in popular.left] == [BOOST_PAIR]
    assert len(daily.came) == len(daily.left) == 2
    assert all(len(deal.items) == 3 for deal in daily.came + daily.left)
    # The invented sets are all Royal, so each step of new sets falls short of National ones.
    assert (daily.short, new.short, popular.short) == ({}, {"national": 0}, {})


async def test_the_pool_brings_a_pinned_set_at_every_step_and_never_an_excluded_one(
    rotation: Rotation, database_url: str
) -> None:
    left = [f"Invented Set {n}" for n in (1, 2, 4, 5, 6)]
    await setting(database_url, "bfheroes.deals.sections.new.pinned", '["Invented Set 3"]')
    await setting(database_url, "bfheroes.deals.sections.new.excluded", json.dumps(left))

    async def new_sets(day: int) -> list[str]:
        rotation.now = MONDAY + timedelta(days=day)
        return [name for section, name, _, _ in await rotation.listed() if section == "new"]

    pinned = [await new_sets(7 * week) for week in range(3)]
    await setting(database_url, "bfheroes.deals.sections.new.pinned", "[]")
    unpinned = await new_sets(21)

    # Pinned, it comes again within the gap; unpinned, the gap keeps it out, and the one set
    # the pool still holds comes.
    assert pinned == [["Invented Set 3"]] * 3
    assert unpinned == ["Invented Set 7"]


async def test_deals_change_at_midnight_and_sets_on_mondays_at_prices_ending_in_99(
    rotation: Rotation, database_url: str
) -> None:
    sunday = await rotation.listed()
    [ended] = [listed.id for listed in await rotation.deals.current(rotation.game)][:1]
    rotation.now = MONDAY
    monday = await rotation.listed()
    periods = {
        (listed.bundle.section, listed.bundle.starts, listed.bundle.ends)
        for listed in await rotation.deals.current(rotation.game)
    }
    rotation.now = MONDAY + timedelta(hours=23, minutes=59)
    later = await rotation.listed()

    # Three pieces of 2,600 Funds at 30% off for each faction, Royal first, a set's piece at
    # half price and the boost pair at its own price, each rounded down to end in 99.
    for listed in (sunday, monday):
        assert listed == [
            ("daily", DAILY, 5399, 7800),
            ("daily", DAILY, 5399, 7800),
            ("new", listed[2][1], 1299, 2600),
            ("popular", BOOST_PAIR, 8399, 12598),
        ]
    assert later == monday
    assert periods == {
        ("daily", MONDAY, MONDAY + timedelta(days=1)),
        ("new", MONDAY, MONDAY + timedelta(days=7)),
        ("popular", MONDAY, MONDAY + timedelta(days=7)),
    }
    # Sunday's deals left the list, so they are no longer sold either.
    assert await rotation.deals.bundle(rotation.game, ended) is None
    made = "SELECT count(*) FROM store_bundle WHERE source = 'auto'"
    assert await select_value(database_url, made) == 8


async def test_a_deal_of_the_day_of_two_deals_makes_two_for_each_faction(
    rotation: Rotation, database_url: str
) -> None:
    # Deals of one piece, so that the invented catalogue fills two for each faction.
    daily = RULES.sections["daily"].model_copy(update={"pieces": 1})
    rules = RULES.model_copy(update={"sections": {**RULES.sections, "daily": daily}})
    rotation.game = rotation.game.model_copy(update={"deals": rules})
    await setting(database_url, "bfheroes.deals.sections.daily.size", "2")

    made = [
        listed.bundle
        for listed in await rotation.deals.current(rotation.game)
        if listed.bundle.section == "daily"
    ]

    # The first of each faction keeps the pictures uploaded for the faction's deal.
    assert [(bundle.faction, offer_of(bundle)) for bundle in made] == [
        ("royal", "royal"),
        ("royal", "royal:1"),
        ("national", "national"),
        ("national", "national:1"),
    ]


async def test_a_set_comes_back_no_sooner_than_six_weeks_after_it_left(
    rotation: Rotation,
) -> None:
    picked = []
    for week in range(8):
        rotation.now = MONDAY + timedelta(weeks=week)
        picked += [name for section, name, _, _ in await rotation.listed() if section == "new"]

    # Seven sets, each new for a week: the first may come back in the eighth.
    assert len(set(picked[:7])) == 7
    assert picked[7] == picked[0]


async def test_the_rotation_leaves_out_the_sets_of_items_marked_not_sold(
    rotation: Rotation, database_url: str
) -> None:
    sets = [
        {"name": "Trousers One", "members": [311]},
        {"name": "Trousers Two", "members": [312]},
        {"name": "Claw", "members": ["9100 30d"]},
    ]
    every = {"kind": "offers", "schedule": {"every": 7}, "discount": [50, 50], "offers": sets}
    deals = DealRules.model_validate({"currency": "_PF", "sections": {"sale": every}})
    rotation.game = rotation.game.model_copy(update={"deals": deals})
    # Not sold switches every price point of an item off.
    await execute(database_url, "UPDATE store_product SET published = false WHERE item_id = 311")

    listed = [name for _, name, _, _ in await rotation.listed()]

    assert listed == ["Trousers Two", "Claw"]


async def test_switched_off_sections_are_left_out_and_without_the_rotation_admin_offers_stay(
    rotation: Rotation, database_url: str
) -> None:
    coat = await select_value(database_url, "SELECT id FROM store_product WHERE item_id = 103")
    assert isinstance(coat, int)
    for name, section in (("Invented Offer", "new"), ("Invented Extra", None)):
        bundle = Bundle(name, None, "_PF", 999, members=(coat,), section=section)
        await rotation.products.add_bundle("bfheroes", bundle)

    every = await rotation.listed()
    await setting(database_url, "bfheroes.deals.sections.new.enabled", "off")
    without_new = await rotation.listed()
    await setting(database_url, "bfheroes.deals.enabled", "off")
    without_rotation = await rotation.listed()

    # The admin's offer of a section follows its own, and those of none come last.
    assert [(section, name) for section, name, _, _ in every] == [
        ("daily", DAILY),
        ("daily", DAILY),
        ("new", every[2][1]),
        ("new", "Invented Offer"),
        ("popular", BOOST_PAIR),
        (None, "Invented Extra"),
    ]
    assert [name for _, name, _, _ in without_new] == [DAILY, DAILY, BOOST_PAIR, "Invented Extra"]
    assert [name for _, name, _, _ in without_rotation] == ["Invented Extra"]


async def test_two_servers_looking_at_once_make_a_period_once(
    rotation: Rotation, database_url: str
) -> None:
    first, second = rotation.deals, rotation.server()

    listed = await asyncio.gather(first.current(rotation.game), second.current(rotation.game))

    made = "SELECT count(*) FROM store_bundle WHERE source = 'auto'"
    assert await select_value(database_url, made) == 4
    assert [bundle.id for bundle in listed[0]] == [bundle.id for bundle in listed[1]]


@pytest.mark.usefixtures("prompt_actions", "base")
async def test_the_game_lists_and_sells_the_deal_of_the_day_and_the_admins_offers_after_it(
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Without the trousers, so that the Deal of the Day has one piece for each of its slots,
    # and without the tonic, so that the store keeps only the boxes that give boosts.
    kept = {
        number: item
        for number, item in INVENTED.items()
        if item[2].get("slot") != LEGS and number != 2153
    }
    await import_catalogue(server_url, tmp_path, capsys, kept, TEXTS)
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    boost = await select_value(
        database_url, "SELECT id FROM store_product WHERE item_id = 9000 AND published"
    )
    assert isinstance(boost, int)
    offer = Bundle("Invented Offer", None, "_PF", 100, members=(boost,))
    offered = await products.add_bundle("bfheroes", offer)

    english, polish = await deals(server_url, "en"), await deals(server_url, "pl")
    brazilian = await deals(server_url, "br")
    # Opened with the account's 2,500 Funds before the admin adds to them.
    await wallets(server_url, cookie, hero)
    await grant(database_url, hero, "_PF", 3000)
    royal = english[0].get("productId", "").removeprefix("OFB-DBFH:")
    bought = [await buy(server_url, cookie, hero, number) for number in (royal, offered)]

    # The game data's sections whose items the catalogue has: the Deal of the Day of each
    # faction, the Widget Supply Drops and the boost pair, then the admin's offer.
    assert [
        (attribute(deal, "Product Name"), attribute(deal, "gameOrder")) for deal in english
    ] == [
        ("Deal of the Day: Royal Army", "1"),
        ("Deal of the Day: National Army", "2"),
        ("Invented Widget Drop", "3"),
        ("Invented Widget Drop", "4"),
        ("Invented Boost Pair", "5"),
        ("Invented Offer", "6"),
    ]
    assert attribute(english[0], "hotdealsDescription") == (
        "Invented Cap, Invented Scarf, and Invented Coat"
    )
    assert attribute(polish[0], "Product Name") == "Okazja dnia: Royal Army"
    assert attribute(polish[0], "hotdealsDescription") == (
        "Invented Cap, Invented Scarf i Invented Coat"
    )
    # The game names Brazilian Portuguese br, whose lists join as pt-BR's do.
    assert attribute(brazilian[0], "hotdealsDescription") == (
        "Invented Cap, Invented Scarf e Invented Coat"
    )
    assert bought == [("success", None)] * 2
    assert (await wallets(server_url, cookie, hero))["_PF"] == 2500 + 3000 - 5399 - 100
    _, _, body = await fetch(server_url, f"/nucleus/entitlements/{hero}", MAGMA, cookie)
    tags = {entry.findtext("entitlementTag") for entry in ElementTree.fromstring(body)}
    assert {"101-unlm", "102-unlm", "103-unlm", "9000-tlmd"} <= tags


async def test_themed_families_change_one_at_a_time_and_come_back_six_weeks_after_leaving(
    rotation: Rotation,
) -> None:
    rotation.game = rotation.game.model_copy(update={"deals": BOX_RULES})
    [first, *_] = periods(Schedule(every=14, lasts=42), MONDAY, "UTC")
    steps = []
    for number in range(8):
        rotation.now = first.start + timedelta(days=14 * number)
        listed = [bundle.bundle for bundle in await rotation.deals.current(rotation.game)]
        steps.append([bundle for bundle in listed if bundle.section == "themed"])

    # Each box is named by its faction and family.
    families = [{bundle.name.split()[1] for bundle in step} for step in steps]
    # Three families at a time, each with both factions' boxes at the store's box prices.
    prices = {"mechanical": 999, "cosmic": 999, "pandora": 1199, "spoils-of-war": 1199}
    for step, found in zip(steps, families, strict=True):
        assert len(found) == 3
        assert sorted(bundle.faction or "" for bundle in step) == ["national"] * 3 + ["royal"] * 3
        assert all(bundle.price == prices.get(bundle.name.split()[1], 1999) for bundle in step)
    for before, after in pairwise(families):
        assert len(after - before) == len(before - after) == 1
    for family in FAMILIES:
        present = [step for step, found in enumerate(families) if family in found]
        # Back after six weeks away at the soonest, three steps of two weeks.
        assert all(later - earlier in (1, 4, 5, 6, 7) for earlier, later in pairwise(present))


async def test_an_event_box_is_sold_only_through_its_event_while_it_is_on_sale(
    rotation: Rotation, database_url: str
) -> None:
    rotation.game = rotation.game.model_copy(update={"deals": BOX_RULES})
    sold = []
    for moment in (
        datetime(2026, 10, 18, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 10, 19, tzinfo=UTC),
        datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
        datetime(2026, 11, 1, tzinfo=UTC),
        datetime(2027, 10, 25, tzinfo=UTC),
    ):
        rotation.now = moment
        sold.append(
            [name for section, name, _, _ in await rotation.listed() if section == "events"]
        )

    # A box that the admin took off sale is left out of its event's next run.
    await execute(
        database_url, "UPDATE store_box SET enabled = false WHERE key = 'royal-trick-or-treat'"
    )
    rotation.now = datetime(2028, 10, 25, tzinfo=UTC)
    off = [name for section, name, _, _ in await rotation.listed() if section == "events"]

    assert sold == [[], ["Invented Bag"], ["Invented Bag"], [], ["Invented Bag"]]
    assert off == []
