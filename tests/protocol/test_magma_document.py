from datetime import UTC, datetime, timedelta, timezone

from openfesl.protocol.magma import EntitlementEntry, entitlements, error, products, success, token


def test_magma_documents_are_the_bytes_the_game_parses() -> None:
    # The game accepts a <success> root, reads the <token> child's text, and logs an <error>
    # root's code with its failure's cause.
    assert success() == b"<success />"
    assert token("a<&>b") == b"<success><token>a&lt;&amp;&gt;b</token></success>"
    assert error("NOT_LOGGED_IN", 'NO "SESSION"') == (
        b'<error code="NOT_LOGGED_IN"><failure cause="NO &quot;SESSION&quot;" /></error>'
    )
    assert error("VALIDATION_FAILED", "NOT_NUMERIC", "userId") == (
        b'<error code="VALIDATION_FAILED"><failure cause="NOT_NUMERIC" field="userId" /></error>'
    )
    assert products() == b"<products />"


def test_an_entitlement_list_gives_dates_to_the_minute_in_utc() -> None:
    # The game parses dates as %d-%d-%dT%d:%dZ and an empty end as none.
    granted = datetime(2026, 10, 20, 12, 0, 59, tzinfo=UTC)
    ends = datetime(2026, 11, 3, 18, 30, tzinfo=timezone(timedelta(hours=1)))
    document = entitlements(
        [
            EntitlementEntry(7, "2005-lmct", 3, granted, None, "ACTIVE"),
            EntitlementEntry(8, "3002-tlmd", 0, granted, ends, "DISABLED"),
        ]
    )

    assert document == (
        b"<entitlements>"
        b"<entitlement><entitlementId>7</entitlementId><entitlementTag>2005-lmct</entitlementTag>"
        b"<useCount>3</useCount><grantDate>2026-10-20T12:00Z</grantDate><terminationDate />"
        b"<status>ACTIVE</status></entitlement>"
        b"<entitlement><entitlementId>8</entitlementId><entitlementTag>3002-tlmd</entitlementTag>"
        b"<useCount>0</useCount><grantDate>2026-10-20T12:00Z</grantDate>"
        b"<terminationDate>2026-11-03T17:30Z</terminationDate>"
        b"<status>DISABLED</status></entitlement>"
        b"</entitlements>"
    )
