from ipaddress import ip_address

from fastapi.requests import HTTPConnection
from pydantic import field_validator
from pydantic_settings import BaseSettings
from sqlalchemy.engine import make_url

from openfesl.core.connection import Address


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
    """

    database_url: str
    behind_trusted_proxy: bool = False
    client_ip_header: str | None = None
    log_level: str = "INFO"

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
