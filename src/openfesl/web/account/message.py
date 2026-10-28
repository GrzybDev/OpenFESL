from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import HTMLResponse

from openfesl.core.texts import translate
from openfesl.web.pages import Pages

# The pages that links with a secret token open, which neither caches nor other sites may keep.
SECRET = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def message_page(
    pages: Pages,
    request: Request,
    status: int,
    key: str,
    action: tuple[str, str],
    rows: tuple[str, ...] = (),
    headers: Mapping[str, str] | None = None,
    **values: object,
) -> HTMLResponse:
    """Render a notice page of the site's accounts, such as the one after registration.

    Args:
        pages: Renders the page.
        request: The request the page answers.
        status: The response's status code.
        key: The catalogues' key of the page's title, whose .subtitle and .text follow it.
        action: The path the page's one action leads to and the key of its label.
        rows: The keys of the rows that explain the text further.
        headers: Headers of the response; a page reached through a secret link is never kept
            and offers no other language, in which opening the link again would find it used.
        values: The texts' placeholders.
    """
    language = request.state.language

    def text(name: str) -> str:
        return translate(name, language) % values

    href, label = action
    return pages.render(
        request,
        "account.Message",
        status,
        steel=True,
        switch=headers is None,
        headers=headers,
        title=text(key),
        subtitle=text(f"{key}.subtitle"),
        text=text(f"{key}.text"),
        rows=[text(row) for row in rows],
        href=href,
        action=translate(label, language),
    )
