#!/usr/bin/env python3
"""Simple CLI harness for the unique_fridge library.

Read-only commands are safe defaults. Mutating commands require --allow-write.
The library itself performs fresh-state construction and independent readback.
"""

import argparse
import asyncio
import csv
from pathlib import Path

from unique_fridge import (
    BatteryProtection,
    CompartmentState,
    CoolingMode,
    RefrigeratorClient,
    TemperatureUnit,
)


def code3(text: str) -> bytes:
    cleaned = text.replace(":", "").replace(" ", "")
    try:
        value = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("code must contain six hexadecimal digits") from exc
    if len(value) != 3:
        raise argparse.ArgumentTypeError("code must contain exactly six hexadecimal digits")
    return value


def print_state(s):
    print(f"type/state:       {s.type_state}")
    print(f"protocol address: {s.protocol_address.hex().upper()}")
    print(f"connection code:  {s.connection_code.hex().upper()}")
    print(f"actual A:         {s.actual_a.celsius} C" if s.actual_a.celsius is not None else f"actual A: {s.actual_a.error}")
    print(f"actual B:         {s.actual_b.celsius} C" if s.actual_b.celsius is not None else f"actual B: {s.actual_b.error}")
    print(f"setpoint A/B:     {s.setpoint_a_c} / {s.setpoint_b_c} C")
    print(f"voltage:          {s.voltage:.2f} V")
    p = s.parameters
    print(f"deviceParam:      0x{p.raw:02X}")
    print(f"cooling:          {p.cooling.name}")
    print(f"power:            {'ON' if p.power_on else 'OFF'}")
    print(f"units:            {p.unit.name}")
    print(f"protection:       {p.battery_protection.name}")
    print(f"unknown bits:     bit7={int(p.unknown_bit7)} bit4={int(p.unknown_bit4)}")
    print(f"deviceState:      0x{s.device_state:02X}")


def require_write(args):
    if not args.allow_write:
        raise SystemExit("This command changes refrigerator configuration. Re-run with --allow-write.")


def save_history(path: Path, history):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["index", "temp_a_c", "temp_b_c", "voltage_major", "voltage_minor", "voltage"])
        for x in history.samples:
            w.writerow([x.index, x.temp_a_c, x.temp_b_c, x.voltage_major, x.voltage_minor, f"{x.voltage:.2f}"])


async def async_main(args):
    async with RefrigeratorClient(args.address, args.code) as fridge:
        if args.command == "status":
            print_state(await fridge.read_state())
            return
        if args.command == "history":
            h = await fridge.read_history(timeout=args.history_timeout)
            print(f"frames: {h.frame_count}")
            print(f"samples: {len(h.samples)}")
            print(f"complete: {h.complete}")
            print(f"span: {h.span_hours} h at {h.sample_interval_seconds} s/sample")
            print(f"first: A={h.samples[0].temp_a_c} B={h.samples[0].temp_b_c} V={h.samples[0].voltage:.2f}")
            print(f"last:  A={h.samples[-1].temp_a_c} B={h.samples[-1].temp_b_c} V={h.samples[-1].voltage:.2f}")
            if args.output:
                save_history(args.output, h)
                print(f"saved: {args.output}")
            return

        require_write(args)
        if args.command == "set-a":
            state = await fridge.set_setpoint_a(args.celsius)
        elif args.command == "set-b":
            state = await fridge.set_setpoint_b(args.celsius)
        elif args.command == "cooling":
            state = await fridge.set_cooling(CoolingMode[args.mode])
        elif args.command == "power":
            state = await fridge.set_power(args.state == "on")
        elif args.command == "units":
            state = await fridge.set_unit(
                TemperatureUnit.CELSIUS if args.unit == "c" else TemperatureUnit.FAHRENHEIT
            )
        elif args.command == "protection":
            state = await fridge.set_battery_protection(BatteryProtection[args.level])
        elif args.command == "compartment":
            mapping = {"both": CompartmentState.BOTH_ACTIVE,
                       "b-off": CompartmentState.CHANNEL_B_DISABLED,
                       "a-off": CompartmentState.CHANNEL_A_DISABLED}
            state = await fridge.set_compartment_state(mapping[args.state], settle_seconds=args.settle)
        else:
            raise RuntimeError("unknown command")
        print_state(state)


def parser():
    p = argparse.ArgumentParser(description="Test harness for unique_fridge")
    p.add_argument("--address", required=True, help="BLE MAC/address")
    p.add_argument("--code", required=True, type=code3, help="3-byte communication code as six hex digits")
    p.add_argument("--allow-write", action="store_true", help="required for mutating commands")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    h = sub.add_parser("history")
    h.add_argument("--history-timeout", type=float, default=30.0)
    h.add_argument("--output", type=Path)
    a = sub.add_parser("set-a"); a.add_argument("celsius", type=int)
    b = sub.add_parser("set-b"); b.add_argument("celsius", type=int)
    c = sub.add_parser("cooling"); c.add_argument("mode", choices=[x.name for x in CoolingMode])
    pw = sub.add_parser("power"); pw.add_argument("state", choices=["on", "off"])
    u = sub.add_parser("units"); u.add_argument("unit", choices=["c", "f"])
    bp = sub.add_parser("protection"); bp.add_argument("level", choices=[x.name for x in BatteryProtection])
    cp = sub.add_parser("compartment"); cp.add_argument("state", choices=["both", "b-off", "a-off"]); cp.add_argument("--settle", type=float, default=5.0)
    return p


def main():
    args = parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("Interrupted.")


if __name__ == "__main__":
    main()
