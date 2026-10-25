from functools import cache
from importlib.resources import files

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

BASE_LANGUAGE = "en"


def load_texts(folder: str) -> dict[str, str]:
    """Read a text that ships with the server, one file per language such as en.txt.

    Args:
        folder: The package folder that holds the text's files.

    Returns:
        The text by language code, without the whitespace around it.
    """
    return {
        entry.name.removesuffix(".txt"): entry.read_text("utf-8").strip()
        for entry in (files("openfesl") / folder).iterdir()
        if entry.name.endswith(".txt")
    }


def language_of(locale: str) -> str:
    """Return the language code of a game's locale.

    Args:
        locale: The locale of the game's Hello, such as pl_PL.
    """
    return locale.split("_")[0].lower()


@cache
def languages() -> list[str]:
    """Return the languages the catalogues hold texts in, English first."""
    found = [entry.name for entry in (files("openfesl") / "locale").iterdir() if entry.is_dir()]
    return sorted(found, key=lambda language: (language != BASE_LANGUAGE, language))


def localize(path: str, language: str) -> str:
    """Return the address of a web page in a language.

    English pages have no prefix; the others start with their language code, such as /pl/terms.

    Args:
        path: The page's path without a language prefix, such as /terms.
        language: The language code.
    """
    if language == BASE_LANGUAGE:
        return path
    return f"/{language}" + ("" if path == "/" else path)


def translate(key: str, language: str) -> str:
    """Return a text from the catalogues, in English where a language has no translation.

    Args:
        key: The text's id in the catalogues.
        language: A language code such as pl.

    Raises:
        KeyError: When the English catalogue has no such text either.
    """
    for catalog in (_catalog(language), _catalog(BASE_LANGUAGE)):
        message = catalog.get(key) if catalog else None
        if message is not None and isinstance(message.string, str) and message.string:
            return message.string
    raise KeyError(key)


@cache
def _catalog(language: str) -> Catalog | None:
    if language not in languages():
        return None
    entry = files("openfesl") / "locale" / language / "LC_MESSAGES" / "messages.po"
    with entry.open("rb") as file:
        return read_po(file, locale=language)
