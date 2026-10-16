# Portable Refrigerator BLE Protocol Specification

## 1. Scope

This specification defines the BLE application protocol used to:

- query current refrigerator state;
- change user configuration;
- control dual-compartment enable state;
- retrieve 24 hours of historical telemetry;
- decode temperatures, setpoints, supply voltage, operating mode, logical power, temperature units, and battery-protection level;
- reconstruct protocol frames fragmented across BLE notifications.

It covers the currently characterized single-compartment and dual-compartment behavior. Fields whose semantics have not been established are explicitly identified as unknown and should be preserved rather than guessed.

---

## 2. BLE GATT transport

### 2.1 Primary proprietary service

```text
00001000-0000-1000-8000-00805f9b34fb
```

### 2.2 Host-to-refrigerator command characteristic

```text
00001001-0000-1000-8000-00805f9b34fb
```

Characteristic description:

```text
RX
```

Direction:

```text
Host -> Refrigerator
```

Known properties:

```text
read
write
write-without-response
```

Protocol commands are written to this characteristic.

### 2.3 Refrigerator-to-host notification characteristic

```text
00001002-0000-1000-8000-00805f9b34fb
```

Characteristic description:

```text
TX
```

Direction:

```text
Refrigerator -> Host
```

Known properties:

```text
read
notify
```

Notifications should be enabled before issuing application-protocol requests.

### 2.4 Other proprietary characteristics

The service may also expose:

```text
00001003-0000-1000-8000-00805f9b34fb    REG_WRITE
00001004-0000-1000-8000-00805f9b34fb    REG_READ
00001005-0000-1000-8000-00805f9b34fb    REG
```

Their semantics are not defined by this specification and they are not required for the operations described below.

---

## 3. General protocol frame

Main commands and responses use:

```text
68 LEN BODY... CHECKSUM EC
```

Conceptually:

```text
+--------+--------+-------------------+----------+--------+
| START  | LENGTH | BODY              | CHECKSUM | END    |
+--------+--------+-------------------+----------+--------+
| 0x68   | 1 byte | variable length   | 1 byte   | 0xEC   |
+--------+--------+-------------------+----------+--------+
```

### 3.1 Length

`LEN` is related to the complete protocol-frame size by:

```text
total_frame_length = LEN + 2
```

or:

```text
LEN = total_frame_length - 2
```

Examples:

```text
LEN 0x09 -> 11-byte frame
LEN 0x0A -> 12-byte frame
LEN 0x11 -> 19-byte frame
LEN 0xFA -> 252-byte frame
```

### 3.2 Checksum

The checksum is the low byte of the unsigned sum of every protocol byte beginning with `LEN` and ending immediately before the checksum:

```python
checksum = sum(frame[1:-2]) & 0xFF
```

Excluded:

```text
frame[0]   0x68 start delimiter
frame[-2]  checksum itself
frame[-1]  0xEC terminator
```

A body-byte change of signed delta `D` changes the additive checksum by the same `D`, modulo 256. Implementations should nevertheless recalculate the entire checksum after constructing a packet.

---

## 4. BLE fragmentation and protocol reassembly

A BLE notification is a transport fragment, not necessarily a protocol frame.

A protocol frame may arrive:

- completely in one notification;
- fragmented across two or more notifications;
- with fragmentation boundaries unrelated to protocol-field boundaries.

This applies to both 19-byte state frames and 252-byte history frames.

### 4.1 Required receive algorithm

Maintain a continuous receive buffer:

1. Append each notification payload to the buffer.
2. Synchronize to `0x68`.
3. If fewer than two bytes are buffered, wait for more.
4. Read `LEN = buffer[1]`.
5. Calculate `required = LEN + 2`.
6. Wait until at least `required` bytes exist.
7. Extract exactly `required` bytes.
8. Validate:
   - `frame[0] == 0x68`;
   - `frame[1] == len(frame) - 2`;
   - `frame[-1] == 0xEC`;
   - additive checksum.
9. Dispatch the complete frame according to its message family.
10. Continue with any remaining buffered bytes.

