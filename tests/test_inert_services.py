import base64

import pytest
from support import game, log_in_player

from openfesl.protocol.relay import Channel, ConnectionKey

pytestmark = pytest.mark.anyio

FESL = ConnectionKey(Channel.FESL, 1)


async def test_telemetry_ping_sites_foreign_logins_and_unserved_services_lead_nowhere(
    server_url: str,
) -> None:
    async with game(server_url) as player:
        await log_in_player(player, FESL, "ann@example.test")

        telemetry = await player.request(FESL, "acct", [("TXN", "GetTelemetryToken")])
        address, port, locale, rest = base64.b64decode(telemetry["telemetryToken"]).split(b",")
        assert (address, port, locale, rest) == (b"0.0.0.0", b"0", b"enUS", b"")
        assert telemetry["enabled"] == ""
        assert {"US", "PL"} <= set(telemetry["disabled"].split(","))

        sites = await player.request(FESL, "fsys", [("TXN", "GetPingSites")])
        assert (sites["pingSite.[]"], sites["minPingSitesToPing"]) == ("0", "0")

        for txn, code in [
            ("NuPS3Login", "101"),
            ("NuXBL360Login", "101"),
            ("NuPS3AddAccount", "21"),
            ("GameSpyPreAuth", "21"),
            ("NuCreateEncryptedToken", "21"),
        ]:
            assert (await player.request(FESL, "acct", [("TXN", txn)]))["errorCode"] == code
        assert "errorCode" not in await player.request(FESL, "fsys", [("TXN", "Suicide")])
        # The SDK turns error 99 into each service's own "service unavailable".
        for command, txn in [
            ("gsum", "GetSessionId"),
            ("gsum", "StartReport"),
            ("fltr", "CheckProfanity"),
            ("fdbk", "SendFeedback"),
            ("fpla", "EnterLocation"),
            ("blob", "ListBlobInfo"),
            ("chnk", "GetNextChunk"),
        ]:
            answer = await player.request(FESL, command, [("TXN", txn)])
            assert (answer["TXN"], answer["errorCode"]) == (txn, "99")
        assert "nuid" in await player.request(FESL, "acct", [("TXN", "NuGetAccount")])
