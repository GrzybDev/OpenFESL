from importlib.resources import files


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
