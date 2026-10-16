from __future__ import annotations

from dataclasses import dataclass

from .errors import ProtocolError, UnsupportedStateError, ValidationError
from .models import (
    BatteryProtection,
    CompartmentState,
    CoolingMode,
    DeviceParameters,
    HistorySample,
    RefrigeratorState,
    TemperatureReading,
    TemperatureUnit,
)

START = 0x68
END = 0xEC
STATUS_ID = 0x0A
CONFIG_ID = 0x0B
HISTORY_ID = 0x0C
COMPARTMENT_ID = 0x0E

COOLING_MASK = 0x60
POWER_MASK = 0x08
UNIT_MASK = 0x04
PROTECTION_MASK = 0x03
UNKNOWN_MASK = 0x90

MIN_SETPOINT_C = -22
MAX_SETPOINT_C = 10


def additive_checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def validate_frame(frame: bytes) -> None:
    if len(frame) < 4:
        raise ProtocolError(f"frame too short: {len(frame)} bytes")
    if frame[0] != START:
        raise ProtocolError(f"invalid start byte 0x{frame[0]:02X}")
    if frame[-1] != END:
        raise ProtocolError(f"invalid terminator 0x{frame[-1]:02X}")
    expected = frame[1] + 2
    if len(frame) != expected:
        raise ProtocolError(f"length mismatch: field requires {expected}, got {len(frame)}")
    calculated = additive_checksum(frame[1:-2])
    if frame[-2] != calculated:
        raise ProtocolError(
            f"checksum mismatch: received 0x{frame[-2]:02X}, calculated 0x{calculated:02X}"
        )


class FrameReassembler:
    """Reassemble 0x68/LEN/.../0xEC frames from arbitrary BLE fragments."""

    def __init__(self, max_frame_bytes: int = 257):
        self._buffer = bytearray()
        self.max_frame_bytes = max_frame_bytes

    def clear(self) -> None:
        self._buffer.clear()

    def feed(self, fragment: bytes) -> list[bytes]:
        self._buffer.extend(fragment)
        frames: list[bytes] = []
        while self._buffer:
            if self._buffer[0] != START:
                try:
                    pos = self._buffer.index(START)
                except ValueError:
                    self._buffer.clear()
                    break
                del self._buffer[:pos]
            if len(self._buffer) < 2:
                break
            required = self._buffer[1] + 2
            if required < 4 or required > self.max_frame_bytes:
                del self._buffer[0]
                continue
            if len(self._buffer) < required:
                break
            candidate = bytes(self._buffer[:required])
            del self._buffer[:required]
            try:
                validate_frame(candidate)
            except ProtocolError:
                # Do not silently return invalid protocol frames.
                continue
            frames.append(candidate)
        return frames


def _require_code(code: bytes) -> None:
    if not isinstance(code, bytes) or len(code) != 3:
        raise ValidationError("communication_code must be exactly 3 bytes")


def _require_address(address: bytes) -> None:
    if not isinstance(address, bytes) or len(address) != 2:
        raise ValidationError("protocol_address must be exactly 2 bytes")


def build_status_query(communication_code: bytes) -> bytes:
    _require_code(communication_code)
    frame = bytearray([START, 0x09, 0xFF, 0xFF, 0xFF])
    frame.extend(communication_code)
    frame.append(STATUS_ID)
    frame.append(additive_checksum(frame[1:]))
    frame.append(END)
    result = bytes(frame)
    validate_frame(result)
    return result


def build_history_query(communication_code: bytes) -> bytes:
    _require_code(communication_code)
    frame = bytearray([START, 0x09, 0xFF, 0xFF, 0xFF])
    frame.extend(communication_code)
    frame.append(HISTORY_ID)
    frame.append(additive_checksum(frame[1:]))
    frame.append(END)
    result = bytes(frame)
    validate_frame(result)
    return result


def decode_temperature(raw: int) -> TemperatureReading:
    if raw == 0xE6:
        return TemperatureReading(raw, None, "Error6")
    if raw == 0xE7:
        return TemperatureReading(raw, None, "Error7")
    return TemperatureReading(raw, raw - 50)


def encode_setpoint(celsius: int) -> int:
    if not isinstance(celsius, int):
        raise ValidationError("setpoint must be an integer Celsius value")
    if not MIN_SETPOINT_C <= celsius <= MAX_SETPOINT_C:
        raise ValidationError(
            f"setpoint {celsius} C outside characterized range "
            f"{MIN_SETPOINT_C}..{MAX_SETPOINT_C} C"
        )
    return celsius + 50


def decode_device_parameters(raw: int) -> DeviceParameters:
    cooling_raw = raw & COOLING_MASK
    protection_raw = raw & PROTECTION_MASK
    if cooling_raw == 0x60:
        raise UnsupportedStateError("reserved cooling encoding 0b11 received")
    if protection_raw == 0x03:
        raise UnsupportedStateError("reserved battery-protection encoding 0b11 received")
    return DeviceParameters(
        raw=raw,
        cooling=CoolingMode(cooling_raw),
        power_on=bool(raw & POWER_MASK),
        unit=TemperatureUnit.FAHRENHEIT if raw & UNIT_MASK else TemperatureUnit.CELSIUS,
        battery_protection=BatteryProtection(protection_raw),
        unknown_bit7=bool(raw & 0x80),
        unknown_bit4=bool(raw & 0x10),
    )


