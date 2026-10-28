import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

from babel.dates import format_datetime

from openfesl.core.texts import BASE_LANGUAGE, languages, localize, translate

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    """Sends e-mail."""

    async def send(self, to: str, subject: str, body: str, language: str) -> None:
        """Hand a plain-text e-mail to the mail server.

        Args:
            to: The recipient's address.
            subject: The subject.
            body: The text.
            language: The language code of the text.

        Raises:
            OSError: When the mail server cannot be reached or refuses the e-mail.
        """


class Outbox:
    """Sends the server's mail, each with a link to one of its pages, in the background."""

    def __init__(self, mailer: Mailer | None, public_url: str | None) -> None:
        """Send mail whose links lead to the server's pages.

        Args:
            mailer: Sends the e-mail; None when the deployment set no mail server.
            public_url: The address the server's pages are reached at, such as
                https://fesl.example.com; None when the deployment set none.
        """
        self._mailer = mailer
        self._public_url = public_url
        self._deliveries: set[asyncio.Task[None]] = set()

    def send(
        self, to: str, texts: str, path: str, expires: datetime, language: str, **values: str
    ) -> None:
        """Start sending a mail whose link leads to a page and stops working at a moment.

        Nothing waits for the mail: a game gives up on its request after 20 s, which a slow mail
        server can take. A mail that does not reach the mail server is only logged.

        Args:
            to: The recipient's address.
            texts: The catalogues' key of the mail, whose subject and body are its .subject and
                .body, with the placeholders link and expires.
            path: The page the link leads to, without a language.
            expires: When the link stops working.
            language: The language code of the game or page it was asked from; English where
                the catalogues hold no texts in it.
            values: The body's other placeholders.
        """
        if self._mailer is None or self._public_url is None:
            logger.error("Cannot send %s: no mail server or public URL is set", texts)
            return
        # The date and the mail's language follow the texts, which would otherwise be English
        # around a date in the game's language, and Babel refuses locales it does not know.
        if language not in languages():
            language = BASE_LANGUAGE
        # The link opens the page in the language of the mail.
        url = self._public_url.rstrip("/") + localize(path, language)
        until = format_datetime(expires, "long", tzinfo=UTC, locale=language)
        subject = translate(f"{texts}.subject", language)
        body = translate(f"{texts}.body", language).format(link=url, expires=until, **values)
        delivery = asyncio.create_task(_deliver(self._mailer, to, subject, body, language, texts))
        # The loop keeps only weak references to its tasks.
        self._deliveries.add(delivery)
        delivery.add_done_callback(self._deliveries.discard)


async def _deliver(
    mailer: Mailer, to: str, subject: str, body: str, language: str, texts: str
) -> None:
    try:
        await mailer.send(to, subject, body, language)
    except OSError:
        logger.exception("Could not send %s", texts)