If synchronization is lost, bytes before the next plausible `0x68` may be discarded.

Example fragmented 19-byte response:

```text
BLE #1: 68
BLE #2: 11 00
BLE #3: remaining 16 bytes
```

These three notifications must be concatenated and parsed as one 19-byte frame.

---

## 5. Protocol identities and codes

The protocol uses multiple identifiers which must not be conflated:

```text
BLE address                transport identity; outside application frame
protocol address           two bytes returned in application state
communication code         three command-side authentication/communication bytes
device connection code     three response-side bytes, typically rendered as six hex digits
```

The protocol address and connection code are returned by the refrigerator. The command communication code is a distinct field.

This specification intentionally uses symbolic values:

```text
AH AL       protocol address bytes
C0 C1 C2    communication-code bytes
K0 K1 K2    device connection-code bytes
```

---

## 6. Operation/message identifiers

Known application operations:

```text
0x0A    current/live state query and response
0x0B    configuration write and resulting-state response
0x0C    24-hour historical telemetry retrieval
0x0E    dual-compartment enable-state control
```

---

# Current State

## 7. `0x0A` current-state request

An 11-byte status request has this layout:

```text
Offset  Field
------  ------------------------------
0       68
1       09
2       FF
3       FF
4       FF
5       C0
6       C1
7       C2
8       0A
9       CHECKSUM
10      EC
```

Symbolically:

```text
68 09 FF FF FF C0 C1 C2 0A SUM EC
```

The three command bytes `C0 C1 C2` are the communication code.

---

## 8. Successful current-state response

Successful state is a 19-byte frame:

```text
Offset  Field
------  ------------------------------
0       68
1       11
2       TYPE_STATE
3       ADDRESS_HIGH
4       ADDRESS_LOW
5       CONNECTION_CODE[0]
6       CONNECTION_CODE[1]
7       CONNECTION_CODE[2]
8       0A
9       TEMP_A
10      TEMP_B
11      SETPOINT_A
12      SETPOINT_B
13      VOLTAGE_MAJOR
14      VOLTAGE_MINOR
15      DEVICE_PARAM
16      DEVICE_STATE
17      CHECKSUM
18      EC
```

Symbolically:

```text
68 11 TYPE AH AL K0 K1 K2 0A TA TB SA SB VH VL PARAM STATE SUM EC
```

A valid state decoder should require both:

```text
frame length = 19
frame[8] = 0x0A
```

---

## 9. Type / compartment state (`TYPE_STATE`)

`frame[2]` identifies refrigerator type or active dual-compartment configuration.

Defined values:

```text
0x00    dual-compartment: channels A and B active
0x01    dual-compartment: channel A active, channel B disabled
0x02    dual-compartment: channel A disabled, channel B active
0x03    single-compartment refrigerator
```

For the characterized dual-display layout:

```text
protocol channel B -> left-side physical display/compartment
protocol channel A -> right-side physical display/compartment
```

Therefore on that layout:

```text
TYPE 0    left ON,  right ON
TYPE 1    left OFF, right ON
TYPE 2    left ON,  right OFF
```

For other models, retain protocol labels A/B internally unless their physical orientation has separately been established.

There is no defined dual-compartment state for both channels disabled. Complete logical refrigeration shutdown uses the separate power bit in `deviceParam`.

---

## 10. Protocol address

```text
frame[3] = ADDRESS_HIGH
frame[4] = ADDRESS_LOW
```

These returned bytes are used in addressed write/control packets.

---

## 11. Device connection code

```text
frame[5] = K0
frame[6] = K1
frame[7] = K2
```

The three bytes are commonly rendered as a six-hex-digit connection code:

```text
K0K1K2
```

They are response-side device identity/connection data and are distinct from outgoing communication-code bytes `C0 C1 C2`.

---

## 12. Temperature encoding

Normal temperatures and setpoints use an offset representation:

```python
temperature_c = raw - 50
raw = temperature_c + 50
```

Examples:

