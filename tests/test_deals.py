import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random
from xml.etree import ElementTree

import anyio
import pytest
from accounts import execute, select_value
from heroes import MAGMA, wallets
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from store import ROYAL_SOLDIER, attribute, buy, deals, grant, shopper
from support import cli, fetch

from openfesl.core.catalogue import CatalogueDocument, CatalogueFile, CatalogueItem
from openfesl.core.catalogues import Catalogues
from openfesl.core.deal_rules import DealRules
from openfesl.core.deals import HotDeals
from openfesl.core.games import Game, load_games
from openfesl.core.store import Bundle
from openfesl.storage.catalogues import SqlCatalogueStore
from openfesl.storage.deals import SqlDealStore
from openfesl.storage.settings import SqlSettingStore
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

# An invented catalogue: a cap, a scarf and a coat of each faction, which the Deal of the Day
# puts together, and two boosts.
ROYAL, NATIONAL = "1", "2"
HAT, NECK, JACKET = "0", "2", "4"
CLOTHES = {
    101: ("Invented Cap", ROYAL, HAT),
    102: ("Invented Scarf", ROYAL, NECK),
    103: ("Invented Coat", ROYAL, JACKET),
    201: ("Invented Helmet", NATIONAL, HAT),
    202: ("Invented Collar", NATIONAL, NECK),
    203: ("Invented Tunic", NATIONAL, JACKET),
}
BOOSTS = {9000: "Invented XP Boost", 9005: "Invented Valor Boost"}
DAILY = "web.deals.daily.name"

# The invented catalogue's deals: the Deal of the Day.
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
        },
    }
)
# A Sunday, a second before midnight in UTC.
SUNDAY = datetime(2026, 11, 1, 23, 59, 59, tzinfo=UTC)
MONDAY = datetime(2026, 11, 2, tzinfo=UTC)

type Line = tuple[str | None, str, int, int | None]


def catalogue_file(folder: Path, clothes: dict[int, tuple[str, str, str]]) -> Path:
    """Write the invented catalogue as `openfesl catalogue extract` writes it."""
    items = [
        CatalogueItem(id=number, kind="appearance", fields={"faction": side, "slot": slot})
        for number, (_, side, slot) in clothes.items()
    ] + [CatalogueItem(id=number, kind="convenience", fields={}) for number in BOOSTS]
    names = {number: name for number, (name, _, _) in clothes.items()} | BOOSTS
    english = {f"WEST_Custom_Item_{number}": name for number, name in names.items()}
    document = CatalogueDocument(format=1, game="bfheroes", items=tuple(items))
    file = folder / "catalogue.zip"
    file.write_bytes(CatalogueFile(document, {"en": english}, {}).write())
    return file


