IoT Hub & Sensor Replacement
What it replaces: The Hikvision AX PRO Hub. How Dahua does it: If you want to simplify the hardware stack, Dahua cameras have physical Alarm I/O ports and built-in gateways. You can wire your gate sensors or vibration sensors directly into the camera or use Dahua's industrial IoT gateways, sending all alerts through the single Dahua HTTP stream.

# 4.9.2 [Config] Alarm Event

---

# Config Data Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Alarm | object[] | O | Alarm configuration array, alarm channels start from 0 | |
| +Enable | bool | R | Enable/Disable alarm from input channel | `false` |
| +EventHandler | EventHandler | O | Event handling settings. See `EventHandler` type description. | |
| +Name | char[] | O | Alarm input channel name | `Door1` |
| +SensorType | char[] | O | Sensor type | `NC` |

---

# SensorType Values

| Value | Description |
|---|---|
| NC | Normally Closed |
| NO | Normally Open |

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=Alarm
```

---

# Get Config Response Example

```txt
table.Alarm[0].Enable=false
table.Alarm[0].EventHandler....(output of EventHandler is described in GetEventHandler)
table.Alarm[0].Name=Door1
table.Alarm[0].SensorType=NC
table.Alarm[1]....
...
```

---

# Set Config Request Example

```http
http://192.168.1.108/cgi-bin/configManager.cgi?action=setConfig&Alarm[0].Enable=true
```

---

# Set Config Response Example

```txt
OK
```

---

# 4.9.3 [Config] Alarm Out

---

# Config Data Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| AlarmOut | object[] | O | Alarm output configuration array, channels start from 0 | |
| +Name | char[] | O | Alarm output port name | `Beep` |
| +Mode | int | O | Alarm output mode | `0` |
| +TriggerMode | enumint | O | Alarm output trigger mode | `-1` |

---

# Mode Values

| Value | Description |
|---|---|
| 0 | Automatically alarm |
| 1 | Force alarm |
| 2 | Close alarm |

---

# TriggerMode Values

| Value | Description |
|---|---|
| -1 | Not configured for linkage |
| 0 | Continuous alarm output trigger |
| 1 | Periodic trigger / close alarm output |
| 2 | Periodic switching of alarm output groups |

---

# TriggerMode Notes

- If an alarm output channel is **not configured for linkage**, any output linkage item can be configured.
- If configured with **continuous trigger alarm output**, it can still be configured by other events, but only continuous-trigger linkage is allowed.
- If configured with:
  - periodic trigger / close alarm output
  - periodic switch alarm output groups

  then the alarm output channel cannot be configured with other linkage events.

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=AlarmOut
```

---

# Get Config Response Example

```txt
table.AlarmOut[0].Mode=0
table.AlarmOut[0].Name=Beep
table.AlarmOut[0].TriggerMode=-1
...
```

---

# Set Config Request Example

```http
http://192.168.1.108/cgi-bin/configManager.cgi?action=setConfig&AlarmOut[0].Mode=0&AlarmOut[0].Name=port1
```

---

# Set Config Response Example

```txt
OK
```


# 4.9.3 [Config] Alarm Out

---

# Config Data Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| AlarmOut | object[] | O | Alarm out configuration array, alarm out channels start from 0 | |
| +Name | char[] | O | Alarm output port name | `Beep` |
| +Mode | int | O | Alarm output mode | `0` |
| +TriggerMode | enumint | O | Alarm output trigger mode | `-1` |

---

# Mode Values

| Value | Description |
|---|---|
| 0 | Automatically alarm |
| 1 | Force alarm |
| 2 | Close alarm |

---

# TriggerMode Values

| Value | Description |
|---|---|
| -1 | Not configured for linkage |
| 0 | Continuous alarm output trigger |
| 1 | Periodic trigger / close alarm output |
| 2 | Periodic switching of alarm output groups |

---

# TriggerMode Description

Each alarm output channel adds a flag to indicate whether the channel can be configured for linkage.

This flag is updated according to the configuration result during event linkage configuration.

If an alarm output channel is:

- **Not configured for linkage** → any output linkage item can be configured.
- Configured with **continuous trigger alarm output** → it can still be configured by other events, but only the continuous-trigger linkage item can be configured.
- Configured with:
  - periodic trigger / close alarm output
  - periodic switching alarm output groups

  → then the alarm output channel cannot be configured with any linkage items by other events.

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=AlarmOut
```

---

# Get Config Response Example

```txt
table.AlarmOut[0].Mode=0
table.AlarmOut[0].Name=Beep
table.AlarmOut[0].TriggerMode=-1
...
```

---

# Set Config Request Example

```http
http://192.168.1.108/cgi-bin/configManager.cgi?action=setConfig&AlarmOut[0].Mode=0&AlarmOut[0].Name=port1
```

---

# Set Config Response Example

```txt
OK
```

# 8.6 PIR Alarm

# 8.6.1 [Config] PIR Parameter

## Get PIR Parameter

### Request URL

```http
http://<server>/cgi-bin/pirAlarm.cgi?action=getPirParam
```

### Method

```txt
GET
```

---

# Request Params (key=value format in URL)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | O | Video channel index, starts from 1, default is 1 | `1` |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/pirAlarm.cgi?action=getPirParam&channel=1
```

