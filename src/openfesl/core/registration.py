import hashlib
from datetime import UTC, date, datetime
from functools import cache

from babel import Locale, UnknownLocaleError

from openfesl.core.games import Ages
from openfesl.core.policies import RegistrationPolicies, UnderParentalAge
from openfesl.core.texts import language_of, load_texts
from openfesl.protocol.fesl import FieldError

# The game maps a field error one lower before it picks a text, so its "password is too long"
# text takes 3; 2 shows its notice to fill in every field properly.
TOO_LONG = 3
INVALID_VALUE = 6
# 16 shows the game's birth date refusal and keeps the form; 15 asks for a parent's e-mail,
# which the PC game has no form for, so it shows the same refusal and disconnects.
NEEDS_CONSENT = 15
TOO_YOUNG = 16

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
        return cls(load_texts("terms"))

    def text(self, locale: str) -> str:
        """Return the terms in the game's language, or in English when there is no translation.

        Args:
            locale: The locale of the game's Hello, such as pl_PL.
        """
        return self._texts.get(language_of(locale), self._texts["en"])


def countries(locale: str) -> list[tuple[str, str]]:
    """Return the ISO code and name of every country, named in the game's language.

    Args:
        locale: The locale of the game's Hello, such as pl_PL.
    """
    language = language_of(locale)
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


def birth_date_error(
    year: int, month: int, day: int, ages: Ages, policies: RegistrationPolicies
) -> FieldError | None:
    """Return what is wrong with a birth date from a form, if anything.

    The date is only checked against the country's age limits and never kept; without the
    game's age check it is not checked at all.

    Args:
        year: The year of birth.
        month: The month of birth.
        day: The day of birth.
        ages: The age limits of the player's country.
        policies: How the game checks ages.
    """
    if not policies.age_check:
        return None
    try:
        age = _age(date(year, month, day))
    except ValueError, OverflowError:
        return FieldError(field_name="dob", field_error=INVALID_VALUE)
    if age < ages.registration_age:
        return FieldError(field_name="dob", field_error=TOO_YOUNG)
    if age < ages.parental_age and policies.under_parental_age is UnderParentalAge.REFUSE:
        return FieldError(field_name="dob", field_error=NEEDS_CONSENT)
    return None


def _age(born: date) -> int:
    today = datetime.now(UTC).date()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
