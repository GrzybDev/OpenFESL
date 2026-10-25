from html.parser import HTMLParser

import pytest
from support import fetch

from openfesl.core.registration import Terms
from openfesl.core.texts import load_texts

pytestmark = pytest.mark.anyio


class Page(HTMLParser):
    """The parts of a page that its tests read."""

    def __init__(self, body: bytes) -> None:
        super().__init__()
        self.language = ""
        self.heading = ""
        self.links: list[dict[str, str | None]] = []
        self.assets: list[str] = []
        self._in_heading = False
        self.feed(body.decode())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "html":
            self.language = found["lang"] or ""
        elif tag == "h1":
            self._in_heading = True
        elif tag == "a":
            self.links.append(found)
        elif tag in ("link", "script", "use") and found.get("rel") != "alternate":
            self.assets.append(found.get("href") or found.get("src") or "")

    def handle_endtag(self, tag: str) -> None:
        self._in_heading = self._in_heading and tag != "h1"

    def handle_data(self, data: str) -> None:
        if self._in_heading:
            self.heading += data

    def switch(self) -> dict[str | None, str | None]:
        """Return the shell's language links by language."""
        return {link["hreflang"]: link["href"] for link in self.links if link.get("hreflang")}


async def test_pages_come_in_the_language_their_address_names(server_url: str) -> None:
    status, headers, body = await fetch(server_url, "/terms")
    page = Page(body)
    assert (status, page.language, page.heading) == (200, "en", "Terms")
    assert Terms.load().version in body.decode()
    assert load_texts("terms")["en"].split("\n\n")[0] in body.decode()
    assert page.switch() == {"en": "/terms", "pl": "/pl/terms"}
    # Nothing injected into a page may run, and no other site may frame it.
    policy = headers["Content-Security-Policy"]
    assert "script-src 'self';" in policy
    assert "frame-ancestors 'none'" in policy
    assert headers["X-Content-Type-Options"] == "nosniff"

    status, _, body = await fetch(server_url, "/pl/terms")
    page = Page(body)
    assert (status, page.language, page.heading) == (200, "pl", "Warunki korzystania")
    assert load_texts("terms")["pl"].split("\n\n")[0] in body.decode()

    status, _, body = await fetch(server_url, "/pl/downloads?from=menu")
    page = Page(body)
    assert (status, page.language, page.heading) == (404, "pl", "Nie znaleziono strony")
    assert page.switch() == {"en": "/downloads?from=menu", "pl": "/pl/downloads?from=menu"}

    # Files that name their version are kept by browsers until they change.
    versioned = [asset for asset in page.assets if "?v=" in asset]
    assert versioned
    for asset in versioned:
        status, headers, _ = await fetch(server_url, asset)
        assert (status, headers["Cache-Control"]) == (200, "public, max-age=31536000, immutable")

    status, headers, body = await fetch(server_url, "/favicon.ico")
    assert (status, headers["Content-Type"], body[:4]) == (200, "image/png", b"\x89PNG")


async def test_a_page_that_fails_answers_a_short_error_page_without_details(
    server_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(terms: Terms, locale: str) -> str:
        raise RuntimeError("the terms file is unreadable")

    monkeypatch.setattr(Terms, "text", fail)
    status, headers, body = await fetch(server_url, "/pl/terms?again=1")
    page = Page(body)
    assert (status, page.language, page.heading) == (500, "pl", "Nie udało się wyświetlić strony")
    assert b"unreadable" not in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    # The ways out: the same page again, or the front page.
    assert {"/pl/terms?again=1", "/pl"} <= {link["href"] for link in page.links}
