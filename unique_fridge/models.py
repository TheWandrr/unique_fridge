from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum


class CoolingMode(IntEnum):
    ECON = 0x00
    NORM = 0x20
    QUICK = 0x40


class BatteryProtection(IntEnum):
    LOW = 0
    MID = 1
    HIGH = 2


class TemperatureUnit(IntEnum):
    CELSIUS = 0
    FAHRENHEIT = 1


class CompartmentState(IntEnum):
    BOTH_ACTIVE = 0
    CHANNEL_B_DISABLED = 1
    CHANNEL_A_DISABLED = 2


@dataclass(frozen=True)
class DeviceParameters:
    raw: int
    cooling: CoolingMode
    power_on: bool
    unit: TemperatureUnit
    battery_protection: BatteryProtection
    unknown_bit7: bool
    unknown_bit4: bool


@dataclass(frozen=True)
class TemperatureReading:
    raw: int
    celsius: int | None
    error: str | None = None

    @property
    def fahrenheit(self) -> float | None:
        return None if self.celsius is None else self.celsius * 1.8 + 32


@dataclass(frozen=True)
class RefrigeratorState:
    type_state: int
    protocol_address: bytes
    connection_code: bytes
    message_id: int
    actual_a: TemperatureReading
    actual_b: TemperatureReading
    setpoint_a_c: int
    setpoint_b_c: int
    voltage_major: int
    voltage_minor: int
    parameters: DeviceParameters
    device_state: int
    raw_frame: bytes

    @property
    def voltage(self) -> float:
        # Minor is a decimal suffix, e.g. 12 and 28 => 12.28.
        return float(f"{self.voltage_major}.{self.voltage_minor:02d}")

    @property
    def is_single_compartment(self) -> bool:
        return self.type_state == 3

    @property
    def compartment_state(self) -> CompartmentState | None:
        if self.type_state in (0, 1, 2):
            return CompartmentState(self.type_state)
        return None


@dataclass(frozen=True)
class HistorySample:
    index: int
    temp_a_c: int
    temp_b_c: int
    voltage_major: int
    voltage_minor: int

    @property
    def voltage(self) -> float:
        return float(f"{self.voltage_major}.{self.voltage_minor:02d}")


@dataclass(frozen=True)
class HistoryData:
    samples: tuple[HistorySample, ...]
    frame_count: int
    retrieved_at: datetime

    @property
    def complete(self) -> bool:
        return self.frame_count == 48 and len(self.samples) == 2880

    @property
    def sample_interval_seconds(self) -> int:
        return 30

    @property
    def span_hours(self) -> int:
        return 24
