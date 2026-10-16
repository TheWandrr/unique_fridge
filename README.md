# unique-fridge

Async Python access to the characterized refrigerator BLE protocol.

## Install for development

```bash
python -m pip install -e .
```

## Basic use

```python
import asyncio
from unique_fridge import RefrigeratorClient

async def main():
    async with RefrigeratorClient("BLE-ADDRESS", bytes.fromhex("AA BB CC")) as fridge:
        state = await fridge.read_state()
        print(state.actual_a.celsius, state.actual_b.celsius, state.voltage)

asyncio.run(main())
```

## Safety model

- Frame length, terminator, and checksum are validated.
- BLE fragments are reassembled before decoding.
- Setpoints are limited to the characterized -22..+10 C range.
- Reserved cooling and battery-protection encodings are rejected.
- Unknown `deviceParam` bits 7 and 4 are preserved.
- Configuration methods perform a fresh state read, one write, validate the immediate `0x0B` state response, and perform an independent `0x0A` readback.
- Compartment control accepts only states 0, 1, and 2 and performs an independent `0x0A` readback.
- History requires all 48 checksum-valid sequence frames and exactly 2880 decoded samples.
- The CLI harness refuses mutating commands unless `--allow-write` is supplied.

## Harness examples

```bash
python test_unique_fridge.py --address AA:BB:CC:DD:EE:FF --code AABBCC status
python test_unique_fridge.py --address AA:BB:CC:DD:EE:FF --code AABBCC history --output history.csv
python test_unique_fridge.py --address AA:BB:CC:DD:EE:FF --code AABBCC --allow-write cooling ECON
python test_unique_fridge.py --address AA:BB:CC:DD:EE:FF --code AABBCC --allow-write protection HIGH
python test_unique_fridge.py --address AA:BB:CC:DD:EE:FF --code AABBCC --allow-write compartment both
```
