import sys
import urllib.request

# Only the standard library: the container's probe has to finish within its timeout, and on a
# small machine such as a Raspberry Pi importing the server takes longer than that.

DEFAULT_PORT = 8000


def healthy(port: int = DEFAULT_PORT) -> bool:
    """Return whether the server on this machine answers its health check.

    Args:
        port: The port the server listens on.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            status: int = response.status
    except OSError:
        return False
    return status == 200


if __name__ == "__main__":
    sys.exit(0 if healthy() else 1)
