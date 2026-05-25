Active Deterrence

What it replaces: Building custom logic to fire the external horn speaker/strobe. How Dahua does it: Dahua's "TiOC" (Three-in-One) cameras feature built-in red/blue strobes and 110dB speakers. The camera can fire these autonomously the exact millisecond a human crosses a digital tripwire, or you can trigger them via the API


Control White Light or Speaker: Page 411 (Section 8.5.1)
.
[Config] Configuring Lighting: Page 402 (Section 8.3.2)
.
[Config] Configuring Light Schemes: Page 408 (Section 8.3.4)
.
[Config] Traffic Strobe Setting: Page 764 (Section 10.4.8)
.


# 8.5 Coaxial Control IO

---

# 8.5.1 Control White Light or Speaker

Send commands for controlling the white light and speaker.

## Request

### Request URL

```http
http://<server>/cgi-bin/coaxialControlIO.cgi?action=control
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | O | Video channel number for white light and speaker. Default is 0. | 1 |
| info | object[] | R | Operation details | |
| +Type | int | R | Operation type:<br>1 = White light<br>2 = Speaker | 1 |
| +IO | int | R | Switch:<br>1 = On<br>2 = Off | 1 |
| +TriggerMode | int | R | Trigger mode:<br>1 = Linked trigger<br>2 = Manual trigger | 2 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/coaxialControlIO.cgi?action=control&channel=1&info[0].Type=1&info[0].IO=1&info[0].TriggerMode=2
```

---

## Response Parameters

```txt
OK in body
```

---

## Response Example

```txt
OK
```

---

# 8.5.2 Get White Light and Speaker Status

Get the current status of the white light and speaker.

## Request

### Request URL

```http
http://<server>/cgi-bin/coaxialControlIO.cgi?action=getstatus
```

### Method

```txt
GET
```

---

## Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| channel | int | O | Video channel number starting from 1. Default is 1. | 1 |

---

## Request Example

```http
http://192.168.1.108/cgi-bin/coaxialControlIO.cgi?action=getstatus&channel=1
```

---

# Response Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| status | object | R | Returned status information | |
| +whitelight | char[4] | R | White light status: `"on"` or `"off"` | `"on"` |
| +speaker | char[4] | R | Speaker status: `"on"` or `"off"` | `"on"` |

---

## Response Example

```txt
status.whitelight=on
status.speaker=on
```


# 8.3.2 [Config] Configuring Lighting

Illuminator configuration is used by IPC/SD production line. The illumination effect depends on the type of installed illuminator.

Usage constraints:

- The illuminator type of the device is unique.
- Only IR light, white light, or other types of lights may exist.
- If there are many different types of lights, only the `Mode` field of this configuration is used.
- Other fields are not used.
- Schedule control requirements were added later for white light.
- Lighting configuration was expanded from one element to four elements.
- Element `0` is the second-dimension indication that takes effect immediately.
- If element `0` is modified, it responds immediately.
- When `SupportByTime` capability is true, schedule configuration is supported.
- Elements `1`, `2`, and `3` represent schedule-independent reserved configuration.
- Compatibility rule:
  - If `Lighting[0]` is modified, it takes effect immediately.
  - If `Lighting[0]` is not modified, lights are controlled by schedule.
- Subscripts `0` and `123` are not synchronized with each other.
- Doorbell products use `Mode` field to control illuminators.
- `"SmartLight"` and `"Off"` modes are used.

---

# Config Data Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Lighting | object[][] | O | 2D array. First dimension corresponds to video input channels. Second dimension represents lighting configurations. | |
| +Mode | char[32] | O | Lighting mode. | `"ZoomPrio"` |

---

# Mode Values

| Mode | Description |
|---|---|
| Manual | Manually control brightness and angle. |
| Auto | Automatically control brightness and angle. |
| Off | Turn off the light. |
| ZoomPrio | Zoom priority mode. |
| Timing | Schedule mode. |
| SmartLight | Smart light mode (used by PTZ cameras). |
| ExclusiveManual | Multiple lights supported, used only in manual mode. |
| ForceOn | Force light on continuously. |

---

# ZoomPrio Notes

If `LightingControl` takes effect:

- `LightingZoomPrio` configuration takes effect.
- Otherwise `LightingZoomPrio` configuration does not take effect.

---

