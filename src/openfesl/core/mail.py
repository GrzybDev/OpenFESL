import asyncio
import logging
from collections.abc import Mapping
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

    @property
    def sends(self) -> bool:
        """Whether the deployment set up the mail server and the address the links lead to."""
        return self._mailer is not None and self._public_url is not None

    def send(
        self,
        to: str,
        texts: str,
        language: str,
        links: Mapping[str, str],
        times: Mapping[str, datetime] | None = None,
        **values: str,
    ) -> None:
        """Start sending a mail whose links lead to the server's pages.

        Nothing waits for the mail: a game gives up on its request after 20 s, which a slow mail
        server can take. A mail that does not reach the mail server is only logged.

        Args:
            to: The recipient's address.
            texts: The catalogues' key of the mail, whose subject and body are its .subject and
                .body.
            language: The language code of the game or page it was asked from; English where
                the catalogues hold no texts in it.
            links: The pages its links lead to, without a language, by the body's placeholder.
            times: The moments the body names, such as when its link expires, by placeholder.
            values: The body's other placeholders.
        """
        if self._mailer is None or self._public_url is None:
            logger.error("Cannot send %s: no mail server or public URL is set", texts)
            return
        # The dates and the mail's language follow the texts, which would otherwise be English
        # around a date in the game's language, and Babel refuses locales it does not know.
        if language not in languages():
            language = BASE_LANGUAGE
        # The links open the pages in the language of the mail.
        base = self._public_url.rstrip("/")
        filled = {name: base + localize(path, language) for name, path in links.items()}
        for name, moment in (times or {}).items():
            filled[name] = format_datetime(moment, "long", tzinfo=UTC, locale=language)
        subject = translate(f"{texts}.subject", language)
        body = translate(f"{texts}.body", language).format(**filled, **values)
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
