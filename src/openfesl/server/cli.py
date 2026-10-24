import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command named on the command line.

    Args:
        argv: The arguments after the program name; the process's own when omitted.
    """
    parser = argparse.ArgumentParser(
        prog="openfesl",
        description="Master server for games built on EA's FESL and Theater services.",
    )
    parser.parse_args(argv)
