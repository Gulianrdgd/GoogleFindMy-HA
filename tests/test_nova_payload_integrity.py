# tests/test_nova_payload_integrity.py
"""Pre-send integrity guard for Nova ExecuteAction payloads.

Context: spurious Play Sound rings (BSkando#211 / #222). A locate action
(field 30, tag 0xF2 0x01) and a start-sound action (field 31, tag 0xFA 0x01)
differ by one bit on the wire. These tests pin that a locate payload can never
leave the integration carrying ``startSound``, and that oversized timestamps or
identifiers cannot bleed into the action field.
"""

from __future__ import annotations

import binascii
from typing import Any

import pytest

from custom_components.googlefindmy.NovaApi.ExecuteAction.LocateTracker.location_request import (
    create_location_request,
)
from custom_components.googlefindmy.NovaApi.ExecuteAction.PlaySound.sound_request import (
    create_sound_request,
)
from custom_components.googlefindmy.NovaApi.nova_request import (
    NovaPayloadIntegrityError,
    _assert_payload_action,
    async_nova_request,
)
from custom_components.googlefindmy.ProtoDecoders import DeviceUpdate_pb2
from tests.test_nova_request import _DummyResponse, _DummySession, _StubCache

_LOCATE_TAG = b"\xf2\x01"
_START_SOUND_TAG = b"\xfa\x01"


def _locate_bytes(**kwargs: Any) -> bytes:
    return binascii.unhexlify(
        create_location_request("device-1", "fcm-token", "req-1", **kwargs)
    )


def _flip_action_bit(payload: bytes) -> bytes:
    """Flip the single bit that turns the locate tag into the start-sound tag."""
    assert payload.count(_LOCATE_TAG) == 1
    return payload.replace(_LOCATE_TAG, _START_SOUND_TAG)


def _actions(payload: bytes) -> list[str]:
    request = DeviceUpdate_pb2.ExecuteActionRequest()
    request.ParseFromString(payload)
    return [
        f
        for f in ("locateTracker", "startSound", "stopSound")
        if request.action.HasField(f)
    ]


def test_a_genuine_locate_payload_passes() -> None:
    _assert_payload_action(_locate_bytes(), "locateTracker")


def test_one_flipped_bit_turns_a_locate_into_a_ring_and_is_refused() -> None:
    """The hypothesis under test, made concrete: one bit is enough, and it is caught."""
    flipped = _flip_action_bit(_locate_bytes())
    assert _actions(flipped) == ["startSound"]
    with pytest.raises(NovaPayloadIntegrityError):
        _assert_payload_action(flipped, "locateTracker")


def test_a_play_sound_payload_is_refused_on_the_locate_path() -> None:
    play = binascii.unhexlify(create_sound_request(True, "device-1", "fcm-token"))
    with pytest.raises(NovaPayloadIntegrityError):
        _assert_payload_action(play, "locateTracker")


def test_undecodable_bytes_are_refused() -> None:
    with pytest.raises(NovaPayloadIntegrityError):
        _assert_payload_action(b"\xff\xff\xff", "locateTracker")


@pytest.mark.parametrize("seconds", [2**31 - 1, 2**31, 2**32 - 1])
def test_a_large_timestamp_stays_inside_the_locate_action(seconds: int) -> None:
    """Values past the signed 32-bit limit only lengthen the locate body."""
    payload = _locate_bytes(last_mode_switch=seconds)
    assert _actions(payload) == ["locateTracker"]
    assert payload.count(_START_SOUND_TAG) == 0


def test_an_overflowing_timestamp_raises_instead_of_wrapping() -> None:
    """``Time.seconds`` is uint32; protobuf refuses 2**32 rather than wrapping."""
    with pytest.raises(ValueError):
        _locate_bytes(last_mode_switch=2**32)


def test_long_identifiers_stay_inside_their_own_fields() -> None:
    payload = binascii.unhexlify(
        create_location_request("d" * 5000, "f" * 5000, "r" * 5000)
    )
    assert _actions(payload) == ["locateTracker"]


@pytest.mark.asyncio
async def test_async_nova_request_sends_nothing_when_the_action_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard sits in front of ``session.post``: a flipped payload never leaves."""
    session = _DummySession([_DummyResponse(200, b"\x10")])

    async def _fake_get_adm_token(*_args: Any, **_kwargs: Any) -> str:
        return "resolved-token"

    monkeypatch.setattr(
        "custom_components.googlefindmy.NovaApi.nova_request.async_get_adm_token_api",
        _fake_get_adm_token,
    )

    flipped_hex = binascii.hexlify(_flip_action_bit(_locate_bytes())).decode()
    with pytest.raises(NovaPayloadIntegrityError):
        await async_nova_request(
            "nbe_execute_action",
            flipped_hex,
            username="user@example.com",
            cache=_StubCache(),
            session=session,
            expected_action="locateTracker",
        )
    assert session.calls == []


@pytest.mark.asyncio
async def test_async_nova_request_sends_a_genuine_locate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _DummySession([_DummyResponse(200, b"\x10")])

    async def _fake_get_adm_token(*_args: Any, **_kwargs: Any) -> str:
        return "resolved-token"

    monkeypatch.setattr(
        "custom_components.googlefindmy.NovaApi.nova_request.async_get_adm_token_api",
        _fake_get_adm_token,
    )

    await async_nova_request(
        "nbe_execute_action",
        binascii.hexlify(_locate_bytes()).decode(),
        username="user@example.com",
        cache=_StubCache(),
        session=session,
        expected_action="locateTracker",
    )
    assert len(session.calls) == 1
