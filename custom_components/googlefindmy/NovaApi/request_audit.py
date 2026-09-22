# custom_components/googlefindmy/NovaApi/request_audit.py
"""Audit trail of sent ExecuteAction requests and unsolicited FCM pushes.

Investigation aid for spurious Play Sound rings (BSkando#211 / #222). Two
questions are answered from the log at the moment of a ring:

1. Did this integration send a sound action? Every ExecuteAction request is
   recorded with its action and request UUID. Locate requests log at DEBUG (they run every poll cycle); sound
   actions log at INFO (they are rare).
2. Did Google push something nobody here asked for? A push whose request
   UUID was not sent by this process is logged at INFO, at most once per
   device per ``UNSOLICITED_LOG_INTERVAL_S``; the rest go to DEBUG.

Raw payloads are never logged (AGENTS.md section 5): only action names,
truncated identifiers and field numbers.
"""

from __future__ import annotations

import binascii
import logging
import time
from collections import OrderedDict
from importlib import import_module
from typing import Any

_LOGGER = logging.getLogger(__name__)

ACTION_FIELDS = ("locateTracker", "startSound", "stopSound")
SENT_MEMORY = 512
UNSOLICITED_LOG_INTERVAL_S = 600.0

# DeviceUpdate top-level fields modelled in DeviceUpdate.proto.
_KNOWN_PUSH_FIELDS = frozenset({1, 2, 3})

# Protobuf wire types (encoding spec) and the varint length limit.
_WIRE_VARINT, _WIRE_I64, _WIRE_LEN, _WIRE_I32 = 0, 1, 2, 5
_VARINT_MAX_SHIFT = 64

_sent: OrderedDict[str, str] = OrderedDict()
_last_unsolicited_log: dict[str, float] = {}
_suppressed: dict[str, int] = {}


def _proto() -> Any:
    return import_module(
        "custom_components.googlefindmy.ProtoDecoders.DeviceUpdate_pb2"
    )


def _top_level_field_numbers(buf: bytes) -> list[int]:
    """Return the field numbers of a serialized message, schema-free.

    Stops quietly at the first malformed tag; the result then lists what was
    readable, which is all a diagnostic needs.
    """
    numbers: list[int] = []
    pos, end = 0, len(buf)

    def _varint() -> int | None:
        nonlocal pos
        shift = value = 0
        while pos < end and shift < _VARINT_MAX_SHIFT:
            byte = buf[pos]
            pos += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
        return None

    while pos < end:
        key = _varint()
        if key is None:
            break
        number, wire_type = key >> 3, key & 0x07
        if wire_type == _WIRE_VARINT:
            if _varint() is None:
                break
        elif wire_type == _WIRE_I64:
            pos += 8
        elif wire_type == _WIRE_LEN:
            length = _varint()
            if length is None:
                break
            pos += length
        elif wire_type == _WIRE_I32:
            pos += 4
        else:
            break
        if pos > end:
            break
        numbers.append(number)
    return numbers


def record_sent(payload: bytes) -> None:
    """Record one ExecuteAction request right before it is handed to the transport."""
    request = _proto().ExecuteActionRequest()
    try:
        request.ParseFromString(payload)
    except Exception:  # noqa: BLE001 - the integrity guard reports decode failures
        return
    actions = [f for f in ACTION_FIELDS if request.action.HasField(f)]
    action = ",".join(actions) or "none"
    request_uuid = request.requestMetadata.requestUuid
    if request_uuid:
        _sent[request_uuid] = action
        _sent.move_to_end(request_uuid)
        while len(_sent) > SENT_MEMORY:
            _sent.popitem(last=False)
    level = logging.DEBUG if actions == ["locateTracker"] else logging.INFO
    _LOGGER.log(
        level,
        "Nova send: action=%s device=%s request=%s",
        action,
        request.scope.device.canonicId.id[:8],
        request_uuid[:8] or "none",
    )


def sent_action_for(request_uuid: str) -> str | None:
    """Return the action this process sent under ``request_uuid``, if any."""
    return _sent.get(request_uuid) if request_uuid else None


def note_unsolicited_push(canonic_id: str, hex_payload: str) -> None:
    """Describe an FCM push that no pending request was waiting for."""
    try:
        raw = binascii.unhexlify(hex_payload)
        update = _proto().DeviceUpdate()
        update.ParseFromString(raw)
    except Exception:  # noqa: BLE001 - diagnostics must never break routing
        _LOGGER.debug("Unsolicited FCM push for %s does not decode", canonic_id[:8])
        return

    request_uuid = update.fcmMetadata.requestUuid
    ours = sent_action_for(request_uuid)
    has_location = update.deviceMetadata.information.HasField("locationInformation")
    extra = sorted(set(_top_level_field_numbers(raw)) - _KNOWN_PUSH_FIELDS)

    if ours is not None:
        _LOGGER.debug(
            "FCM push for %s answers our %s request %s",
            canonic_id[:8],
            ours,
            request_uuid[:8],
        )
        return

    device = canonic_id[:8]
    now = time.monotonic()
    last = _last_unsolicited_log.get(device)
    if last is not None and now - last < UNSOLICITED_LOG_INTERVAL_S:
        _suppressed[device] = _suppressed.get(device, 0) + 1
        _LOGGER.debug(
            "Unsolicited FCM push for %s (request=%s, location=%s, extra_fields=%s)",
            device,
            request_uuid[:8] or "none",
            "yes" if has_location else "no",
            extra,
        )
        return
    _last_unsolicited_log[device] = now
    suppressed = _suppressed.pop(device, 0)
    _LOGGER.info(
        "Unsolicited FCM push for %s: request=%s was not sent by this integration; "
        "location=%s, extra_fields=%s, earlier_suppressed=%d",
        device,
        request_uuid[:8] or "none",
        "yes" if has_location else "no",
        extra,
        suppressed,
    )


def reset_for_tests() -> None:
    """Clear module state (tests only)."""
    _sent.clear()
    _last_unsolicited_log.clear()
    _suppressed.clear()