```text
Raw     Celsius
----    -------
0x1C    -22 C
0x28    -10 C
0x32      0 C
0x37      5 C
0x3C     10 C
```

Characterized setpoint range:

```text
-22 C .. +10 C
```

or:

```text
0x1C .. 0x3C
```

### 12.1 Actual-temperature error sentinels

Actual-temperature values must be checked for:

```text
0xE6    Error6
0xE7    Error7
```

before applying `raw - 50`.

The physical fault meanings of these two error values remain undefined.

---

## 13. Live temperature fields

Dual-compartment:

```text
frame[9]     current temperature channel A
frame[10]    current temperature channel B
```

Single-compartment operation uses the B slot:

```text
frame[10]    current temperature
```

---

## 14. Setpoint fields

Dual-compartment:

```text
frame[11]    setpoint channel A
frame[12]    setpoint channel B
```

Single-compartment operation uses:

```text
frame[12]    setpoint
```

Setpoints use the same Celsius offset encoding.

---

## 15. Temperature unit behavior

The wire representation remains Celsius-offset based regardless of display preference.

Fahrenheit display conversion is:

```python
fahrenheit = celsius * 1.8 + 32
```

Changing the display-unit bit does not require rewriting temperature fields into Fahrenheit.

---

## 16. Voltage encoding

Live status:

```text
frame[13] = VOLTAGE_MAJOR
frame[14] = VOLTAGE_MINOR
```

The displayed value is the decimal form:

```text
VOLTAGE_MAJOR.VOLTAGE_MINOR V
```

Example:

```text
0C 1C -> 12.28 V
```

The minor byte is the decimal component shown after the decimal point, not a binary fraction.

---

# Device Parameter Bitfield

## 17. `deviceParam`

```text
frame[15] = DEVICE_PARAM
```

Bit layout:

```text
bit      7   6   5   4   3   2   1   0
         ?   M   M   ?   P   U   B   B
```

Masks:

```text
0x80    unknown
0x60    cooling mode
0x10    unknown
0x08    logical refrigerator power
0x04    temperature display units
0x03    battery protection
```

All defined user-facing fields below have been observed to round-trip through write response and independent status readback.

---

## 18. Cooling mode

```text
bits 6:5
mask 0x60
```

Encoding:

```text
00 / 0x00    ECON
01 / 0x20    NORM
10 / 0x40    QUICK
11 / 0x60    undefined/reserved
```

Read:

```python
mode = device_param & 0x60
```

Write safely:

```python
new_param = (old_param & ~0x60) | new_mode
```

Preserve all unrelated bits.

---

## 19. Logical refrigerator power

```text
bit 3
mask 0x08
```

Encoding:

```text
0    OFF
1    ON
```

Set ON:

```python
new_param = old_param | 0x08
```

Set OFF:

```python
new_param = old_param & ~0x08
```

This is a logical refrigeration-power state, not necessarily removal of BLE/module power. BLE communication can remain operational after the logical OFF transition.

---

## 20. Temperature display unit

```text
bit 2
mask 0x04
```

Encoding:

```text
0    Celsius
1    Fahrenheit
```

Set Fahrenheit:

```python
new_param = old_param | 0x04
```

Set Celsius:

```python
new_param = old_param & ~0x04
```

---

## 21. Battery protection

```text
bits 1:0
mask 0x03
```

Encoding:

```text
00 / 0x00    LOW
01 / 0x01    MID
10 / 0x02    HIGH
11 / 0x03    undefined/reserved
```

Safe setter:

```python
new_param = (old_param & ~0x03) | protection
```

where `protection` is one of `0x00`, `0x01`, or `0x02`.

---

## 22. Undefined `deviceParam` values

Remain undefined:

```text
bit 7
bit 4
cooling encoding 0x60
battery-protection encoding 0x03
```

Implementations should preserve bits 7 and 4 exactly as reported and should not deliberately emit reserved cooling/protection encodings.

---

## 23. `deviceState`

```text
frame[16] = DEVICE_STATE
```

This is a separate eight-bit state field.

