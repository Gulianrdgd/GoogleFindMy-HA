# tests/test_location_request_high_traffic_time.py
from __future__ import annotations

import binascii

from custom_components.googlefindmy.NovaApi.ExecuteAction.LocateTracker.location_request import (
    REFERENCE_LAST_MODE_SWITCH,
    create_location_request,
)
from custom_components.googlefindmy.ProtoDecoders import DeviceUpdate_pb2


def _enabling_time(hex_payload: str) -> int:
    request = DeviceUpdate_pb2.ExecuteActionRequest()
    request.ParseFromString(binascii.unhexlify(hex_payload))
    return request.action.locateTracker.lastHighTrafficEnablingTime.seconds


def test_unknown_mode_switch_sends_the_reference_timestamp() -> None:
    """Without a known switch time, send what GoogleFindMyTools sends.

    The payload used to carry 0 here, which no other client sends.
    """

    payload = create_location_request("device-1", "fcm-token", "req-1")

    assert _enabling_time(payload) == REFERENCE_LAST_MODE_SWITCH
    assert REFERENCE_LAST_MODE_SWITCH == 1732120060


def test_known_mode_switch_is_sent_verbatim() -> None:
    """A real contributor-mode switch time still wins over the fallback."""

    payload = create_location_request(
        "device-1", "fcm-token", "req-1", last_mode_switch=1_700_000_000
    )

    assert _enabling_time(payload) == 1_700_000_000
