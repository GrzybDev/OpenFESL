from xml.etree.ElementTree import Element, tostring

NOT_LOGGED_IN = 2

_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>'


def empty_locker(game: str, owner: int, persona: str) -> bytes:
    """Return the answer to a locker listing for a locker that holds no files and takes none.

    Args:
        game: The partition the game asked for, such as /eagames/BFBC2.
        owner: The number of the persona whose session asked.
        persona: The persona the game asked for.
    """
    return _locker(
        error="0",
        game=game,
        maxBytes="0",
        maxFiles="0",
        numBytes="0",
        numFiles="0",
        ownr=str(owner),
        pers=persona,
    )


def locker_error(code: int) -> bytes:
    """Return the answer to a locker request that failed.

    Args:
        code: The locker's error, such as NOT_LOGGED_IN for an unknown session key.
    """
    return _locker(error=str(code))


def _locker(**attributes: str) -> bytes:
    return (_DECLARATION + tostring(Element("LOCKER", attributes), encoding="unicode")).encode()