def update_device_parameters(
    raw: int,
    *,
    cooling: CoolingMode | None = None,
    power_on: bool | None = None,
    unit: TemperatureUnit | None = None,
    battery_protection: BatteryProtection | None = None,
) -> int:
    # Validate current defined encodings while preserving unknown bits 7 and 4.
    decode_device_parameters(raw)
    value = raw
    if cooling is not None:
        cooling = CoolingMode(cooling)
        value = (value & ~COOLING_MASK) | int(cooling)
    if power_on is not None:
        value = value | POWER_MASK if power_on else value & ~POWER_MASK
    if unit is not None:
        unit = TemperatureUnit(unit)
        value = value | UNIT_MASK if unit == TemperatureUnit.FAHRENHEIT else value & ~UNIT_MASK
    if battery_protection is not None:
        battery_protection = BatteryProtection(battery_protection)
        value = (value & ~PROTECTION_MASK) | int(battery_protection)
    if (value & UNKNOWN_MASK) != (raw & UNKNOWN_MASK):
        raise ValidationError("unknown deviceParam bits would change")
    decode_device_parameters(value)
    return value


def parse_state(frame: bytes, *, allowed_message_ids=(STATUS_ID, CONFIG_ID)) -> RefrigeratorState:
    validate_frame(frame)
    if len(frame) != 19:
        raise ProtocolError(f"state frame must be 19 bytes, got {len(frame)}")
    if frame[8] not in allowed_message_ids:
        raise ProtocolError(f"unexpected state message id 0x{frame[8]:02X}")
    return RefrigeratorState(
        type_state=frame[2],
        protocol_address=frame[3:5],
        connection_code=frame[5:8],
        message_id=frame[8],
        actual_a=decode_temperature(frame[9]),
        actual_b=decode_temperature(frame[10]),
        setpoint_a_c=frame[11] - 50,
        setpoint_b_c=frame[12] - 50,
        voltage_major=frame[13],
        voltage_minor=frame[14],
        parameters=decode_device_parameters(frame[15]),
        device_state=frame[16],
        raw_frame=frame,
    )


def build_config_write(
    state: RefrigeratorState,
    communication_code: bytes,
    *,
    setpoint_a_c: int | None = None,
    setpoint_b_c: int | None = None,
    cooling: CoolingMode | None = None,
    power_on: bool | None = None,
    unit: TemperatureUnit | None = None,
    battery_protection: BatteryProtection | None = None,
) -> bytes:
    _require_code(communication_code)
    _require_address(state.protocol_address)
    a = state.setpoint_a_c if setpoint_a_c is None else setpoint_a_c
    b = state.setpoint_b_c if setpoint_b_c is None else setpoint_b_c
    param = update_device_parameters(
        state.parameters.raw,
        cooling=cooling,
        power_on=power_on,
        unit=unit,
        battery_protection=battery_protection,
    )
    frame = bytearray(19)
    frame[0] = START
    frame[1] = 0x11
    frame[2] = 0x03
    frame[3:5] = state.protocol_address
    frame[5:8] = communication_code
    frame[8] = CONFIG_ID
    frame[9] = 0
    frame[10] = 0
    frame[11] = encode_setpoint(a)
    frame[12] = encode_setpoint(b)
    frame[13] = 0
    frame[14] = 0
    frame[15] = param
    frame[16] = 0
    frame[17] = additive_checksum(frame[1:17])
    frame[18] = END
    result = bytes(frame)
    validate_frame(result)
    return result


def build_compartment_command(
    state: RefrigeratorState,
    communication_code: bytes,
    target: CompartmentState,
) -> bytes:
    _require_code(communication_code)
    if state.type_state not in (0, 1, 2):
        raise ValidationError("0x0E compartment control requires a dual-compartment state 0..2")
    target = CompartmentState(target)
    frame = bytearray(12)
    frame[0] = START
    frame[1] = 0x0A
    frame[2] = state.type_state
    frame[3:5] = state.protocol_address
    frame[5:8] = communication_code
    frame[8] = COMPARTMENT_ID
    frame[9] = int(target)
    frame[10] = additive_checksum(frame[1:10])
    frame[11] = END
    result = bytes(frame)
    validate_frame(result)
    return result


def parse_history_frame(frame: bytes) -> tuple[int, list[HistorySample]]:
    validate_frame(frame)
    if len(frame) != 252 or frame[1] != 0xFA or frame[8] != HISTORY_ID:
        raise ProtocolError("not a valid 252-byte 0x0C history frame")
    seq = frame[9]
    if not 1 <= seq <= 48:
        raise ProtocolError(f"history sequence out of range: {seq}")
    payload = frame[10:250]
    if len(payload) != 240:
        raise ProtocolError("history payload must be exactly 240 bytes")
    samples = []
    base_index = (seq - 1) * 60
    for record in range(60):
        off = record * 4
        samples.append(
            HistorySample(
                index=base_index + record,
                temp_a_c=payload[off] - 50,
                temp_b_c=payload[off + 1] - 50,
                voltage_major=payload[off + 2],
                voltage_minor=payload[off + 3],
            )
        )
    return seq, samples
