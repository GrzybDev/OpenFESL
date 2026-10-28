import gzip
from pathlib import Path

import anyio
import pytest
from game_files import COPY, NEEDS_COPY
from heroes import MAGMA
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from store import offers
from support import cli, fetch

from openfesl.core.drops import below_value
from openfesl.core.games import load_games
from openfesl.extraction.catalogue import extract, find_game
from openfesl.storage.models import StoreBoxRow
from openfesl.storage.store import SqlProductStore

pytestmark = [
    NEEDS_COPY,
    pytest.mark.anyio,
]


@pytest.mark.usefixtures("prompt_actions")
async def test_the_store_data_names_the_copys_items_and_every_box_is_worth_its_price(
    server_url: str, database_url: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert COPY
    folder = Path(COPY)
    game = find_game(load_games(), folder)
    catalogue = extract(game, folder)
    file = tmp_path / "bfheroes-catalogue.zip"
    file.write_bytes(catalogue.write())
    await cli(capsys, "catalogue", "import", str(file))
    with anyio.fail_after(30):
        while not await offers(server_url):
            await anyio.sleep(0.1)

    _, _, plain = await fetch(server_url, "/ofb/products", MAGMA)
    _, _, packed = await fetch(server_url, "/ofb/products", MAGMA, {"Accept-Encoding": "gzip"})
    engine = create_async_engine(database_url)
    store = SqlProductStore(async_sessionmaker(engine))
    async with engine.connect() as connection:
        numbers = list(await connection.scalars(select(StoreBoxRow.id).order_by(StoreBoxRow.id)))
    boxes = [await store.box(game.id, number) for number in numbers]
    await engine.dispose()

    rules = game.store
    assert rules
    named = {item for rule in rules.prices for item in rule.items}
    for box in rules.boxes:
        named |= {item for tier in box.tiers for item, _ in tier.drops}
        named |= {box.fallback[0]} if box.fallback else set()
    missing = named - {item.id for item in catalogue.document.items}
    with capsys.disabled():
        print(
            f"\n{len(await offers(server_url))} products: {len(plain):,} bytes,"
            f" {len(gzip.decompress(packed)):,} inflated from {len(packed):,};"
            f" {len(boxes)} boxes; items the copy lacks: {sorted(missing)}"
        )
    # The store's data is that of the game that sells hot deals, as 1.89 does and 1.46 not.
    if b"game/hotDeals" in (folder / game.executable).read_bytes():
        assert missing == set()
        assert len(boxes) == len(rules.boxes) and all(box and box.drops for box in boxes)
    assert all(box and not below_value(box) for box in boxes)