Its bit-level semantics remain undefined. Extended live polling has shown `0x00` as a stable normal value across many valid samples, but no observed transition has supplied evidence for assigning meanings to individual bits.

Implementations should:

- retain the field;
- expose/log it as raw hex/bits;
- avoid using it for control decisions until semantics are established.

---

# Configuration Write

## 24. `0x0B` configuration packet

A dual-compartment configuration packet is 19 bytes:

```text
Offset  Field
------  ------------------------------
0       68
1       11
2       03
3       ADDRESS_HIGH
4       ADDRESS_LOW
5       C0
6       C1
7       C2
8       0B
9       00
10      00
11      SETPOINT_A
12      SETPOINT_B
13      00
14      00
15      DEVICE_PARAM
16      00
17      CHECKSUM
18      EC
```

Symbolically:

```text
68 11 03 AH AL C0 C1 C2 0B 00 00 SA SB 00 00 PARAM 00 SUM EC
```

The zero-valued positions are not populated from similarly positioned response values:

```text
frame[9]
frame[10]
frame[13]
frame[14]
frame[16]
```

They should remain at their established template values unless future evidence defines them otherwise.

### 24.1 Single-compartment setpoint

For single-compartment operation the active setpoint field is:

```text
frame[12]
```

The inactive setpoint slot should remain at its established/default value unless model-specific behavior is known.

---

## 25. Safe `0x0B` modification sequence

Recommended procedure:

```text
1. Send 0x0A current-state query.
2. Validate and decode the fresh state response.
3. Build a complete 0x0B packet using the current protocol address,
   current setpoint(s), current deviceParam, and configured communication code.
4. Modify only the desired field/bits.
5. Recalculate checksum.
6. Verify locally that unrelated bytes/bits were preserved.
7. Transmit once.
8. Reassemble and validate the immediate 0x0B response.
9. Optionally issue an independent 0x0A query.
10. Verify desired state and preservation of unrelated configuration.
```

Starting from fresh state minimizes accidental overwriting of concurrent or previously changed settings.

---

## 26. `0x0B` resulting-state response

A successful configuration write returns a 19-byte state-style frame:

```text
68 11 TYPE AH AL K0 K1 K2 0B TA TB SA SB VH VL PARAM STATE SUM EC
```

Layout is equivalent to current-state response except:

```text
frame[8] = 0x0B
```

Its fields describe the resulting state after application of the configuration write.

---

## 27. Useful checksum deltas

Because the checksum is additive:

```text
setpoint raw +1            checksum +1
setpoint raw -1            checksum -1
Celsius -> Fahrenheit      PARAM +0x04, checksum +0x04
Fahrenheit -> Celsius      PARAM -0x04, checksum -0x04
OFF -> ON                  PARAM +0x08, checksum +0x08
ON -> OFF                  PARAM -0x08, checksum -0x08
NORM -> QUICK              PARAM +0x20, checksum +0x20
QUICK -> NORM              PARAM -0x20, checksum -0x20
NORM -> ECON               PARAM -0x20, checksum -0x20
```

These are useful diagnostics; complete checksum recalculation remains preferred.

---

# Dual-Compartment Control

## 28. `0x0E` compartment-state command

A compartment-control command is 12 bytes:

```text
Offset  Field
------  ------------------------------
0       68
1       0A
2       CURRENT_TYPE_STATE
3       ADDRESS_HIGH
4       ADDRESS_LOW
5       C0
6       C1
7       C2
8       0E
9       TARGET_COMPARTMENT_STATE
10      CHECKSUM
11      EC
```

Symbolically:

```text
68 0A CURRENT AH AL C0 C1 C2 0E TARGET SUM EC
```

Defined target states:

```text
TARGET  Meaning
------  -------------------------------------------
0       channels A and B active
1       channel A active, channel B disabled
2       channel A disabled, channel B active
```

For the characterized physical dual-display mapping:

```text
0       left ON,  right ON
1       left OFF, right ON
2       left ON,  right OFF
```

No target value for both channels disabled is defined. Use logical power control (`deviceParam` bit 3) to turn refrigeration OFF rather than inventing a fourth compartment state.