---

# Response Params (key=value format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| configEx | object[] | R | PIR configuration | |
| +Enable | bool | O | Enable/Disable motion detection feature in channel | `true` |
| +DetectWindow | object(WinNum) | O | Index of detect window | |
| ++Level | int | O | PIR sensitivity level (1–6) | `1` |
| ++Id | int | O | Detect window ID | `1` |
| ++Name | string | O | Detect window name | `xxx` |
| ++Sensitive | int | O | Sensitivity range [0–100], higher value = more sensitive | `2` |
| ++Threshold | int | O | Threshold for triggering motion detect | `2` |
| ++Region | int(LineNum) | O | Region definition bitmap | `[4194303,0,3]` |

---

# Region Description

- Region is divided into:
  - 18 lines
  - 22 blocks per line

- Each line is represented by a 32-bit integer bitmap.

- Bit meanings:
  - `1` → monitored
  - `0` → not monitored

---

# Example Region Definitions

```txt
MotionDetect[0].Region[] = [4194303 (0x3FFFFF)]
```

Motion in line 0's 22 blocks is monitored.

```txt
MotionDetect[0].Region[1] = 0
```

Motion in line 1's 22 blocks is not monitored.

```txt
MotionDetect[0].Region[17] = 3
```

Only first two blocks monitored in last line.

---

# Time Section

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +TimeSection | char[wd][8][] | O | PIR active schedule | `["1 00:00:00-24:00:00", ...]` |

---

# TimeSection Format

```txt
mask hh:mm:ss-hh:mm:ss
```

- `mask`
  - Range: `[0–65535]`
- `hh`
  - Range: `[0–24]`
- `mm`
  - Range: `[0–59]`
- `ss`
  - Range: `[0–59]`

---

# Mask Bit Definitions

| Bit | Meaning |
|---|---|
| Bit0 | Regular record |
| Bit1 | Motion detection record |
| Bit2 | Alarm record |
| Bit3 | Card record |

---

# PIR Linkage Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +PirLink | object | O | PIR linkage settings | |
| ++RecordChannels | int[] | O | Recording channels | `[0,1,0]` |
| ++RecordEnable | bool | O | Enable/Disable record | `true` |
| ++RecordLatch | int | O | Record duration after alarm clear (seconds) | `10` |
| ++AlarmOutChannels | int[] | O | Alarm output channels | `[0,0,1]` |
| ++AlarmOutEnable | bool | O | Enable/Disable alarm out | `true` |
| ++AlarmOutLatch | int | O | Alarm output duration after clear (seconds) | `15` |
| ++SnapshotChannels | int[] | O | Snapshot video channels | `[0,0,1]` |
| ++SnapshotEnable | bool | O | Enable/Disable snapshot | `true` |
| ++Dejitter | int | O | Alarm signal dejitter (0–255 seconds) | `10` |
| ++MailEnable | bool | O | Enable/Disable email send | `true` |
| ++AlarmBellEnable | bool | O | Enable/Disable alarm bell | `true` |
| ++AlarmBellLatch | int | O | Alarm bell duration (seconds) | `10` |
| ++LogEnable | bool | O | Enable/Disable logging | `true` |

---

# Response Example

```txt
configEx[0].Enable=true

configEx[0].PirLink.LightingLink.Enable=true
configEx[0].PirLink.LightingLink.LightLinkType=Flicker
configEx[0].PirLink.LightingLink.FlickerIntervalTime=5
configEx[0].PirLink.LightingLink.LightDuration=10

configEx[0].PirLink.TimeSection[0][0]=1 00:00:00-24:00:00
configEx[0].PirLink.TimeSection[0][1]=0 02:00:00-24:00:00
configEx[0].PirLink.TimeSection[0][2]=0 03:00:00-24:00:00
configEx[0].PirLink.TimeSection[0][3]=0 04:00:00-24:00:00
configEx[0].PirLink.TimeSection[0][4]=0 05:00:00-24:00:00
configEx[0].PirLink.TimeSection[0][5]=0 06:00:00-24:00:00

configEx[0].RecordEnable=true
configEx[0].RecordChannels=[0,1,2]
configEx[0].RecordLatch=10

configEx[0].AlarmOutEnable=true
configEx[0].AlarmOutChannels=[1,4]
configEx[0].AlarmOutLatch=10

configEx[0].SnapshotEnable=true
configEx[0].SnapshotChannels=[2,4]

configEx[0].MailEnable=true
configEx[0].AlarmBellEnable=true
configEx[0].AlarmBellLatch=10

configEx[0].Dejitter=0
configEx[0].LogEnable=true

configEx[0].DetectWindow[0].Level=3
configEx[0].DetectWindow[0].Id=0
configEx[0].DetectWindow[0].Name=Region0
configEx[0].DetectWindow[0].Sensitive=58
configEx[0].DetectWindow[0].Threshold=4
configEx[0].DetectWindow[0].Region[0]=3932160
configEx[0].DetectWindow[0].Region[1]=3932160

...

configEx[0].DetectWindow[1]...
```

