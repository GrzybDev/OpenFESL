import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from openfesl.server.settings import Settings, SmtpSecurity

TIMEOUT = 10.0


class SmtpMailer:
    """Sends e-mail through the deployment's mail server."""

    def __init__(self, settings: Settings, host: str, sender: str) -> None:
        """Send through the mail server the settings name.

        Args:
            settings: The deployment's settings, with the port, encryption and login.
            host: The mail server.
            sender: The address mail comes from.
        """
        self._settings = settings
        self._host = host
        self._sender = sender

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
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate()
        message["Message-ID"] = make_msgid(domain=self._sender.rpartition("@")[2] or None)
        message.set_content(body)
        # After the content, which replaces every Content- header.
        message["Content-Language"] = language
        await asyncio.to_thread(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        settings = self._settings
        context = ssl.create_default_context()
        if settings.smtp_security is SmtpSecurity.TLS:
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, settings.smtp_port, timeout=TIMEOUT, context=context
            )
        else:
            client = smtplib.SMTP(self._host, settings.smtp_port, timeout=TIMEOUT)
        with client:
            if settings.smtp_security is SmtpSecurity.STARTTLS:
                client.starttls(context=context)
            if settings.smtp_user and settings.smtp_password:
                client.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            client.send_message(message)
