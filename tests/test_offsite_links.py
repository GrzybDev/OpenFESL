import httpx2
import pytest
from accounts import PASSWORD, execute, post_sign_in

from openfesl.web.sessions import inside

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "path",
    [
        "//evil.example",
        "/\\evil.example",
        "/\t/evil.example",
        "/\r/evil.example",
        "/\n/evil.example",
    ],
)
async def test_a_path_that_a_browser_reads_as_another_site_is_refused(path: str) -> None:
    assert inside(path, ("/",)) == ""
    assert inside("/terms", ("/",)) == "/terms"


@pytest.mark.parametrize(
    "path",
    ["/pl//evil.example", "//evil.example", "/%09/evil.example", "/%0a/evil.example"],
)
async def test_a_missing_pages_language_links_stay_on_the_site(base: str, path: str) -> None:
    async with httpx2.AsyncClient() as client:
        page = await client.get(f"{base}{path}")
    assert page.status_code == 404
    assert 'href="//' not in page.text and 'href="/pl//' not in page.text


async def test_the_terms_page_leaves_out_a_next_page_that_a_browser_reads_as_another_site(
    base: str, database_url: str
) -> None:
    await execute(
        database_url,
        "UPDATE account SET terms_version = 'older', display_name = 'Invented_Ann'"
        " WHERE nuid = 'ann@example.test'",
    )
    async with httpx2.AsyncClient() as client:
        await post_sign_in(client, base, "ann@example.test", PASSWORD)
        page = await client.get(f"{base}/account/terms?next=/%09/evil.example")
    assert 'href="/account"' in page.text and 'href="/\t' not in page.text
