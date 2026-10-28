from dataclasses import dataclass

from openfesl.core.accounts import ADDRESS, MAXIMUM_NAME, MAXIMUM_PASSWORD, MINIMUM_PASSWORD

SHORTEST_NAME = 4
LONGEST_NAME = 16


@dataclass(frozen=True, slots=True)
class Problem:
    """What is wrong with a value that one of the site's account forms sent.

    Attributes:
        field: The form's field.
        kind: What is wrong, such as short or taken.
        detail: What the page names of the value sent, such as its length.
    """

    field: str
    kind: str
    detail: str = ""


def address_problem(address: str) -> Problem | None:
    """Return what is wrong with an e-mail address that is to log in to games, if anything.

    Args:
        address: The address.
    """
    if len(address) > MAXIMUM_NAME:
        return Problem("email", "long", str(len(address)))
    if not ADDRESS.fullmatch(address):
        return Problem("email", "form")
    return None


def password_problem(password: str) -> Problem | None:
    """Return what is wrong with a new game password, if anything.

    Args:
        password: The password.
    """
    if len(password) < MINIMUM_PASSWORD:
        return Problem("password", "short", str(len(password)))
    if len(size := password.encode()) > MAXIMUM_PASSWORD:
        return Problem("password", "long", str(len(size)))
    return None


def display_name_problem(name: str) -> Problem | None:
    """Return what is wrong with a display name, apart from its being taken, if anything.

    Args:
        name: The display name.
    """
    others = dict.fromkeys(
        character
        for character in name
        if not (character.isascii() and (character.isalnum() or character in "_-"))
    )
    if others:
        return Problem("display_name", "characters", "".join(others))
    if not SHORTEST_NAME <= len(name) <= LONGEST_NAME:
        return Problem("display_name", "length", str(len(name)))
    return None
