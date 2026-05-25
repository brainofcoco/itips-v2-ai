
Autonomous PTZ Tracking
What it replaces: Writing complex algorithms to translate bounding box coordinates from a tracking model into mechanical Pan/Tilt/Zoom commands. How Dahua does it: Dahua's Auto-Tracking automatically locks onto a human or vehicle and physically moves the PTZ motors to keep them centered in the frame. You can simply turn this on and let the camera do the work.

[Config] PTZ Auto Movement: Page 388 (Section 8.1.11)
.
PTZ Control (Manual Override via dashboard): Page 374 (Section 8.1.5)


# 8.1.11 [Config] PTZ Auto Movement

Configure PTZ automatic movement scheduling and actions.

---

# Config Data Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| PtzAutoMovement | object[][] | O | Two-dimensional array. First dimension represents channel index, second dimension represents task configuration. | |
| Enable | bool | O | Enable or disable PTZ auto movement. | true |

---

# TimeSection

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TimeSection | object[][] | O | Time range during which PTZ timed action is active. | `["0 00:00:00-23:59:59"]` |

## TimeSection Format

```txt
TimeSection[week][section]=start-end
```

Example:

```txt
TimeSection[1][0]=00:00:00-23:59:59
```

---

## Week Values

| Value | Day |
|---|---|
| 0 | Sunday |
| 1 | Monday |
| 2 | Tuesday |
| 3 | Wednesday |
| 4 | Thursday |
| 5 | Friday |
| 6 | Saturday |

---

## Section Rules

- Maximum of 6 sections per day.
- Each section defines an active PTZ schedule period.

---

# PTZ Function

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Function | enumchar[32] | O | PTZ operation mode. | `"Scan"` |

## Supported Function Values

```txt
Scan
Preset
Pattern
Tour
None
```

---

# Function Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ScanId | int | O | Scan ID, starting from 1. | 1 |
| PresetId | int | O | Preset ID, starting from 1. | 1 |
| PatternId | int | O | Pattern ID, starting from 1. | 1 |
| TourId | int | O | Tour ID, starting from 1. | 1 |

---

# Auto Homing

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| AutoHoming | object | O | Auto homing configuration. | |
| Enable | bool | O | Enable or disable auto homing. | true |
| ReturnTime | uint | O | Recovery time in seconds. | 300 |

---

# Snapshot Configuration

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| SnapshotEnable | bool | O | Enable or disable snapshot when Function is `Preset`. | false |
| SnapshotDelayTime | int | O | Snapshot delay time when Function is `Preset`. | 30 |

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=PtzAutoMovement
```

---

# Get Config Response Example

```txt
table.PtzAutoMovement[0][0].AutoHoming.Time=30
table.PtzAutoMovement[0][0].Enable=false
table.PtzAutoMovement[0][0].Function=None
table.PtzAutoMovement[0][0].PatternId=0
table.PtzAutoMovement[0][0].PresetId=0
table.PtzAutoMovement[0][0].ScanId=0
table.PtzAutoMovement[0][0].SnapshotDelayTime=30
table.PtzAutoMovement[0][0].SnapshotEnable=false