### 28.1 Verification

After a `0x0E` transition, an implementation should independently issue `0x0A` and verify:

```text
frame[2] == requested target state
protocol address unchanged
setpoints unchanged
deviceParam unchanged
```

Actual temperatures are not required to remain constant because compartment enable state can legitimately affect thermal behavior.

The states `0`, `1`, and `2` have been exercised as valid transitions and can be restored to an initial state using the same control.

---

# Historical Telemetry

## 29. `0x0C` history request

The refrigerator provides a fixed-depth historical telemetry dataset.

Request:

```text
68 09 FF FF FF C0 C1 C2 0C SUM EC
```

The request uses the same short-query structure and normal additive checksum as `0x0A`.

---

## 30. History dataset dimensions

A complete history transfer contains:

```text
48 protocol frames
60 sample records per frame
2880 samples total
```

The history represents:

```text
24 hours
```

Therefore:

```text
2880 / 24 = 120 samples/hour
120 / 60 = 2 samples/minute
sampling period = 30 seconds
```

Canonical dataset properties:

```text
history span       24 hours
sample interval    30 seconds
sample count       2880
frame count        48
samples/frame      60
```

---

## 31. History response frame

Each history protocol frame is 252 bytes:

```text
Offset     Field
---------  ----------------------------------
0          68
1          FA
2          TYPE_STATE
3          ADDRESS_HIGH
4          ADDRESS_LOW
5          CONNECTION_CODE[0]
6          CONNECTION_CODE[1]
7          CONNECTION_CODE[2]
8          0C
9          SEQUENCE
10..249    HISTORY_PAYLOAD (240 bytes)
250        CHECKSUM
251        EC
```

Symbolically:

```text
68 FA TYPE AH AL K0 K1 K2 0C SEQ
[240-byte payload]
SUM EC
```

Length validation:

```text
0xFA + 2 = 252 bytes
```

Checksum:

```python
frame[250] == sum(frame[1:250]) & 0xFF
```

---

## 32. History frame sequence

A complete transfer uses:

```text
SEQ = 0x01 through 0x30
```

which is decimal:

```text
1 through 48
```

A robust implementation should detect:

- missing sequence numbers;
- duplicates;
- unexpected sequence values;
- checksum failure;
- incomplete final transfer.

For a complete normal history response require all 48 sequence values.

---

## 33. History payload records

Each history frame has:

```text
240 payload bytes
```

which contain:

```text
60 records × 4 bytes
```

Record format:

```text
Offset  Field
------  ------------------------------
+0      TEMP_A
+1      TEMP_B
+2      VOLTAGE_MAJOR
+3      VOLTAGE_MINOR
```

Thus the payload is:

```text
TA TB VH VL
TA TB VH VL
TA TB VH VL
...
```

60 times.

Temperature uses the same:

```python
temperature_c = raw - 50
```

Historical voltage uses the same decimal major/minor representation as live status.

Example synthetic historical record:

```text
20 22 0C 1C
```

means:

```text
channel A temperature = -18 C
channel B temperature = -16 C
voltage               = 12.28 V
```

For single-compartment models, channel B is the operating temperature channel used by the UI; the four-byte history record format remains compatible.

---

## 34. History transfer fragmentation

History frames are much larger than common BLE notification payloads and are transported as fragments.

A possible observed transport fragmentation is:

```text
10-byte fragment
12 × 20-byte fragments
2-byte fragment
```

which totals:

```text
10 + 240 + 2 = 252 bytes
```

This fragmentation distribution is **not** part of the application protocol and must not be hard-coded.

Use the universal `0x68 / LEN+2` reassembler.

---

## 35. History sample ordering and time reconstruction

Frames are ordered by `SEQ`; records are ordered within each frame.

Zero-based sample index:

```python
sample_index = frame_index * 60 + record_index
```

where:

```text
frame_index  = 0..47
record_index = 0..59
sample_index = 0..2879
```

Records are an evenly spaced 30-second series. Individual records contain no explicit wall-clock timestamp.

