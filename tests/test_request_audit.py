# tests/test_request_audit.py
"""Audit trail for sent ExecuteAction requests and unsolicited FCM pushes."""

from __future__ import annotations

import binascii
import logging
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from custom_components.googlefindmy.NovaApi import request_audit
from custom_components.googlefindmy.NovaApi.ExecuteAction.LocateTracker.location_request import (
    create_location_request,
)
from custom_components.googlefindmy.NovaApi.ExecuteAction.PlaySound.sound_request import (
    create_sound_request,
)
from custom_components.googlefindmy.ProtoDecoders import DeviceUpdate_pb2
from custom_components.googlefindmy.services import _log_play_sound_origin

_LOGGER_NAME = request_audit.__name__


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    request_audit.reset_for_tests()
    yield
    request_audit.reset_for_tests()


def _push_hex(request_uuid: str, *, extra_field: bool = False) -> str:
    update = DeviceUpdate_pb2.DeviceUpdate()
    update.fcmMetadata.requestUuid = request_uuid
    raw = update.SerializeToString()
    if extra_field:
        raw += b"\x48\x01"  # field 9, varint 1: not modelled by DeviceUpdate
    return binascii.hexlify(raw).decode()


def _records(caplog: pytest.LogCaptureFixture, text: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if text in r.getMessage()]


def test_a_locate_send_is_recorded_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    payload = binascii.unhexlify(create_location_request("device-1", "fcm", "req-L"))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        request_audit.record_sent(payload)
    (record,) = _records(caplog, "Nova send")
    assert record.levelno == logging.DEBUG
    assert "action=locateTracker" in record.getMessage()
    assert request_audit.sent_action_for("req-L") == "locateTracker"


def test_a_sound_send_is_recorded_at_info(caplog: pytest.LogCaptureFixture) -> None:
    payload = binascii.unhexlify(create_sound_request(True, "device-1", "fcm", "req-S"))
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        request_audit.record_sent(payload)
    (record,) = _records(caplog, "Nova send")
    assert record.levelno == logging.INFO
    assert "action=startSound" in record.getMessage()


def test_a_push_answering_our_request_stays_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_audit.record_sent(
        binascii.unhexlify(create_location_request("device-1", "fcm", "req-ours"))
    )
    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        request_audit.note_unsolicited_push("device-1", _push_hex("req-ours"))
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    assert _records(caplog, "answers our locateTracker request")


def test_a_foreign_push_is_logged_once_then_rate_limited(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(request_audit.time, "monotonic", lambda: clock[0])

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        request_audit.note_unsolicited_push("device-1", _push_hex("foreign-1"))
        clock[0] += 60
        request_audit.note_unsolicited_push("device-1", _push_hex("foreign-2"))
        clock[0] += request_audit.UNSOLICITED_LOG_INTERVAL_S
        request_audit.note_unsolicited_push("device-1", _push_hex("foreign-3"))

    info = [
        r for r in _records(caplog, "Unsolicited FCM push") if r.levelno == logging.INFO
    ]
    assert len(info) == 2
    assert "earlier_suppressed=0" in info[0].getMessage()
    assert "earlier_suppressed=1" in info[1].getMessage()


def test_unmodelled_push_fields_are_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        request_audit.note_unsolicited_push(
            "device-1", _push_hex("foreign", extra_field=True)
        )
    (record,) = _records(caplog, "Unsolicited FCM push")
    assert "extra_fields=[9]" in record.getMessage()


def test_a_garbled_push_does_not_raise() -> None:
    request_audit.note_unsolicited_push("device-1", "ffff")
    assert request_audit._top_level_field_numbers(b"\xff\xff") == []


@pytest.mark.parametrize(
    ("context", "source"),
    [
        (SimpleNamespace(user_id="user-123456", parent_id=None, id="ctx-1"), "user"),
        (
            SimpleNamespace(user_id=None, parent_id="auto-99", id="ctx-2"),
            "automation_or_script",
        ),
        (SimpleNamespace(user_id=None, parent_id=None, id="ctx-3"), "system"),
    ],
)
def test_play_sound_origin_is_classified(
    caplog: pytest.LogCaptureFixture, context: SimpleNamespace, source: str
) -> None:
    call = SimpleNamespace(context=context)
    with caplog.at_level(
        logging.INFO, logger="custom_components.googlefindmy.services"
    ):
        _log_play_sound_origin(call, "device-abcdef1234")  # type: ignore[arg-type]
    (record,) = _records(caplog, "Play Sound requested")
    assert f"source={source}" in record.getMessage()
    assert record.levelno == logging.INFO
