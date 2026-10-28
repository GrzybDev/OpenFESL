import gzip
from pathlib import Path

import anyio
import pytest
from accounts import execute, select_value
from heroes import MAGMA
from store import ITEMS, catalogue_file, item, listed, offers, stock
from support import cli, fetch

from openfesl.core.games import Game
from openfesl.core.inventory import RENTED, Ownership
from openfesl.core.store_rules import PriceRule

pytestmark = pytest.mark.anyio


async def test_the_store_sells_each_way_to_own_an_imported_item_as_a_product_of_its_own(
    prompt_actions: None, server_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The store stays empty until the admin imports the game's catalogue.
    assert await offers(server_url) == []

    await stock(server_url, tmp_path, capsys)

    sold = [listed(product) for product in await offers(server_url)]
    plain = await fetch(server_url, "/ofb/products", MAGMA)
    packed = await fetch(server_url, "/ofb/products", MAGMA, {"Accept-Encoding": "gzip"})
    # By the game data's rules: clothes and emotes for good or for 30 days, pets for good, each
    # level of an ability for a Hero Point, uses of consumables in packs, class weapons for
    # Funds and every weapon for Valor Points by the day, boosts for 30 days. The base hat, the
    # hair, the withheld shirt, the partner's shirt, the loadout's knife, the unnamed weapon and
    # the mission are not sold.
    assert sold == [
        "OFB-DBFH:1 3-unlm 2600_PF",
        "OFB-DBFH:2 3-tlmd 30d 1300_PF",
        "OFB-DBFH:3 4-unlm 2600_PF",
        "OFB-DBFH:4 4-tlmd 30d 1300_PF",
        "OFB-DBFH:5 521-unlm 900_PF",
        "OFB-DBFH:6 2005-lmct x25 175_PF",
        "OFB-DBFH:7 2005-lmct x110 770_PF",
        "OFB-DBFH:8 2096-unlm 1_DH",
        "OFB-DBFH:9 2097-unlm 1_DH",
        "OFB-DBFH:10 2098-unlm 1_DH",
        "OFB-DBFH:11 2153-lmct x25 175_PF",
        "OFB-DBFH:12 2153-lmct x110 770_PF",
        "OFB-DBFH:13 3015-unlm 6720_PF",
        "OFB-DBFH:14 3015-tlmd 1d 250_DV",
        "OFB-DBFH:15 3015-tlmd 3d 500_DV",
        "OFB-DBFH:16 3015-tlmd 7d 1000_DV",
        "OFB-DBFH:17 3015-tlmd 30d 2500_DV",
        "OFB-DBFH:18 3042-unlm 6720_PF",
        "OFB-DBFH:19 3042-tlmd 1d 250_DV",
        "OFB-DBFH:20 3042-tlmd 3d 500_DV",
        "OFB-DBFH:21 3042-tlmd 7d 1000_DV",
        "OFB-DBFH:22 3042-tlmd 30d 2500_DV",
        "OFB-DBFH:23 5001-unlm 1680_PF",
        "OFB-DBFH:24 5001-tlmd 30d 840_PF",
        "OFB-DBFH:25 9000-tlmd 30d 6299_PF",
    ]
    # The game asks for the list compressed and inflates it to the same document.
    assert packed[1]["Content-Encoding"] == "gzip"
    assert gzip.decompress(packed[2]) == plain[2]


async def test_a_later_import_keeps_the_admins_products_and_takes_out_withheld_items(
    prompt_actions: None,
    server_url: str,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await cli(capsys, "catalogue", "import", str(catalogue_file(tmp_path)))
    # Stocked, and the list not yet asked for: the server keeps the list it built until the
    # store changes through the admin's actions.
    with anyio.fail_after(30):
        while not await select_value(database_url, "SELECT count(*) FROM store_product"):
            await anyio.sleep(0.05)
    # The admin puts the coat on sale and takes the boost out of the store; an earlier release,
    # whose game data did not withhold the logo shirt yet, stocked it.
    await execute(
        database_url,
        "UPDATE store_product SET price = 1999, original_price = 2600 WHERE id = 1",
    )
    await execute(database_url, "DELETE FROM store_product WHERE item_id = 9000")
    await execute(
        database_url,
        "INSERT INTO store_product (game, item_id, type, currency, price, sort)"
        " VALUES ('bfheroes', 257, 'unlm', '_PF', 2600, 99)",
    )
    stocked = [listed(product) for product in await offers(server_url)]
    assert stocked[-1].endswith(" 257-unlm 2600_PF")
    before = stocked[:-1]

    # A newer copy of the game brings another emote.
    await cli(
        capsys, "catalogue", "import", str(catalogue_file(tmp_path, [*ITEMS, item(5002, "emote")]))
    )
    # The server reads the admin's actions every 0.05 seconds here.
    await anyio.sleep(0.5)
    after = await offers(server_url)

    assert [listed(product) for product in after] == before
    assert before[0] == "OFB-DBFH:1 3-unlm 1999_PF"
    assert not any("9000-" in line for line in before)
    on_sale = after[0].find("attributes/attribute[@name='originalPrice']")
    assert on_sale is not None and on_sale.get("value") == "2600"


async def test_valor_point_rentals_round_down_to_50_while_funds_rentals_stay_exact(
    bfheroes: Game,
) -> None:
    rules = bfheroes.store
    assert rules is not None
    valor = PriceRule(kind="weapon", currency="_DV", value=5555)
    weeks = [Ownership(RENTED, days=days) for days in (1, 3, 7, 30)]
    assert [rules.price(valor, days) for days in weeks] == [250, 550, 1100, 2750]
    # A week of a Super weapon stays worth the 629 Funds of the class supply drops that drop it.
    funds = PriceRule(kind="weapon", currency="_PF", value=3360)
    assert rules.price(funds, Ownership(RENTED, days=7)) == 672