BLE transfer timestamps must **not** be used as historical sample timestamps because the full 24-hour dataset is transferred rapidly after the request.

The final records represent the most recent end of the historical sequence and can closely correspond to a current-state observation taken adjacent to retrieval.

The exact wall-clock anchoring/boundary convention for the newest 30-second sample is not yet specified.

---

## 36. Recommended history acquisition

High-level implementation:

```python
enable_notifications()
send(history_request)

frames = {}

while transfer_not_complete:
    fragment = await next_notification()
    receive_buffer.extend(fragment)

    for frame in extract_complete_frames(receive_buffer):
        validate_frame(frame)

        if len(frame) == 252 and frame[8] == 0x0C:
            seq = frame[9]
            validate_sequence(seq)
            frames[seq] = frame

require set(frames) == set(range(1, 49))

records = []

for seq in range(1, 49):
    payload = frames[seq][10:250]

    for offset in range(0, 240, 4):
        records.append({
            "temp_a_c": payload[offset] - 50,
            "temp_b_c": payload[offset + 1] - 50,
            "voltage_major": payload[offset + 2],
            "voltage_minor": payload[offset + 3],
        })

assert len(records) == 2880
```

---

# Error Handling and Validation

## 37. Short rejection/error-class response

An unsuccessful query can return a valid 12-byte frame rather than a normal 19-byte status response.

General class:

```text
68 0A ... SUM EC
```

A 12-byte response must not be passed to the 19-byte state decoder.

Its internal payload semantics remain undefined. Treat it as a rejection/error-class response unless separately characterized.

---

## 38. Validation requirements by message type

### Any framed message

Require:

```text
start delimiter == 0x68
reported length  == actual frame length - 2
terminator       == 0xEC
checksum         == valid additive checksum
```

### Current state

Require:

```text
length      19 bytes
frame[8]    0x0A
```

### Configuration resulting state

Require:

```text
length      19 bytes
frame[8]    0x0B
```

### History frame

Require:

```text
length      252 bytes
frame[1]    0xFA
frame[8]    0x0C
frame[9]    expected sequence
checksum    valid
```

### Complete history

Require:

```text
48 history frames
sequences 1..48 exactly once
2880 decoded samples
```

---

# Recommended Client Behavior

## 39. Initialization

```text
Connect BLE
    ↓
discover service/characteristics
    ↓
enable TX notifications
    ↓
send 0x0A
    ↓
reassemble/validate 19-byte response
    ↓
learn protocol address and current state
```

---

## 40. Live monitoring

```text
send 0x0A periodically
    ↓
validate status
    ↓
decode current temperature(s)
setpoint(s)
voltage
deviceParam
deviceState raw byte
```

A poll period on the order of a few seconds is suitable for UI/live monitoring; protocol correctness does not depend on a specific polling interval.

---

## 41. Configuration transaction

```text
fresh 0x0A read
    ↓
build complete 0x0B from fresh values
    ↓
modify only intended field
    ↓
recalculate checksum
    ↓
write once
    ↓
validate 0x0B resulting-state response
    ↓
optional independent 0x0A verification
```

Unknown bits must be preserved.

---

## 42. Compartment-state transaction

```text
fresh 0x0A read
    ↓
build 0x0E with current TYPE_STATE and desired target
    ↓
write once
    ↓
allow physical/UI transition
    ↓
independent 0x0A readback
    ↓
verify frame[2] target and preservation of unrelated configuration
```

Defined states are only `0`, `1`, and `2`.

---

## 43. History transaction

```text
send one 0x0C request
    ↓
accumulate BLE fragments into protocol stream
    ↓
reassemble 252-byte frames
    ↓
validate sequence/checksum
    ↓
collect frames 1..48
    ↓
decode 60 records/frame
    ↓
obtain 2880 samples
    ↓
interpret as 24 h at 30 s/sample
```

---

# Implementation Safety Rules

## 44. Preserve unknown state

When writing configuration:

