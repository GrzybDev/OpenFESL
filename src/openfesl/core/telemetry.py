import base64
import re
from typing import Any

from openfesl.core.fesl import FeslConnection, Route
from openfesl.core.registration import countries
from openfesl.protocol.acct import GetTelemetryTokenAnswer
from openfesl.protocol.message import Message

# An address no connection can reach, so telemetry never leaves the player's PC.
NOWHERE = "0.0.0.0"


def telemetry_routes() -> dict[tuple[str, str], Route[Any]]:
    """Return the handler of GetTelemetryToken, which keeps the game's telemetry switched off."""

    async def token(connection: FeslConnection, request: Message) -> GetTelemetryTokenAnswer:
        assert connection.hello is not None
        # The SDK splits the token at commas and takes the country from the locale's end.
        locale = re.sub("[^A-Za-z]", "", connection.hello.locale)[:4]
        return GetTelemetryTokenAnswer(
            telemetry_token=base64.b64encode(f"{NOWHERE},0,{locale},".encode()).decode(),
            enabled="",
            disabled=",".join(code for code, _ in countries("en")),
        )

    return {("acct", "GetTelemetryToken"): Route(Message, token)}
