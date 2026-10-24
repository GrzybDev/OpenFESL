import hashlib
from functools import cache
from importlib.resources import files

from babel import Locale, UnknownLocaleError

# Regions and placeholders that CLDR lists among territories but that are no country.
_NOT_COUNTRIES = frozenset({"EU", "EZ", "UN", "QO", "XA", "XB", "ZZ"})


class Terms:
    """The service's terms that the game shows on registration and whenever their version changes.

    The version is taken from the English text, so any change to it asks every player again.
    """

    def __init__(self, texts: dict[str, str]) -> None:
        """Keep the terms by language.

        Args:
            texts: The text of the terms by language code, English included.
        """
        self._texts = texts
        self.version = hashlib.sha256(texts["en"].encode()).hexdigest()[:12]

    @classmethod
    def load(cls) -> Terms:
        """Read the terms that ship with the server."""
        folder = files("openfesl") / "terms"
        return cls(
            {
                entry.name.removesuffix(".txt"): entry.read_text("utf-8").strip()
                for entry in folder.iterdir()
                if entry.name.endswith(".txt")
            }
        )

    def text(self, locale: str) -> str:
        """Return the terms in the game's language, or in English when there is no translation.

        Args:
            locale: The locale of the game's Hello, such as pl_PL.
        """
        return self._texts.get(locale.split("_")[0].lower(), self._texts["en"])


def countries(locale: str) -> list[tuple[str, str]]:
    """Return the ISO code and name of every country, named in the game's language.

    Args:
        locale: The locale of the game's Hello, such as pl_PL.
    """
    language = locale.split("_")[0].lower()
    return _countries(language if language.isalpha() and len(language) <= 3 else "en")


@cache
def _countries(language: str) -> list[tuple[str, str]]:
    try:
        names = Locale.parse(language).territories
    except ValueError, UnknownLocaleError:
        names = Locale("en").territories
    return sorted(
        (
            (code, name)
            for code, name in names.items()
            if len(code) == 2 and code.isalpha() and code not in _NOT_COUNTRIES
        ),
        key=lambda country: country[1].casefold(),
    )


def is_country(code: str) -> bool:
    """Tell whether a code names a country of the registration list.

    Args:
        code: An ISO 3166 alpha-2 code.
    """
    return code.upper() in dict(countries("en"))