---

# Set PIR Parameter

### Request URL

```http
http://<server>/cgi-bin/pirAlarm.cgi?action=setPirParam
```

### Method

```txt
GET
```

---

# Request Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | O | Video channel index, starts from 1, default is 1 | `1` |
| configEx | object[] | O | PIR configuration | |
| +Enable | bool | O | Enable/Disable motion detect feature | `true` |

Other parameters refer to `getPirParam`.

---

# Request Example

```http
http://192.168.1.108/cgi-bin/pirAlarm.cgi?action=setPirParam&channel=1&configEx[1].Enable=true&configEx[1].PirLink.LightingLink.Enable=true...
```

---

# Response Params

```txt
OK
```

---

# Response Example

```txt
OK
```

# 15.10 Industrial Gateway

# 15.10.1 Obtaining Sensor History Data

## Request URL

```http
http://<server>/cgi-bin/api/iotboxComm/getHistoryData
```

## Method

```txt
POST
```

---

# Request Params (JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| condition | object | R | Query criteria | |
| +ChannelName | char[64] | R | Sensor channel name | `"http"` |
| +DeviceName | char[64] | R | Sensor device name | `"box"` |
| +TagName | char[64] | R | Tag name | `"shidu"` |
| +StartTime | char[64] | R | Query start time | `"2023-05-23 18:43:30"` |
| +EndTime | char[64] | R | Query end time | `"2024-05-22 18:43:30"` |
| +TargetType | char[16] | R | Query type: `"Day"`, `"Week"`, `"Month"`, `"Year"` | `"Year"` |

---

# Request Example

```json
{
  "condition": {
    "ChannelName": "http",
    "DeviceName": "box",
    "TagName": "shidu",
    "StartTime": "2023-05-23 18:43:30",
    "EndTime": "2024-05-22 18:43:30",
    "TargetType": "Year"
  }
}
```

---

# Response Params (JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| data | object[] | R | Queried data for the corresponding period | |
| +MaxValue | float | R | Maximum value | `64.5` |
| +MinValue | float | R | Minimum value | `32.4` |
| +AverageValue | float | R | Average value | `48.1` |
| +SystemTime | int64 | R | Database insert timestamp | `1716579323` |
| +MaxValueOccurTime | int64 | R | Maximum value update time | `1766575366` |
| +MinValueOccurTime | int64 | R | Minimum value update time | `1766575423` |

---

# Response Example

```json
{
  "data": [
    {
      "MaxValue": 64.5,
      "MinValue": 32.4,
      "AverageValue": 48.1,
      "SystemTime": 1716579323,
      "MaxValueOccurTime": 1766575366,
      "MinValueOccurTime": 1766575423
    }
  ]
}
```

---

# 15.10.2 Write the Sensor Value

## Request URL

```http
http://<server>/cgi-bin/api/iotboxComm/writeSensorValue
```

## Method

```txt
POST
```

---

# Request Params (JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TagValues | object[16] | R | Tag parameter values array (max 16 tags) | |
| +ChannelName | char[96] | R | Sensor channel name | `"modbus8"` |
| +DeviceName | char[96] | R | Sensor device name | `"xx"` |
| +TagName | char[96] | R | Sensor tag | `"wendu"` |
| +ValueType | uint32 | R | 0=Invalid, 1=Boolean, 2=Float, 3=Integer, 4=Character | `3` |
| +Value | object[5] | R | Tag value array | `[null,null,null,100,null]` |

---

# Value Mapping

| ValueType | Description | Stored In |
|---|---|---|
| 1 | Boolean | `Value[1]` |
| 2 | Floating point | `Value[2]` |
| 3 | Integer | `Value[3]` |
| 4 | Character/String | `Value[4]` |

- String length must not exceed 32.

---

# Request Example

