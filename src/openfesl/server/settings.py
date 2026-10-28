from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated

from fastapi.requests import HTTPConnection
from pydantic import SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

from openfesl.core.connection import Address
from openfesl.web.oidc import SignInService
from openfesl.web.turnstile import Turnstile

# A Discord application's id, a snowflake of up to 20 digits.
type ApplicationId = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]


class SmtpSecurity(StrEnum):
    """How the connection to the mail server is encrypted."""

    STARTTLS = "starttls"
    """A plain connection upgraded with STARTTLS, usually on port 587."""
    TLS = "tls"
    """TLS from the start, usually on port 465."""
    NONE = "none"
    """No encryption, as for a mail server on the same host."""


class Settings(BaseSettings):
    """The deployment's settings, read from the environment.

    Attributes:
        behind_trusted_proxy: Whether every request comes through a proxy that sets
            client_ip_header; any client could set the header itself otherwise.
        client_ip_header: The header that carries the client's address behind the proxy, such as
            CF-Connecting-IP.
        log_level: The lowest level logged; DEBUG shows every packet, secrets masked.
        database_url: The PostgreSQL database, as a SQLAlchemy URL with the asyncpg driver; a
            plain postgresql:// or postgres:// URL gets that driver.
        smtp_host: The mail server that sends the links confirming new accounts' addresses;
            without one no mail goes out.
        smtp_port: The mail server's port.
        smtp_security: How the connection to the mail server is encrypted.
        smtp_user: The name to log in to the mail server with, if it needs a login.
        smtp_password: The password of that login.
        smtp_sender: The address mail comes from.
        public_url: The address the server's web pages are reached at, where the links in
            mail lead and Discord fetches presence's images from, such as
            https://fesl.example.com; the relay's address follows from it.
        contact_email: The address at which the operators of dedicated servers reach this
            server's administrators, such as for a provider file.
        hook_default_url: The relay address OpenFESLHook's releases connect to when no
            OpenFESLHook.ini names another; the guide to playing explains that file to players
            of other servers.
        session_secret: The key that signs the session cookie of the admin panel and the site's
            accounts; without one each start of the server picks its own, which signs everyone
            out.
        admin_oidc_name: The name of the admin panel's sign-in provider on its button.
        admin_oidc_issuer: The OpenID Connect issuer the admins sign in through, whose discovery
            document names its endpoints, such as https://auth.example.com/application/o/fesl/;
            without it and the client's id and secret nobody signs in to the panel.
        admin_oidc_client_id: The panel's client id at the provider.
        admin_oidc_client_secret: The panel's client secret there.
        admin_group: The provider's group whose members are admins.
        moderator_group: The provider's group whose members are moderators.
        discord_application_id: The id of the Discord application whose Rich Presence shows
            what players do in their games, sent to the hooks that ask for it; without one no
            presence is sent.
        turnstile_site_key: The site key of Cloudflare Turnstile, whose check guards the forms
            that take an e-mail address or send mail; without it and the secret key no form is
            checked.
        turnstile_secret_key: The secret key of Cloudflare Turnstile.
        sign_in_services: The services that players sign in to the site through, as a JSON
            list of their settings, such as {"id": "discord", "name": "Discord", "kind":
            "oauth2", ...}; their tiles show in this order. Their pages send the browser back
            to /sign-in/callback.
        files_path: The folder where the server keeps the files it serves besides its own,
            such as the icons of the games' imported catalogues; without one it keeps none.
    """

    database_url: str
    behind_trusted_proxy: bool = False
    client_ip_header: str | None = None
    log_level: str = "INFO"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: SmtpSecurity = SmtpSecurity.STARTTLS
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_sender: str | None = None
    public_url: str | None = None
    contact_email: str | None = None
    hook_default_url: str = "wss://fesl.grzyb.app"
    session_secret: SecretStr | None = None
    admin_oidc_name: str = "Authentik"
    admin_oidc_issuer: str | None = None
    admin_oidc_client_id: str | None = None
    admin_oidc_client_secret: SecretStr | None = None
    admin_group: str | None = None
    moderator_group: str | None = None
    discord_application_id: ApplicationId | None = None
    turnstile_site_key: str | None = None
    turnstile_secret_key: SecretStr | None = None
    sign_in_services: list[SignInService] = []
    files_path: Path | None = None

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        # Hosts such as Dokploy hand out plain URLs, for which SQLAlchemy would pick psycopg.
        url = make_url(value)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)

    @field_validator("discord_application_id", mode="before")
    @classmethod
    def _empty_as_unset(cls, value: object) -> object:
        # Hosts such as Dokploy switch a variable off by leaving it empty.
        return value or None

    @property
    def sends_mail(self) -> bool:
        """Whether the server sends mail, which needs a mail server, a sender and a public URL."""
        return bool(self.smtp_host and self.smtp_sender and self.public_url)

    @property
    def turnstile(self) -> Turnstile | None:
        """Cloudflare Turnstile's check of the forms, which needs its site key and secret key."""
        secret = self.turnstile_secret_key
        if not (self.turnstile_site_key and secret):
            return None
        return Turnstile(self.turnstile_site_key, secret.get_secret_value(), self.client_address)

    def client_address(self, connection: HTTPConnection) -> Address | None:
        """Return the address of the client behind a request or WebSocket.

        Args:
            connection: The request or WebSocket.
        """
        host = connection.client.host if connection.client else None
        if self.behind_trusted_proxy and self.client_ip_header:
            host = connection.headers.get(self.client_ip_header, host)
        try:
            return ip_address(host.strip()) if host else None
        except ValueError:
            return None