table.PtzAutoMovement[0][0].TimeSection[0][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[0][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[0][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[0][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[0][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[0][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[1][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[1][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[1][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[1][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[1][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[1][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[2][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[2][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[2][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[2][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[2][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[2][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[3][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[3][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[3][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[3][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[3][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[3][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[4][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[4][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[4][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[4][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[4][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[4][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[5][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[5][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[5][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[5][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[5][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[5][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TimeSection[6][0]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[6][1]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[6][2]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[6][3]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[6][4]=0 00:00:00-23:59:59
table.PtzAutoMovement[0][0].TimeSection[6][5]=0 00:00:00-23:59:59

table.PtzAutoMovement[0][0].TourId=0
...
```

---

# Set Config Request Example

```http
http://192.168.1.108/cgi-bin/configManager.cgi?action=setConfig&PtzAutoMovement[0][0].Function=Preset&PtzAutoMovement[0][0].PresetId=1
```

---

# Set Config Response Example

```txt
OK
```

---

# PTZ Auto Movement Workflow

## 1. Configure Schedule

Define active periods using:

```txt
TimeSection[week][section]
```

---

## 2. Select Function

Choose PTZ action:

- Preset
- Scan
- Tour
- Pattern

---

## 3. Configure IDs

Assign:

- PresetId
- ScanId
- TourId
- PatternId

---

## 4. Enable Auto Homing (Optional)

Automatically return PTZ to default state after inactivity.

---

# Common Use Cases

## Preset Patrol

```txt
Function=Preset
PresetId=1
```

Move camera automatically to a preset.

---

## Continuous Scan

```txt
Function=Scan
ScanId=1
```

Run horizontal/vertical scan movement.

---

## PTZ Tour

```txt
Function=Tour
TourId=1
```

Cycle through multiple preset points.

---

# Notes

- IDs start from `1`.
- TimeSection supports up to 6 schedules per day.
- Snapshot configuration only applies when `Function=Preset`.
- Coordinate/schedule values are device dependent.


# 8.1.5 PTZ Control

---

# PTZ Basic Movement

Start moving the PTZ.

## Request

### Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=start
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index, starting from 1. | 1 |
| code | char[16] | R | PTZ operation code. | `"Up"` |
| arg1 | int | O | Operation parameter 1. | 0 |
| arg2 | int | O | Operation parameter 2. | 1 |
| arg3 | int | O | Operation parameter 3. | 0 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=start&channel=1&code=Up&arg1=0&arg2=1&arg3=0
```

---

## Response

### Response Params

```txt
OK in body
```

### Response Example

```txt
OK
```

---

# Stop PTZ Movement

Stop moving the PTZ.

## Request

### Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=stop
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index, starting from 1. | 1 |
| code | char[16] | R | PTZ operation code. | `"Up"` |
| arg1 | int | O | Reserved. | 0 |
| arg2 | int | O | Reserved. | 0 |
| arg3 | int | O | Reserved. | 0 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=stop&code=Up&channel=1&arg1=0&arg2=0&arg3=0
```

---

## Response Example

```txt
OK
```

---

# PTZ Operation Codes

| Code | Description | arg1 | arg2 | arg3 |
|---|---|---|---|---|
| Up | Move up | 0 | Vertical speed `[1-8]` | 0 |
| Down | Move down | 0 | Vertical speed `[1-8]` | 0 |
| Left | Move left | 0 | Horizontal speed `[1-8]` | 0 |
| Right | Move right | 0 | Horizontal speed `[1-8]` | 0 |
| LeftUp | Move upper-left | Vertical speed `[1-8]` | Horizontal speed `[1-8]` | 0 |
| RightUp | Move upper-right | Vertical speed `[1-8]` | Horizontal speed `[1-8]` | 0 |
| LeftDown | Move lower-left | Vertical speed `[1-8]` | Horizontal speed `[1-8]` | 0 |
| RightDown | Move lower-right | Vertical speed `[1-8]` | Horizontal speed `[1-8]` | 0 |
| ZoomWide | Zoom in | 0 | 0 | 0 |
| ZoomTele | Zoom out | 0 | 0 | 0 |
| FocusNear | Focus near | 0 | 0 | 0 |
| FocusFar | Focus far | 0 | 0 | 0 |
| IrisLarge | Increase aperture | 0 | 0 | 0 |
| IrisSmall | Decrease aperture | 0 | 0 | 0 |

---

# PTZ Continuous Movement

Start continuously moving the PTZ.

## Request

### Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=start&code=Continuously
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index, starting from 1. | 1 |
| code | char[16] | R | Must be `"Continuously"` | `"Continuously"` |
| arg1 | int | O | Horizontal movement step. | 5 |
| arg2 | int | O | Vertical movement step. | 5 |
| arg3 | int | O | Zoom speed `[-100,100]` | 5 |
| arg4 | int | O | Timeout in seconds (max 3600). | 60 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=start&code=Continuously&channel=1&arg1=5&arg2=5&arg3=5&arg4=60
```

---

## Response Example

```txt
OK
```

---

# Continuous Movement Direction Mapping

| Movement | arg1 | arg2 |
|---|---|---|
| Move left | `< -4` | 0 |
| Move right | `> 4` | 0 |
| Move up | 0 | `> 4` |
| Move down | 0 | `< -4` |
| Move upper-left | `< -4` | `> 4` |
| Move upper-right | `> 4` | `> 4` |
| Move lower-left | `< -4` | `< -4` |
| Move lower-right | `> 4` | `< -4` |

---

# Stop Continuous Movement

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=stop&code=Continuously
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index. | 1 |
| code | char[16] | R | Must be `"Continuously"` | `"Continuously"` |
| arg1 | int | O | Reserved | 0 |
| arg2 | int | O | Reserved | 0 |
| arg3 | int | O | Reserved | 0 |
| arg4 | int | O | Reserved | 0 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=stop&code=Continuously&channel=1&arg1=0&arg2=0&arg3=0&arg4=0
```

---

## Response Example

```txt
OK
```

---

# 3D Positioning

Move PTZ to a selected screen region.

## Request URL

```http
http://<server>/cgi-bin/ptzBase.cgi?action=moveDirectly
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index. | 1 |
| startPoint | int[2] | R | Start point `[x,y]` normalized to `0-8192`. | `[7253,2275]` |
| endPoint | int[2] | R | End point `[x,y]` normalized to `0-8192`. | `[7893,3034]` |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptzBase.cgi?action=moveDirectly&channel=1&startPoint[0]=7253&startPoint[1]=2275&endPoint[0]=7893&endPoint[1]=3034
```

---

## Response Example

```txt
OK
```

---

# Relative PTZ Movement

Move PTZ relatively from current position.

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=moveRelatively
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index. | 1 |
| arg1 | double | O | Relative horizontal movement `[-1,1]` | 0.1 |
| arg2 | double | O | Relative vertical movement `[-1,1]` | 0.1 |
| arg3 | double | O | Relative zoom `[-1,1]` | 0.5 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=moveRelatively&channel=1&arg1=0.1&arg2=0.1&arg3=0.5
```

---

## Response Example

```txt
OK
```

---

# Accurate PTZ Positioning

Move PTZ to an exact position.

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=moveAbsolutely
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index. | 1 |
| arg1 | double | O | Absolute horizontal position normalized to `[-1,1]`. | -0.8 |
| arg2 | double | O | Absolute vertical position normalized to `[-1,1]`. | 0.3 |
| arg3 | double | O | Absolute zoom normalized to `[-1,1]`. | 0.5 |

---

## Horizontal Position Mapping

```txt
arg1 < 0:
Angle = 180 * arg1 + 360
Range = [180,360]

arg1 >= 0:
Angle = 180 * arg1
Range = [0,180]
```

---

## Vertical Position Mapping

```txt
Angle = -180 * arg2
Range = [-180,180]
```

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=moveAbsolutely&channel=1&arg1=-0.8&arg2=0.3&arg3=0.5
```

---

## Response Example

```txt
OK
```

---

# 8.1.6 Preset

---

# Get Preset Information

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=getPresets
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | O | Video channel number starting from 1. | 1 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=getPresets&channel=1
```

---

# Response Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| presets | object[] | R | Preset information list | |
| +Index | int | R | Preset number starting from 1 | 1 |
| +Name | char[256] | O | Preset name | `"preset1"` |
| +Type | int | O | Preset type | 0 |
| +PresetFunction | char[16] | O | Special preset function | `"VideoBlack"` |
| +Position | int[3] | O | Preset coordinates and zoom | `[900,-900,5]` |

---

## Preset Types

| Value | Description |
|---|---|
| 0 | Normal preset |
| 1 | Smart rule preset |
| 2 | Special preset |

---

## Special Preset Functions

| Value | Description |
|---|---|
| VideoBlack | Day/Night B/W |
| VideoColor | Day/Night Color |
| VideoBrightness | Day/Night Auto |

---

## Position Definition

```txt
Position[0] = Horizontal angle [0,3599]
Position[1] = Vertical angle [-1800,1800]
Position[2] = Zoom range [0,128]
```

---

## Response Example

```txt
presets[0].Index=1
presets[0].Name=Preset 1
presets[0].Type=0
presets[0].PresetFunction="VideoBlack"
presets[0].Position=[900,-900,5]
```

---

# Configure Preset

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=start&code=SetPreset
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index | 1 |
| arg1 | int | O | Ignore | 0 |
| arg2 | int | R | Preset number starting from 1 | 1 |
| arg3 | int | O | Ignore | 0 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=start&code=SetPreset&channel=1&arg1=0&arg2=2&arg3=0
```

---

## Response Example

```txt
OK
```

---

# Configure Preset Name

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=setPreset
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index | 1 |
| arg1 | int | R | Preset number starting from 1 | 2 |
| arg2 | char[256] | R | Preset name | `"preset2"` |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=setPreset&channel=1&arg1=2&arg2=preset2
```

---

## Response Example

```txt
OK
```

---

# Delete Preset

## Request URL

```http
http://<server>/cgi-bin/ptz.cgi?action=start&code=ClearPreset
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | R | PTZ channel index | 1 |
| arg1 | int | O | Ignore | 0 |
| arg2 | int | R | Preset number starting from 1 | 2 |
| arg3 | int | O | Ignore | 0 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/ptz.cgi?action=start&code=ClearPreset&channel=1&arg1=0&arg2=2&arg3=0
```

---

## Response Example

```txt
OK
```