```json
{
  "TagValues": [
    {
      "ChannelName": "modbus8",
      "DeviceName": "xx",
      "TagName": "wendu",
      "ValueType": 3,
      "Value": []
    }
  ]
}
```

---

# Response Params (JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| result | bool | R | Write result | `true` |

---

# Response Example

```json
{
  "result": true
}
```

---

# 15.10.3 Conditionally Subscribing to Sensor Real-Time Data

## Request URL

```http
http://<server>/cgi-bin/api/iotboxComm/attachByCond
```

## Method

```txt
POST
```

---

# Request Params (JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| filter | object[] | R | Sensor channel filter conditions | |
| +ChannelName | char[96] | R | Sensor channel name | `"MoudleBus"` |
| +Devices | object[] | R | Sensors under channel | |
| ++DeviceName | char[96] | R | Sensor name | `"xx"` |
| ++Tags | object[] | O | Sensor measurement tags | |
| +++TagName | char[96] | O | Sensor tag name | `"shidu"` |

---

# Request Example

```json
{
  "filter": [
    {
      "ChannelName": "MoudleBus",
      "Devices": [
        {
          "DeviceName": "xx",
          "Tags": [
            {
              "TagName": "shidu"
            }
          ]
        }
      ]
    }
  ]
}
```

---

# Response Params (multipart:JSON format in body)

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Deviceinfo | object[16] | O | Information on all sensors | |
| +ChannelName | char[64] | R | Sensor channel name | `"MoudleBus"` |
| +ChannelStatusName | char[128] | O | Channel status point name | `"ChannelStatus"` |
| +ChannelStatusValue | double | O | Channel status point value | `1.0` |
| +ChannelControlName | char[128] | O | Channel control point name | `"ChannelControl"` |
| +ChannelControlValue | double | O | Channel control point value | `1.0` |
| +Devices | object[128] | O | Devices under channel | |
| ++DeviceName | char[64] | O | Sensor name | `"xx"` |
| ++DeviceStatusName | char[128] | O | Device status point name | `"DeviceStatus"` |
| ++DeviceStatusValue | double | O | Device status point value | `1.0` |
| ++DeviceControlName | char[128] | O | Equipment control point name | `"DeviceControl"` |
| ++DeviceControlValue | double | O | Equipment control point value | `1.0` |
| ++Tags | object[32] | O | Detection properties of sensor | |
| +++TagName | char[64] | O | Third-party sensor attribute point name | `"shidu"` |
| +++Value | double | O | Third-party sensor attribute value | `30.0` |
| +++Describe | char[64] | O | Description of attribute point | `"shidu"` |
| +++Unity | char[32] | O | Unit of attribute value | `"℃"` |
| +++ValueType | uint32 | O | 0=Invalid, 1=Boolean, 2=Float, 3=Integer, 4=String | `2` |

---

# Alarm Value Definition

| Bit | Meaning |
|---|---|
| Bit1 | Low-low limit alarm |
| Bit2 | Low limit alarm |
| Bit3 | High limit alarm |
| Bit4 | High-high limit alarm |
| Bit13 | Switching value alarm |

---

# Additional Alarm Fields

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +++AlarmValue | uint32 | O | Alarm bitmap | `1` |
| +++QualityStamp | uint32 | O | Quality stamp | `192` |
| +++AlarmName | char[256] | O | Alarm names separated by `|` | `""` |

---

# AlarmName Field Definitions

| Name | Meaning |
|---|---|
| LLNAME | Low-low limit alarm name |
| LONAME | Low limit alarm name |
| HINAME | High limit alarm name |
| HHNAME | High-high limit alarm name |
| ALMNAME | Switching value alarm name |

Combination mode:

```txt
LLNAME | LONAME | HINAME | HHNAME | ALMNAME
```

---

# Response Example

```http
--<boundary>
Content-Type: application/json
Content-Length: <data length>

{
  "Deviceinfo": [
    {
      "ChannelName": "MoudleBus",
      "ChannelStatusName": "ChannelStatus",
      "ChannelStatusValue": 1.0,
      "ChannelControlName": "ChannelControl",
      "ChannelControlValue": 1.0,
      "Devices": [
        {
          "DeviceName": "Device0",
          "DeviceStatusName": "DeviceStatus",
          "DeviceStatusValue": 1.0,
          "DeviceControlName": "DeviceControl",
          "DeviceControlValue": 1.0,
          "Tags": [
            {
              "TagName": "temperature",
              "Describe": "q",
              "Value": 30.0,
              "Unity": "℃",
              "ValueType": 2,
              "AlarmValue": 1,
              "QualityStamp": 192,
              "AlarmName": "||||"
            }
          ]
        }
      ]
    }
  ]
}
--<boundary>
```