- read first;
- preserve `deviceParam` bit 7;
- preserve `deviceParam` bit 4;
- avoid cooling encoding `11`;
- avoid battery-protection encoding `11`;
- do not copy `deviceState` into unknown write-template fields;
- leave established reserved/template zero fields unchanged.

---

## 45. Avoid stale full-configuration writes

A `0x0B` packet carries multiple settings simultaneously. A host that changes only one setting should still construct the command from a fresh status response so that unrelated current values are preserved.

---

## 46. Verify writes

For controllable settings, response acknowledgement alone should not be the only verification when correctness matters.

Recommended:

```text
write
-> validate immediate response
-> fresh independent status query
-> compare intended and protected fields
```

---

# Remaining Undefined Areas

## 47. Fields/values intentionally left undefined

```text
deviceParam bit 7
deviceParam bit 4

deviceParam cooling encoding 11 / 0x60
deviceParam battery-protection encoding 11 / 0x03

deviceState individual bits

Error6 physical meaning
Error7 physical meaning

12-byte rejection/error payload semantics

exact physical channel A/B orientation on models other than the characterized dual-display layout

REG_WRITE semantics
REG_READ semantics
REG semantics

other proprietary service/characteristic behavior

exact wall-clock anchoring rule for history sample 2879
```

These unknowns do not prevent implementation of the characterized live telemetry, setpoints, cooling mode, logical power, Celsius/Fahrenheit preference, battery protection, dual-compartment enable control, voltage telemetry, and 24-hour history retrieval.

---

# Compact Protocol Reference

## 48. Frame signatures

### Current-state request

```text
68 09 FF FF FF C0 C1 C2 0A SUM EC
```

### Current-state response

```text
68 11 TYPE AH AL K0 K1 K2 0A TA TB SA SB VH VL PARAM STATE SUM EC
```

### Configuration write

```text
68 11 03 AH AL C0 C1 C2 0B 00 00 SA SB 00 00 PARAM 00 SUM EC
```

### Configuration resulting-state response

```text
68 11 TYPE AH AL K0 K1 K2 0B TA TB SA SB VH VL PARAM STATE SUM EC
```

### History request

```text
68 09 FF FF FF C0 C1 C2 0C SUM EC
```

### History response frame

```text
68 FA TYPE AH AL K0 K1 K2 0C SEQ [240-byte payload] SUM EC
```

### Dual-compartment control

```text
68 0A CURRENT AH AL C0 C1 C2 0E TARGET SUM EC
```

---

## 49. Core formulas

```python
# Frame length
total_length = frame[1] + 2

# Checksum
checksum = sum(frame[1:-2]) & 0xFF

# Temperature
temp_c = raw - 50

# Cooling
cooling = device_param & 0x60

# Power
power_on = bool(device_param & 0x08)

# Units
fahrenheit_display = bool(device_param & 0x04)

# Battery protection
protection = device_param & 0x03

# History sample count
48 * 60 == 2880

# History period
2880 * 30 seconds == 24 hours
```

---

## 50. Characterized control matrix

```text
Function                     Encoding / operation
---------------------------  ------------------------------------
Read current state           0x0A
Write configuration          0x0B
Read 24-hour history         0x0C
Control compartments         0x0E
Setpoint A                   config byte 11, raw=C+50
Setpoint B                   config byte 12, raw=C+50
Cooling ECON                 deviceParam bits 6:5 = 00
Cooling NORM                 deviceParam bits 6:5 = 01
Cooling QUICK                deviceParam bits 6:5 = 10
Logical power OFF            deviceParam bit 3 = 0
Logical power ON             deviceParam bit 3 = 1
Display Celsius              deviceParam bit 2 = 0
Display Fahrenheit           deviceParam bit 2 = 1
Battery protection LOW       deviceParam bits 1:0 = 00
Battery protection MID       deviceParam bits 1:0 = 01
Battery protection HIGH      deviceParam bits 1:0 = 10
Both compartments active     TYPE/TARGET = 0
Channel B disabled           TYPE/TARGET = 1
Channel A disabled           TYPE/TARGET = 2
```
