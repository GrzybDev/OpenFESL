import hashlib
import re
from base64 import b64encode
from pathlib import Path

from openfesl.core.games import Game
from openfesl.core.texts import BASE_LANGUAGE, languages

# The stylesheets and scripts of the pages a game shows in its own browser, which the pages
# carry inline: the game's window loads a page alone through the hook.
FOLDER = Path(__file__).with_name("ingame")
# What a stylesheet can do without: its comments, runs of whitespace and the spaces around its
# punctuation.
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SPACE = re.compile(r"\s+")
_AROUND = re.compile(r"\s*([{};,>])\s*")


def inline(name: str) -> str:
    """Return a stylesheet or script that the in-game pages carry inline, a stylesheet compacted.

    Args:
        name: The file's name, such as store-window.css.
    """
    text = (FOLDER / name).read_text("utf-8")
    if not name.endswith(".css"):
        return text
    # The game fetches every page anew through the hook.
    text = _SPACE.sub(" ", _COMMENT.sub("", text))
    return _AROUND.sub(lambda found: found[1], text).strip()


def digest(text: str) -> str:
    """Return the source that allows an inline style or script in a page's content policy.

    Args:
        text: The style's or script's text, exactly as the page holds it.
    """
    return f"'sha256-{b64encode(hashlib.sha256(text.encode()).digest()).decode()}'"


def language_of(game: Game, segment: str) -> str:
    """Return the language of an in-game page's texts: the game's where the site has texts in it.

    The site has texts in fewer languages than the game; in the others a page is English, while
    the game's own texts on it, such as the items' names, keep the game's language.

    Args:
        game: The game, which names its languages.
        segment: The language the game names at the start of the address, such as en or br.
    """
    language = game.language_of(segment)
    return language if language in languages() else BASE_LANGUAGE
