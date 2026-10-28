from openfesl.protocol.magma import error, success, token


def test_magma_documents_are_the_bytes_the_game_parses() -> None:
    # The game accepts a <success> root, reads the <token> child's text, and logs an <error>
    # root's code with its failure's cause.
    assert success() == b"<success />"
    assert token("a<&>b") == b"<success><token>a&lt;&amp;&gt;b</token></success>"
    assert error("NOT_LOGGED_IN", 'NO "SESSION"') == (
        b'<error code="NOT_LOGGED_IN"><failure cause="NO &quot;SESSION&quot;" /></error>'
    )
