from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from accounts import select_value
from heroes import wallets
from store import (
    CHEER,
    ITEMS,
    ROYAL_SOLDIER,
    attribute,
    buy,
    deals,
    grant,
    items,
    shopper,
    stock,
    without_rotation,
)

from openfesl.core.store import Bundle
from openfesl.storage.store import SqlProductStore

pytestmark = pytest.mark.anyio

# The invented catalogue's products: the Royal coat for good, the National tunic, the Royal pet
# and 25 bandages, and the cheer for good where it is stocked.
COAT, TUNIC, PET, BANDAGES, CHEER_FOR_GOOD = 1, 3, 5, 6, 25


@pytest.mark.usefixtures("prompt_actions")
async def test_a_hot_deal_sells_its_members_together_while_it_runs(
    base: str,
    server_url: str,
    database_url: str,
    products: SqlProductStore,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await stock(server_url, tmp_path, capsys, [*ITEMS, CHEER])
    await without_rotation(database_url)
    now = datetime.now(UTC)
    royal_set = Bundle(
        "WEST_Custom_Bundle_Invented",
        "WEST_Custom_Bundle_Invented_HotDealsDesc",
        "_PF",
        2000,
        original_price=3675,
        faction="royal",
        game_order=1,
        members=(COAT, PET, BANDAGES, CHEER_FOR_GOOD),
    )
    number = await products.add_bundle("bfheroes", royal_set)
    # A deal that ended yesterday and one that starts tomorrow, named by their own texts.
    ended = Bundle("Old Set", None, "_PF", 10, ends=now - timedelta(days=1), members=(TUNIC,))
    later = Bundle("Later Set", None, "_PF", 10, starts=now + timedelta(days=1), members=(TUNIC,))
    gone = [await products.add_bundle("bfheroes", bundle) for bundle in (ended, later)]
    cookie, hero = await shopper(server_url, "ann@example.test", "Brassbuckle", **ROYAL_SOLDIER)
    national, other = await shopper(server_url, "bob@example.test", "Mudlark")

    polish, german = await deals(server_url, "pl"), await deals(server_url, "de")
    # The game names Brazilian Portuguese br; a language it does not have is English.
    brazilian, unknown = await deals(server_url, "br"), await deals(server_url, "x" * 2000)
    # The hero owns the pet already, and the cheer as the game grants it, which the deal then
    # leaves out.
    assert await buy(server_url, cookie, hero, PET) == ("success", None)
    await grant(database_url, hero, "_PF", 1000)
    bought = await buy(server_url, cookie, hero, number)
    refused = [
        await buy(server_url, national, other, number),
        *[await buy(server_url, cookie, hero, bundle) for bundle in gone],
    ]

    [deal] = polish
    assert deal.get("productId") == f"OFB-DBFH:{number}"
    assert attribute(deal, "Product Name") == "Wymyślony zestaw"
    assert attribute(deal, "hotdealsDescription") == "Płaszcz, zwierzak, bandaże."
    assert [attribute(deal, "Product Name") for deal in german] == ["Invented Set"]
    assert [attribute(deal, "Product Name") for deal in brazilian] == ["Conjunto inventado"]
    assert [attribute(deal, "Product Name") for deal in unknown] == ["Invented Set"]
    point = deal.find("PricePoints/pricePoint")
    assert point is not None and (point.get("currency"), point.get("price")) == ("_PF", "2000")
    # Each member as an offer with its own price.
    assert [
        (member.get("productId"), attribute(member, "entitlementTag"))
        for member in deal.iterfind("association/product")
    ] == [
        ("OFB-DBFH:1", "3-unlm"),
        ("OFB-DBFH:5", "521-unlm"),
        ("OFB-DBFH:6", "2005-lmct"),
        ("OFB-DBFH:25", "5016-unlm"),
    ]
    assert bought == ("success", None)
    assert refused == [
        ("error", "NOT_FOR_HERO"),
        ("error", "UNKNOWN_PRODUCT"),
        ("error", "UNKNOWN_PRODUCT"),
    ]
    assert (await wallets(server_url, cookie, hero))["_PF"] == 2500 - 900 + 1000 - 2000
    assert await items(server_url, cookie, hero) == ["2005-lmct 35", "521-unlm 0", "3-unlm 0"]
    assert (
        await select_value(database_url, "SELECT outcome::text FROM purchase WHERE kind = 'bundle'")
        == '{"items": [3, 2005]}'
    )
