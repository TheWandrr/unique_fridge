from __future__ import annotations

import asyncio
from datetime import datetime

from bleak import BleakClient, BleakScanner

from .errors import CommunicationError, ProtocolError, ValidationError
from .models import (
    BatteryProtection,
    CompartmentState,
    CoolingMode,
    HistoryData,
    RefrigeratorState,
    TemperatureUnit,
)
from .protocol import (
    CONFIG_ID,
    HISTORY_ID,
    STATUS_ID,
    FrameReassembler,
    build_compartment_command,
    build_config_write,
    build_history_query,
    build_status_query,
    parse_history_frame,
    parse_state,
)

SERVICE_UUID = "00001000-0000-1000-8000-00805f9b34fb"
RX_UUID = "00001001-0000-1000-8000-00805f9b34fb"
TX_UUID = "00001002-0000-1000-8000-00805f9b34fb"


class RefrigeratorClient:
    """Async, validated client for the characterized refrigerator protocol."""

    def __init__(
        self,
        ble_address: str,
        communication_code: bytes,
        *,
        scan_timeout: float = 15.0,
        response_timeout: float = 8.0,
    ):
        if len(communication_code) != 3:
            raise ValidationError("communication_code must be exactly 3 bytes")
        self.ble_address = ble_address
        self.communication_code = bytes(communication_code)
        self.scan_timeout = scan_timeout
        self.response_timeout = response_timeout
        self._client: BleakClient | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._reassembler = FrameReassembler()
        self._transaction_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def connect(self) -> None:
        if self.connected:
            return
        device = await BleakScanner.find_device_by_address(
            self.ble_address, timeout=self.scan_timeout
        )
        if device is None:
            raise CommunicationError(f"BLE device {self.ble_address} not found")
        self._client = BleakClient(device, timeout=self.scan_timeout)
        await self._client.connect()
        if not self._client.is_connected:
            raise CommunicationError("BLE connection did not become active")
        service = self._client.services.get_service(SERVICE_UUID)
        if service is None:
            await self.disconnect()
            raise CommunicationError(f"required service {SERVICE_UUID} not found")
        rx = service.get_characteristic(RX_UUID)
        tx = service.get_characteristic(TX_UUID)
        if rx is None or tx is None:
            await self.disconnect()
            raise CommunicationError("required RX/TX characteristics not found")
        if not ({"write", "write-without-response"} & set(rx.properties)):
            await self.disconnect()
            raise CommunicationError("RX characteristic has no supported write property")
        if "notify" not in set(tx.properties):
            await self.disconnect()
            raise CommunicationError("TX characteristic does not support notifications")
        await self._client.start_notify(TX_UUID, self._on_notification)
        await asyncio.sleep(0.2)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            if client.is_connected:
                try:
                    await client.stop_notify(TX_UUID)
                except Exception:
                    pass
                await client.disconnect()
        finally:
            self._clear_receive_state()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.disconnect()

    def _on_notification(self, _sender, data: bytearray) -> None:
        for frame in self._reassembler.feed(bytes(data)):
            self._queue.put_nowait(frame)

    def _clear_receive_state(self) -> None:
        self._reassembler.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _require_client(self) -> BleakClient:
        if not self._client or not self._client.is_connected:
            raise CommunicationError("client is not connected")
        return self._client

    async def _write(self, frame: bytes) -> None:
        client = self._require_client()
        try:
            await client.write_gatt_char(RX_UUID, frame, response=False)
        except Exception as exc:
            raise CommunicationError(f"BLE write failed: {exc}") from exc

    async def _wait_for(self, predicate, *, timeout: float | None = None) -> bytes:
        timeout = self.response_timeout if timeout is None else timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise CommunicationError(f"response timeout after {timeout:g} seconds")
            try:
                frame = await asyncio.wait_for(self._queue.get(), remaining)
            except asyncio.TimeoutError as exc:
                raise CommunicationError(
                    f"response timeout after {timeout:g} seconds"
                ) from exc
            if predicate(frame):
                return frame

    async def read_state(self) -> RefrigeratorState:
        async with self._transaction_lock:
            self._clear_receive_state()
            await self._write(build_status_query(self.communication_code))
            frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == STATUS_ID
            )
            return parse_state(frame, allowed_message_ids=(STATUS_ID,))

    async def _config_transaction(self, builder) -> RefrigeratorState:
        """Fresh read -> one safe 0x0B write -> acknowledgement -> independent readback."""
        async with self._transaction_lock:
            # Fresh state under the same lock prevents library-side concurrent transactions.
            self._clear_receive_state()
            await self._write(build_status_query(self.communication_code))
            source_frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == STATUS_ID
            )
            before = parse_state(source_frame, allowed_message_ids=(STATUS_ID,))

            command, validator = builder(before)
            self._clear_receive_state()
            await self._write(command)
            ack_frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == CONFIG_ID
            )
            acknowledgement = parse_state(ack_frame, allowed_message_ids=(CONFIG_ID,))
            validator(before, acknowledgement, "0x0B acknowledgement")

            self._clear_receive_state()
            await self._write(build_status_query(self.communication_code))
            readback_frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == STATUS_ID
            )
            readback = parse_state(readback_frame, allowed_message_ids=(STATUS_ID,))
            validator(before, readback, "independent readback")
            return readback

    @staticmethod
    def _validate_protected(before, after, label, *, allow_a=False, allow_b=False, allow_param=False):
        if after.protocol_address != before.protocol_address:
            raise ProtocolError(f"{label}: protocol address changed")
        if not allow_a and after.setpoint_a_c != before.setpoint_a_c:
            raise ProtocolError(f"{label}: setpoint A changed unexpectedly")
        if not allow_b and after.setpoint_b_c != before.setpoint_b_c:
            raise ProtocolError(f"{label}: setpoint B changed unexpectedly")
        if not allow_param and after.parameters.raw != before.parameters.raw:
            raise ProtocolError(f"{label}: deviceParam changed unexpectedly")

    async def set_setpoint_a(self, celsius: int) -> RefrigeratorState:
        def builder(before):
            command = build_config_write(
                before, self.communication_code, setpoint_a_c=celsius
            )
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_a=True)
                if a.setpoint_a_c != celsius:
                    raise ProtocolError(f"{label}: setpoint A did not become {celsius} C")
            return command, check
        return await self._config_transaction(builder)

    async def set_setpoint_b(self, celsius: int) -> RefrigeratorState:
        def builder(before):
            command = build_config_write(
                before, self.communication_code, setpoint_b_c=celsius
            )
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_b=True)
                if a.setpoint_b_c != celsius:
                    raise ProtocolError(f"{label}: setpoint B did not become {celsius} C")
            return command, check
        return await self._config_transaction(builder)

    async def set_cooling(self, mode: CoolingMode) -> RefrigeratorState:
        mode = CoolingMode(mode)
        def builder(before):
            command = build_config_write(before, self.communication_code, cooling=mode)
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_param=True)
                if a.parameters.cooling != mode:
                    raise ProtocolError(f"{label}: cooling mode mismatch")
                if (a.parameters.raw & ~0x60) != (b.parameters.raw & ~0x60):
                    raise ProtocolError(f"{label}: non-cooling deviceParam bits changed")
            return command, check
        return await self._config_transaction(builder)

    async def set_power(self, on: bool) -> RefrigeratorState:
        def builder(before):
            command = build_config_write(before, self.communication_code, power_on=on)
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_param=True)
                if a.parameters.power_on != on:
                    raise ProtocolError(f"{label}: power state mismatch")
                if (a.parameters.raw & ~0x08) != (b.parameters.raw & ~0x08):
                    raise ProtocolError(f"{label}: non-power deviceParam bits changed")
            return command, check
        return await self._config_transaction(builder)

    async def set_unit(self, unit: TemperatureUnit) -> RefrigeratorState:
        unit = TemperatureUnit(unit)
        def builder(before):
            command = build_config_write(before, self.communication_code, unit=unit)
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_param=True)
                if a.parameters.unit != unit:
                    raise ProtocolError(f"{label}: unit mismatch")
                if (a.parameters.raw & ~0x04) != (b.parameters.raw & ~0x04):
                    raise ProtocolError(f"{label}: non-unit deviceParam bits changed")
            return command, check
        return await self._config_transaction(builder)

    async def set_battery_protection(
        self, protection: BatteryProtection
    ) -> RefrigeratorState:
        protection = BatteryProtection(protection)
        def builder(before):
            command = build_config_write(
                before, self.communication_code, battery_protection=protection
            )
            def check(b, a, label):
                self._validate_protected(b, a, label, allow_param=True)
                if a.parameters.battery_protection != protection:
                    raise ProtocolError(f"{label}: battery protection mismatch")
                if (a.parameters.raw & ~0x03) != (b.parameters.raw & ~0x03):
                    raise ProtocolError(f"{label}: non-protection deviceParam bits changed")
            return command, check
        return await self._config_transaction(builder)

    async def set_compartment_state(
        self,
        target: CompartmentState,
        *,
        settle_seconds: float = 2.0,
    ) -> RefrigeratorState:
        target = CompartmentState(target)
        if settle_seconds < 0:
            raise ValidationError("settle_seconds cannot be negative")
        async with self._transaction_lock:
            self._clear_receive_state()
            await self._write(build_status_query(self.communication_code))
            before_frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == STATUS_ID
            )
            before = parse_state(before_frame, allowed_message_ids=(STATUS_ID,))
            command = build_compartment_command(before, self.communication_code, target)
            self._clear_receive_state()
            await self._write(command)
            if settle_seconds:
                await asyncio.sleep(settle_seconds)
            # Response family for 0x0E is not required here; independent 0x0A is authoritative.
            self._clear_receive_state()
            await self._write(build_status_query(self.communication_code))
            after_frame = await self._wait_for(
                lambda f: len(f) == 19 and f[8] == STATUS_ID
            )
            after = parse_state(after_frame, allowed_message_ids=(STATUS_ID,))
            self._validate_protected(before, after, "compartment readback")
            if after.type_state != int(target):
                raise ProtocolError(
                    f"compartment readback: expected state {int(target)}, got {after.type_state}"
                )
            return after

    async def read_history(self, *, timeout: float = 30.0) -> HistoryData:
        """Read and strictly validate the complete 48-frame / 2880-sample history."""
        if timeout <= 0:
            raise ValidationError("history timeout must be positive")
        async with self._transaction_lock:
            self._clear_receive_state()
            await self._write(build_history_query(self.communication_code))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            frames: dict[int, bytes] = {}
            while len(frames) < 48:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    missing = sorted(set(range(1, 49)) - set(frames))
                    raise CommunicationError(
                        f"history transfer incomplete; frames={len(frames)}/48, missing={missing}"
                    )
                try:
                    frame = await asyncio.wait_for(self._queue.get(), remaining)
                except asyncio.TimeoutError as exc:
                    missing = sorted(set(range(1, 49)) - set(frames))
                    raise CommunicationError(
                        f"history timeout; frames={len(frames)}/48, missing={missing}"
                    ) from exc
                if len(frame) != 252 or frame[8] != HISTORY_ID:
                    continue
                seq, _ = parse_history_frame(frame)
                if seq in frames:
                    if frames[seq] != frame:
                        raise ProtocolError(f"conflicting duplicate history frame {seq}")
                    continue
                frames[seq] = frame

            if set(frames) != set(range(1, 49)):
                raise ProtocolError("history sequence set is incomplete")
            samples = []
            for seq in range(1, 49):
                decoded_seq, decoded = parse_history_frame(frames[seq])
                if decoded_seq != seq:
                    raise ProtocolError("history sequence ordering failure")
                samples.extend(decoded)
            if len(samples) != 2880:
                raise ProtocolError(f"history must contain 2880 samples, got {len(samples)}")
            return HistoryData(
                samples=tuple(samples),
                frame_count=48,
                retrieved_at=datetime.now().astimezone(),
            )
