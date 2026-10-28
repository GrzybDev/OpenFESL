def read_language_table(data: bytes) -> dict[str, dict[str, str]]:
    """Read a game's language table: UTF-16 lines of tab-separated cells under a header line.

    The first column holds each text's id and every other column its text in one language,
    named in the header. Cells are taken as they are, quotes included, since the table has no
    quoting; empty cells and lines without an id hold no text.

    Args:
        data: The table's file, with its byte order mark.

    Returns:
        The texts of each column but the first, by the column's name and then by id.
    """
    lines = data.decode("utf-16").split("\n")
    header, *rows = (line.removesuffix("\r").split("\t") for line in lines)
    columns: dict[str, dict[str, str]] = {name: {} for name in header[1:]}
    for text_id, *cells in rows:
        if not text_id:
            continue
        for name, cell in zip(header[1:], cells, strict=False):
            if cell:
                columns[name][text_id] = cell
    return columns