# Additional Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Correction | int | O | Light compensation in zoom priority mode. Range: 0–100. Recommended: 0–100. | 2 |
| +Sensitive | int | O | Light sensitivity in zoom priority mode. Range: 0–5. Default: 3 | 3 |
| +Times | int | O | Turn-on duration in auto mode. Unit: seconds. | 30 |

---

# Near Light Configuration

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +NearLight | object[] | O | Near light group. | |
| ++Light | int | O | Brightness percentage `(1–100)`, `0` = off. | 0 |
| ++Angle | int | O | Normalized laser angle. Range `0–100`. | 50 |

---

# Middle Light Configuration

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +MiddleLight | object[] | O | Middle light group. | |
| ++Enable | bool | O | Enable switch. | true |
| ++Light | int | O | Brightness percentage `(1–100)`, `0` = off. | 0 |
| ++Angle | int | O | Normalized laser angle. Range `0–100`. | 50 |

---

# Far Light Configuration

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +FarLight | object[] | O | Far light group. | |
| ++Light | int | O | Brightness percentage `(1–100)`, `0` = off. | 0 |
| ++Angle | int | O | Normalized laser angle. Range `0–100`. | 50 |

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=Lighting
```

---

# Get Config Response Example

```txt
table.Lighting[0][0].Correction=0
table.Lighting[0][0].FarLight[0].Light=50
table.Lighting[0][0].FarLight[0].Angle=50
table.Lighting[0][0].Mode=ZoomPrio
table.Lighting[0][0].NearLight[0].Angle=50
table.Lighting[0][0].NearLight[0].Light=50
table.Lighting[0][0].Sensitive=3
```

---

# Set Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=setConfig&Lighting[0][0].Mode=Manual
```

---

# Set Config Response Example

```txt
OK
```

# 8.3.4 [Config] Configuring Light Schemes

---

# Config Data Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| LightingScheme | object[][] | O | Light scheme configuration. 2D array where first dimension corresponds to video input channels and second dimension represents different lighting configurations. | |
| +LightingMode | enumchar[16] | O | Light scheme mode. | `"MixMode"` |
| +SchemeSchedule | object | O | Schedule configuration for switching lighting schemes by period. | |

---

# LightingMode Values

| Value | Description |
|---|---|
| MixMode | Mixed light scheme |
| WhiteMode | White light scheme |
| NormalMode | Soft and dual lights unsupported |
| InfraredMode | IR scheme |
| AIMode | AI scheme |
| Off | Night vision disabled |

---

# SchemeSchedule Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++Enable | bool | O | Enable or disable schedule. `true` = active, `false` = inactive. | `true` |
| ++TimeSectionByWeek | char[7][6][36] | O | Weekly time schedule configuration. | `[["05:40:00-18:20:00 WhiteMode", ...]]` |

---

# TimeSectionByWeek Notes

- 2D schedule array:
  - First dimension = 7 days of week
    - `0 = Sunday`
    - `1 = Monday`
    - ...
    - `6 = Saturday`
  - Second dimension = time periods of a day
    - Maximum `6` periods per day
- Time periods cannot overlap.
- Total configured periods per day must equal 24 hours.

---

# Example Time Period

```txt
05:40:00-18:20:00 WhiteMode
```

Meaning:

- `05:40:00` → Start time
- `18:20:00` → End time
- `WhiteMode` → Lighting scheme applied during that time

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=LightingScheme
```

---

# Get Config Response Example

```txt
table.LightingScheme[0][0].LightingMode=AIMode
```

---

# Set Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=setConfig&LightingScheme[0][0].LightingMode=InfraredMode
```

---

# Set Config Response Example

```txt
OK
```

# 10.4.8 [Config] Traffic Strobe Setting

---

# Config Data Params

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TrafficStrobe | object[] | O | Traffic strobe setting | |
| +Enable | bool | O | Enable or disable traffic strobe | `true` |
| +ControlType | enumchar[32] | O | Strobe control type | `["TrafficTrustList","Order"]` |

---

# ControlType Values

| Value | Description |
|---|---|
| TrafficTrustList | Control strobe by trust list, open strobe only when car is in trust list |
| AllSnapCar | Open strobe for all snapped cars |
| Order | Open strobe by platform order |
| SpecialCar | Open strobe when car is special |
| NewEnergyCar | Open strobe when vehicle is new energy car |