class Rotation:
    """The invented catalogue's hot deals, read at a time that the test sets."""

    def __init__(self, game: Game, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.game = game
        self.sessions = sessions
        self.catalogues = Catalogues(SqlCatalogueStore(sessions), None, load_games())
        self.products = SqlProductStore(sessions)
        self.now = SUNDAY
        # The same picks on every run.
        self.deals = self.server(Random(20121019))

    def server(self, random: Random) -> HotDeals:
        """Return the hot deals as a server keeps them, which has made nothing yet."""
        store, settings = SqlDealStore(self.sessions), SqlSettingStore(self.sessions)
        return HotDeals(self.products, store, settings, self.catalogues, random, lambda: self.now)

    async def listed(self) -> list[Line]:
        """Return the hot deals as section, name, price and original price, in order."""
        return [
            (found.section, found.name, found.price, found.original_price)
            for found in [listed.bundle for listed in await self.deals.current(self.game)]
        ]


async def import_catalogue(
    server_url: str,
    folder: Path,
    capsys: pytest.CaptureFixture[str],
    clothes: dict[int, tuple[str, str, str]] = CLOTHES,
) -> None:
    """Import the invented catalogue as the admin does, and wait for the store to fill from it."""
    await cli(capsys, "catalogue", "import", str(catalogue_file(folder, clothes)))
    with anyio.fail_after(5):
        while not len(ElementTree.fromstring((await fetch(server_url, "/ofb/products", MAGMA))[2])):
            await anyio.sleep(0.05)


@pytest.fixture
async def rotation(
    prompt_actions: None,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    bfheroes: Game,
) -> AsyncIterator[Rotation]:
    await import_catalogue(server_url, tmp_path, capsys)
    engine = create_async_engine(database_url)
    found = Rotation(bfheroes.model_copy(update={"deals": RULES}), async_sessionmaker(engine))
    await found.catalogues.load()
    yield found
    await engine.dispose()


async def setting(database_url: str, key: str, value: str) -> None:
    """Change a setting as the admin does."""
    await execute(
        database_url,
        "INSERT INTO setting (key, value) VALUES (:key, :value)"
        " ON CONFLICT (key) DO UPDATE SET value = :value",
        key=key,
        value=value,
    )


async def test_deals_change_at_midnight_at_prices_ending_in_99(
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

    # Three pieces of 2,600 Funds at 30% off for each faction, Royal first, rounded down to end
    # in 99.
    for listed in (sunday, monday):
        assert listed == [("daily", DAILY, 5399, 7800), ("daily", DAILY, 5399, 7800)]
    assert later == monday
    assert periods == {("daily", MONDAY, MONDAY + timedelta(days=1))}
    # Sunday's deals left the list, so they are no longer sold either.
    assert await rotation.deals.bundle(rotation.game, ended) is None
    made = "SELECT count(*) FROM store_bundle WHERE source = 'auto'"
    assert await select_value(database_url, made) == 4


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

    # Each deal of the period has a pool of its own; the first keeps the faction's.
    assert [(bundle.faction, bundle.pool) for bundle in made] == [
        ("royal", "royal"),
        ("royal", "royal:1"),
        ("national", "national"),
        ("national", "national:1"),
    ]


async def test_switched_off_sections_are_left_out_and_without_the_rotation_admin_offers_stay(
    rotation: Rotation, database_url: str
) -> None:
    coat = await select_value(database_url, "SELECT id FROM store_product WHERE item_id = 103")
    assert isinstance(coat, int)
    for name, section in (("Invented Offer", "daily"), ("Invented Extra", None)):
        bundle = Bundle(name, None, "_PF", 999, members=(coat,), section=section)
        await rotation.products.add_bundle("bfheroes", bundle)

    every = await rotation.listed()
    await setting(database_url, "bfheroes.deals.sections.daily.enabled", "off")
    without_daily = await rotation.listed()
    await setting(database_url, "bfheroes.deals.sections.daily.enabled", "on")
    await setting(database_url, "bfheroes.deals.enabled", "off")
    without_rotation = await rotation.listed()

    # The admin's offer of a section follows its own, and those of none come last.
    assert [(section, name) for section, name, _, _ in every] == [
        ("daily", DAILY),
        ("daily", DAILY),
        ("daily", "Invented Offer"),
        (None, "Invented Extra"),
    ]
    assert [name for _, name, _, _ in without_daily] == ["Invented Extra"]
    assert [name for _, name, _, _ in without_rotation] == ["Invented Offer", "Invented Extra"]


async def test_two_servers_looking_at_once_make_a_period_once(
    rotation: Rotation, database_url: str
) -> None:
    first, second = rotation.deals, rotation.server(Random(1))

    listed = await asyncio.gather(first.current(rotation.game), second.current(rotation.game))

    made = "SELECT count(*) FROM store_bundle WHERE source = 'auto'"
    assert await select_value(database_url, made) == 2
    assert [bundle.id for bundle in listed[0]] == [bundle.id for bundle in listed[1]]


@pytest.mark.usefixtures("prompt_actions", "base")
async def test_the_game_lists_and_sells_the_deal_of_the_day_and_the_admins_offers_after_it(
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await import_catalogue(server_url, tmp_path, capsys)
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

    # The game data's Deal of the Day of each faction, then the admin's offer.
    assert [
        (attribute(deal, "Product Name"), attribute(deal, "gameOrder")) for deal in english
    ] == [
        ("Deal of the Day: Royal Army", "1"),
        ("Deal of the Day: National Army", "2"),
        ("Invented Offer", "3"),
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
