import os
import signal
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="Windows sends no SIGINT to one process")
def test_ctrl_c_stops_the_server_without_a_traceback(database_url: str) -> None:
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from openfesl.server.cli import main; main()",
            *("serve", "--host", "127.0.0.1", "--port", "0"),
        ],
        env=os.environ | {"DATABASE_URL": database_url.replace("+asyncpg", "", 1)},
        stderr=subprocess.PIPE,
        text=True,
    )
    assert server.stderr is not None
    for line in server.stderr:
        if "Application startup complete" in line:
            break
    server.send_signal(signal.SIGINT)
    _, stopping = server.communicate(timeout=30)

    assert server.returncode == 0, stopping
    assert "Application shutdown complete" in stopping and "Traceback" not in stopping
