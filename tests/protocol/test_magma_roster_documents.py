from datetime import UTC, datetime, timedelta, timezone

from openfesl.protocol.magma import (
    PersonaEntry,
    RelationshipEntry,
    personas,
    relationship,
    roster,
    status_update,
)


def test_a_roster_gives_each_relationship_every_child_the_game_requires() -> None:
    # Times to the second in UTC; the game reads an entityModified over 12 minutes old as gone.
    changed = datetime(2026, 10, 27, 13, 5, 9, tzinfo=timezone(timedelta(hours=1)))
    reported = datetime(2026, 10, 27, 12, 30, tzinfo=UTC)
    asked = RelationshipEntry(
        7, True, 2, "friends", "12", "nucleus", "", "Invented_Ann", changed, changed
    )
    friend = RelationshipEntry(
        8, False, 3, "friends", "13", "nucleus", "In <menu> & more", "Bob", changed, reported
    )

    assert roster([]) == b"<roster />"
    assert roster([asked, friend]) == (
        b'<roster><relationship id="7"><new>true</new><state>2</state><tag>friends</tag>'
        b"<realid>12</realid><type>nucleus</type><status /><name>Invented_Ann</name>"
        b"<modified>2026-10-27 12:05:09</modified>"
        b"<entityModified>2026-10-27 12:05:09</entityModified></relationship>"
        b'<relationship id="8"><new>false</new><state>3</state><tag>friends</tag>'
        b"<realid>13</realid><type>nucleus</type><status>In &lt;menu&gt; &amp; more</status>"
        b"<name>Bob</name><modified>2026-10-27 12:05:09</modified>"
        b"<entityModified>2026-10-27 12:30:00</entityModified></relationship></roster>"
    )
    # A change answers the relationship as it is now, which the game puts in its cache.
    assert relationship(asked) == (
        b'<success><relationship id="7"><new>true</new><state>2</state><tag>friends</tag>'
        b"<realid>12</realid><type>nucleus</type><status /><name>Invented_Ann</name>"
        b"<modified>2026-10-27 12:05:09</modified>"
        b"<entityModified>2026-10-27 12:05:09</entityModified></relationship></success>"
    )


def test_a_persona_search_names_each_player_with_its_namespace() -> None:
    # The game tells an account's display name from a hero by the namespace.
    found = [PersonaEntry(12, "Invented_Ann", "bfheroes_web"), PersonaEntry(12, "a&b", "bfwest")]

    assert personas([]) == b"<personas />"
    assert personas(found) == (
        b"<personas><persona><userId>12</userId><displayName>Invented_Ann</displayName>"
        b"<namespaceName>bfheroes_web</namespaceName></persona>"
        b"<persona><userId>12</userId><displayName>a&amp;b</displayName>"
        b"<namespaceName>bfwest</namespaceName></persona></personas>"
    )


def test_a_status_update_gives_its_line_as_both_versions_write_it() -> None:
    # 1.89 wraps the line in CDATA; 1.46 writes it bare, markup and all.
    line = "In <menu> & more"
    wrapped = f"<update>\n\t<status><![CDATA[{line}]]></status>\n</update>"
    assert status_update(wrapped.encode()) == line
    assert status_update(f"<update>\n\t<status>{line}</status>\n</update>".encode()) == line
    assert status_update(b"<update>\n\t<status><![CDATA[]]></status>\n</update>") == ""
    assert status_update("<update><status>★ Ω</status></update>".encode()) == "★ Ω"
    assert status_update(b"<update>\n</update>") is None
    assert status_update(b"<update>\n\t<status><![CDATA[In menu]]>\n</update>") is None
