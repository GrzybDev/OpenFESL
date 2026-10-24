from ipaddress import ip_address

from fastapi.requests import HTTPConnection
from pydantic_settings import BaseSettings

from openfesl.core.connection import Address


class Settings(BaseSettings):
    """The deployment's settings, read from the environment.

    Attributes:
        behind_trusted_proxy: Whether every request comes through a proxy that sets
            client_ip_header; any client could set the header itself otherwise.
        client_ip_header: The header that carries the client's address behind the proxy, such as
            CF-Connecting-IP.
        log_level: The lowest level logged; DEBUG shows every packet, secrets masked.
    """

    behind_trusted_proxy: bool = False
    client_ip_header: str | None = None
    log_level: str = "INFO"

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
