from babel.lists import format_list

from openfesl.core.problems import Problem
from openfesl.core.texts import translate
from openfesl.web.pages import locale_of

_LETTERS = {"day": "d", "month": "M", "year": "y"}


def field_errors(problems: list[Problem], language: str) -> dict[str, str]:
    """Return what a form's page says under each field that must change, by the field's name.

    Args:
        problems: What is wrong with the values the form sent.
        language: The page's language code.
    """
    return {problem.field: _sentence(problem, language) for problem in problems}


def birth_order(language: str) -> list[str]:
    """Return the parts of a birth date in the order the language writes a date.

    Args:
        language: The page's language code.
    """
    pattern = str(locale_of(language).date_formats["short"].pattern)
    return sorted(_LETTERS, key=lambda part: pattern.find(_LETTERS[part]))


def _sentence(problem: Problem, language: str) -> str:
    text = translate(f"web.account.error.{problem.field}.{problem.kind}", language)
    locale = locale_of(language)
    match problem:
        case Problem("display_name", "characters", characters):
            space = translate("web.account.error.space", language)
            named = [space if character == " " else character for character in characters]
            return text % {"characters": format_list(named, locale=locale)}
        case Problem("birth", "day", detail):
            day, month = detail.split("-")
            return text % {"day": day, "month": locale.months["format"]["wide"][int(month)]}
    return text % {"detail": problem.detail}
