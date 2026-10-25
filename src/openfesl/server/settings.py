from enum import StrEnum
from ipaddress import ip_address

from fastapi.requests import HTTPConnection
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

from openfesl.core.connection import Address


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
            mail lead, such as https://fesl.example.com.
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

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        # Hosts such as Dokploy hand out plain URLs, for which SQLAlchemy would pick psycopg.
        url = make_url(value)
        if url.drivername in {"postgres", "postgresql"}:
            url = url.set(drivername="postgresql+asyncpg")
        return url.render_as_string(hide_password=False)

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