---

# Additional Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +AllSnapCar | char[32][8] | O | All snap car types | `["Plate","NoPlate"]` |
| +OrderIP | char[40] | O | Platform IP address allowed to send order to open strobe | `"172.32.1.52"` |
| +EnableOfflineSolution | bool | O | Enable open strobe when offline | `false` |
| +OrderIPDisconnect | enumchar[32] | O | Offline strobe control strategy | `["TrafficTrustList","NewEnergyCar"]` |
| +EventHandler | EventHandler | O | Event handling parameters when opening strobe | |
| +EventHandlerClose | EventHandler | O | Event handling parameters when closing strobe | |
| +StationaryOpen | object | O | Always-open setting for high traffic flow | |

---

# AllSnapCar Values

| Value | Description |
|---|---|
| Plate | Snap cars with plate |
| NoPlate | Snap cars without plate |

If not configured, all snapped vehicles are included.

---

# OrderIPDisconnect Values

| Value | Description |
|---|---|
| TrafficTrustList | Open strobe only for trust list vehicles |
| AllSnapCar | Open strobe for all snapped cars |
| SpecialCar | Open strobe for special cars |
| AlwaysOpen | Always keep strobe open |
| NewEnergyCar | Open strobe for new energy cars |

---

# StationaryOpen Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++Enable | bool | O | Enable always-open mode | `true` |
| ++TimeSchedule | TimeSchedule | O | Always-open time schedule | `TimeSchedule` |

---

# ForbidOpen Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++Enable | bool | O | Enable forbidden-open mode | `false` |
| ++TimeSchedule | TimeSchedule | O | Forbidden-open time schedule | `TimeSchedule` |

---

# ForbidNotice Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++Enable | bool | O | Enable forbidden-open notification | `true` |
| +++NoticeString | char[128] | O | Notification message for forbidden open period | `"The strobe is in forbidden open time section"` |

---

# Get Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=getConfig&name=TrafficStrobe
```

---

# Get Config Response Example

```txt
table.TrafficStrobe[0].AllSnapCar[0]=Plate
table.TrafficStrobe[0].AllSnapCar[1]=NoPlate
table.TrafficStrobe[0].ControlType[0]=TrafficTrustList
table.TrafficStrobe[0].ControlType[1]=Order
table.TrafficStrobe[0].Enable=false
table.TrafficStrobe[0].EnableOfflineSolution=true

table.TrafficStrobe[0].EventHandler.AlarmOutChannels[0]=0
table.TrafficStrobe[0].EventHandler.AlarmOutLatch=1

table.TrafficStrobe[0].EventHandlerClose.AlarmOutChannels[0]=1
table.TrafficStrobe[0].EventHandlerClose.AlarmOutLatch=1

table.TrafficStrobe[0].ForbidOpen.Enable=false
table.TrafficStrobe[0].ForbidNotice.Enable=false
table.TrafficStrobe[0].ForbidNotice.NoticeString=

table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][0]=1 07:17:30-13:26:07
table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][1]=0 00:00:00-23:59:59
table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][2]=0 00:00:00-23:59:59
table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][3]=0 00:00:00-23:59:59
table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][4]=0 00:00:00-23:59:59
table.TrafficStrobe[0].ForbidOpen.TimeSchedule[0][5]=0 00:00:00-23:59:59

table.TrafficStrobe[0].OrderIP=
table.TrafficStrobe[0].OrderIPDisconnect[0]=TrafficTrustList

table.TrafficStrobe[0].StationaryOpen.Enable=false

table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][0]=1 10:37:40-12:52:26
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][1]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][2]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][3]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][4]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[0][5]=0 00:00:00-23:59:59

table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][0]=1 09:30:34-12:28:39
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][1]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][2]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][3]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][4]=0 00:00:00-23:59:59
table.TrafficStrobe[0].StationaryOpen.TimeSchedule[1][5]=0 00:00:00-23:59:59
```

---

# Set Config Request Example

```http
http://10.0.0.8/cgi-bin/configManager.cgi?action=setConfig&TrafficStrobe[0].StationaryOpen.Enable=true&TrafficStrobe[0].StationaryOpen.TimeSchedule[0][0]=1 10:00:00-23:59:59
```

---

# Set Config Response Example

```txt
OK
```