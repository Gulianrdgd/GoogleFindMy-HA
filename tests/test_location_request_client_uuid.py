# tests/test_location_request_client_uuid.py
from __future__ import annotations

import binascii

from custom_components.googlefindmy.NovaApi.ExecuteAction import nbe_execute_action
from custom_components.googlefindmy.NovaApi.ExecuteAction.LocateTracker.location_request import (
    create_location_request,
)
from custom_components.googlefindmy.ProtoDecoders import DeviceUpdate_pb2


def _client_uuid(hex_payload: str) -> str:
    request = DeviceUpdate_pb2.ExecuteActionRequest()
    request.ParseFromString(binascii.unhexlify(hex_payload))
    return request.requestMetadata.fmdClientUuid


def test_location_requests_use_fresh_client_uuid() -> None:
    """Each locate request carries its own fmdClientUuid.

    Location requests must not reuse the process-wide client UUID that
    Play/Stop Sound requests share.
    """

    first = _client_uuid(create_location_request("device-1", "fcm-token", "req-1"))
    second = _client_uuid(create_location_request("device-1", "fcm-token", "req-2"))
    shared = nbe_execute_action._get_client_uuid()

    assert first
    assert second
    assert first != second
    assert shared not in (first, second)
