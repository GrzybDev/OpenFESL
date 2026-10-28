from openfesl.formats.language_table import read_language_table


def test_a_language_table_gives_each_columns_texts_by_id_as_written() -> None:
    lines = [
        "STRINGID\tENG_US\tPOL_PL\tCATEGORY",
        'INVENTED_HAT\t"Lucky" hat\tKapelusz „Fart”\tSHARED',
        "INVENTED_HAT _LongDesc\tA hat.\t\tSHARED",
        "\tNo id\tBez id\tSHARED",
        "INVENTED_SHORT\tShort line",
        "",
    ]
    data = "\r\n".join(lines).encode("utf-16")

    columns = read_language_table(data)

    # Quotes are text, a stray space stays in its id, and empty cells hold nothing.
    assert columns == {
        "ENG_US": {
            "INVENTED_HAT": '"Lucky" hat',
            "INVENTED_HAT _LongDesc": "A hat.",
            "INVENTED_SHORT": "Short line",
        },
        "POL_PL": {"INVENTED_HAT": "Kapelusz „Fart”"},
        "CATEGORY": {"INVENTED_HAT": "SHARED", "INVENTED_HAT _LongDesc": "SHARED"},
    }
