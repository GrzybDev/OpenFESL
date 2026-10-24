import subprocess
import sys

import anyio
import pytest

from openfesl.server.cli import RelayServer
from openfesl.server.health import healthy

pytestmark = pytest.mark.anyio


async def test_the_container_probe_answers_without_loading_the_server(
    relay_server: tuple[str, RelayServer],
) -> None:
    _, server = relay_server
    port = server.servers[0].sockets[0].getsockname()[1]
    assert await anyio.to_thread.run_sync(healthy, port)

    # On a Raspberry Pi importing the server outlasts the probe's 5 s timeout.
    loaded = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, openfesl.server.health; print(sorted(sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for heavy in ("sqlalchemy", "uvicorn", "fastapi", "pydantic"):
        assert f"'{heavy}'" not in loaded
