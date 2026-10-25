import re
from importlib.resources import files

from babel import Locale
from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

from openfesl.core.texts import BASE_LANGUAGE, languages

PLACEHOLDER = re.compile(r"%\((\w+)\)s|\{(\w+)\}")
CATEGORIES = (".zero", ".one", ".two", ".few", ".many", ".other")


def texts(language: str) -> dict[str, str]:
    """Return a language's texts by key, as its catalogue holds them."""
    entry = files("openfesl") / "locale" / language / "LC_MESSAGES" / "messages.po"
    with entry.open("rb") as file:
        catalog: Catalog = read_po(file, locale=language)
    return {
        message.id: message.string
        for message in catalog
        if isinstance(message.id, str) and message.id and isinstance(message.string, str)
    }


def placeholders(text: str) -> set[str]:
    return {first or second for first, second in PLACEHOLDER.findall(text)}


def test_every_language_has_every_text_with_its_placeholders_and_plural_forms() -> None:
    base = texts(BASE_LANGUAGE)
    plurals = {key.rsplit(".", 1)[0] for key in base if key.endswith(".other")}
    for language in languages():
        found = texts(language)
        # A text without a translation would show in English on that language's pages.
        singular = {key for key in base if not key.endswith(CATEGORIES)}
        assert singular - found.keys() == set(), language
        for key in singular:
            assert placeholders(found[key]) == placeholders(base[key]), (language, key)
        forms = Locale.parse(language).plural_form.tags | {"other"}
        for key in plurals:
            for form in forms:
                text = found.get(f"{key}.{form}", "")
                assert text, (language, key, form)
                assert placeholders(text) == placeholders(base[f"{key}.other"]), (language, key)
