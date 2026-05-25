# Dahua Traffic ANPR Measurement API Documentation
What it replaces: Deploying and licensing the Plate Recognizer or OpenALPR software on the Jetson. How Dahua does it: The camera can natively read plates, run OCR, and compare them against an internal database of authorized fleet vehicles.

[Event] Traffic ANPR Measurement: Page 739 (Section 10.1.8)
Insert Traffic BlockList/AllowList Record: Page 751 (Section 10.3.1)
[Event] CarDrivingInOut (Gate vehicle access): Page 470 (Section 9.1.13)

---

# 10.1.8 [Event] Traffic ANPR Measurement

Traffic ANPR Measurement (Vehicle length, width, height, weight, etc.) Event.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | TrafficCarMeasurement |
| Event Action | Pulse |
| Event Index | 0 |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Name | char[128] | R | Event name | "TrafficCarMeasurement1" |
| GroupID | int | O | Event group ID | 123 |
| CountInGroup | int | O | Event count in group | 3 |
| IndexInGroup | int | O | Event index in group, starts from 1 | 1 |
| PTS | double | O | Relative timestamp (ms) | 150.0 |
| UTC | int64 | O | Event time (seconds) | 1670842479 |
| UTCMS | uint32 | O | Event time millisecond part | 123 |
| EventID | uint32 | O | Event ID | 5864 |

---

# Plate Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | O | Plate object info | |
| Object.BoundingBox | int[4] | O | Plate bounding box coordinates remapped to 0–8192 | [3848, 6128, 4280, 6288] |
| Object.MainColor | uint8[4] | O | RGB plate color (R,G,B,A) | [255,255,255,0] |
| Object.Text | char[64] | O | Plate number | "AC00003" |

---

# Vehicle Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Vehicle | object | O | Vehicle object info | |
| Vehicle.BoundingBox | int[4] | O | Vehicle bounding box remapped to 0–8192 | [1341,2451,4513,4135] |
| Vehicle.Category | char[32] | O | Vehicle type | "Bus" |

---

# Trigger Type

| Value | Meaning |
|---|---|
| 0 | Vehicle detector |
| 1 | Radar |
| 2 | Video |

---

# Vehicle Trigger Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TriggerType | enumint | O | Trigger type | 0 |
| TriggerOccur | uint32 | O | 0=enter, 1=leave | 0 |
| Mark | uint | O | Snap frame mark | 10 |
| Source | uint | O | Source of analysis | 5678 |
| FrameSequence | uint | O | Frame sequence | 12345 |
| Lane | int | O | Lane number | 1 |
| RedLightUTC | int64 | O | UTC time of red light | 74874395 |
| Sequence | uint | O | Snap sequence | 1 |
| Speed | uint | O | Vehicle speed (Km/H) | 80 |

---

# Traffic Car Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TrafficCar | object | O | Traffic car info | |
| TrafficCar.RecNo | int | O | Record ID | 1234 |
| TrafficCar.BoundingBox | int[4] | O | Plate bounding box remapped to 0–8192 | [3848,6128,4280,6288] |
| TrafficCar.PlateNumber | char[64] | O | Plate number | "AC00003" |
| TrafficCar.VehicleColor | char[16] | R | Vehicle color | "White" |
| TrafficCar.VehicleColorRGB | uint[4] | O | RGB vehicle color | [0,0,0,0] |
| TrafficCar.VehicleBoundingBox | int[4] | O | Vehicle bounding box remapped to 0–8192 | [1341,2451,4513,4135] |
| TrafficCar.Speed | int | R | Speed (Km/H) | 60 |
| TrafficCar.Event | char[32] | O | Relative event | "TrafficCarMeasurement" |
| TrafficCar.GroupID | int | R | Event group ID | 123 |

---

# Direction Mapping

| Value | Direction |
|---|---|
| 0 | South → North |
| 1 | WestSouth → EastNorth |
| 2 | West → East |
| 3 | WestNorth → EastSouth |
| 4 | North → South |
| 5 | EastNorth → WestSouth |
| 6 | East → West |
| 7 | EastSouth → WestNorth |
| 8 | Unknown |
| 9 | Custom |

---

# Traffic Lane Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| TrafficCar.CountInGroup | int | R | Event count in group | 3 |
| TrafficCar.IndexInGroup | int | R | Event index in group | 1 |
| TrafficCar.Lane | int | O | Lane number | 1 |
| TrafficCar.Direction | uint8 | O | Lane direction | 0 |
| TrafficCar.UTC | int64 | O | Event UTC timestamp | 1670842479 |

---

# Junction Direction

| Value | Meaning |
|---|---|
| Obverse | Obverse direction |
| Reverse | Reverse direction |

---

# Traffic Light State

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Green Light |
| 2 | Red Light |
| 3 | Yellow Light |

---

# Strobe State

| Value | Meaning |
|---|---|
| Close | Closed |
| Auto | Auto Open |
| Manual | Manual Open |

---

# Vehicle Direction

| Value | Meaning |
|---|---|
| Unknown | Unknown |
| Head | Vehicle Head |
| VehBodySide | Vehicle Body |
| Tail | Vehicle Tail |

---

# Seatbelt State

| Value | Meaning |
|---|---|
| WithSafeBelt | Wearing seatbelt |
| WithoutSafeBelt | No seatbelt |

---

# Driver Information

| Name | Type | Description | Example |
|---|---|---|---|
| MainSeat | enumchar[32] | Main seatbelt state | "WithSafeBelt" |
| SubSeat | enumchar[32] | Passenger seatbelt state | "WithoutSafeBelt" |

---

# Plate Information

| Name | Type | Description | Example |
|---|---|---|---|
| id | uint | Related request ID | 347 |
| PlateInfo.FrontPlateNumber | char[64] | Front plate number | "AC00003" |
| PlateInfo.FrontPlateColor | enumchar[32] | Front plate color | "Blue" |
| PlateInfo.BackPlateNumber | char[64] | Rear plate number | "AC00004" |
| PlateInfo.BackPlateColor | char[32] | Rear plate color | "Blue" |

---

# Supported Plate Colors

```txt
Blue
Yellow
White
Black
Green
YellowbottomBlackText
BluebottomWhiteText
BlackBottomWhiteText
ShadowGreen
YellowGreen
Other
Unknown
```

---

# Vehicle Weight Information

| Name | Type | Description | Example |
|---|---|---|---|
| CarWeightInfo | object | Vehicle weight info | |
| AxleNum | uint | Number of axles | 3 |
| AxleWeightInfo | uint[8] | Axle weight array | [3000,5000,8000] |
| AxleLengthInfo | uint[7] | Axle distance array | [3500,1800] |
| OverWeight | uint | Overweight value (kg) | 3000 |
| TotalWeight | uint32 | Total vehicle weight (kg) | 3000 |
| AxisType | uint32 | Vehicle axis type | 11 |
| MeasurementScene | uint32 | Measurement scene | 0 |
| LimitWeight | uint32 | Weight limit (kg) | 50000 |
| LimitWeightPercent | uint32 | Overweight limit percentage | 5 |
| RealWeightPercent | uint32 | Actual overweight percent ×100 | 765 |
| LimitLength | uint32 | Max length (cm) | 800 |
| LimitWidth | uint32 | Max width (cm) | 260 |
| LimitHeight | uint32 | Max height (cm) | 300 |
| Ultralimit | uint8 | Limit state | 1 |
| Speed | uint | Speed (Km/H) | 80 |
| ApplicationScene | uint32 | Application scene | 1 |
| WeightCapturetime | uint32 | Capture time | 123456 |
| WeightGroupid | int | Capture group ID | 123 |
| WeightTime | uint32 | Weight measurement time | 6538920 |

---

# Ultralimit State

| Value | Meaning |
|---|---|
| 0 | Not over limit |
| 1 | Over limit |
| 2 | Exception |

---

# Radar Information

| Name | Type | Description | Example |
|---|---|---|---|
| RadarInfo | object | Radar report information | |
| VehId | uint | Radar vehicle ID | 1 |
| VehLength | uint | Vehicle length (cm) | 4096 |
| VehWidth | uint | Vehicle width (cm) | 4096 |
| VehHeight | uint | Vehicle height (cm) | 4096 |
| VehVolume | uint | Vehicle volume (cm³) | 4096 |
| Lane | uint | Radar lane ID | 1 |
| Dir | int | 1=obverse, -1=reverse, 0=unknown | 1 |
| Time | char[25] | Radar vehicle reach time | "2019-04-01 18:28:00:123" |
| RailingHigh | uint | Vehicle railing height | 0 |
| VehSpeed | uint | Vehicle speed | 60 |
| VehType | uint | Vehicle type | 0 |
| AxisNum | uint | Vehicle axis number | 2 |
| AxisType | uint | Vehicle axis type | 11 |
| VehTopHeight | double | Vehicle top height (cm) | 300.0 |
| VehBottomHeight | double | Vehicle bottom height (cm) | 500.0 |
| EventNo | int64 | Event number | 123 |
| DistanceHead | int32 | Distance to front vehicle (cm) | 50 |
| DistanceTail | int32 | Distance to rear vehicle (cm) | 50 |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=TrafficCarMeasurement
Events[0].EventBaseInfo.Action=Pulse
Events[0].EventBaseInfo.Index=0

Events[0].Name=TrafficCarMeasurement1
Events[0].GroupID=123
Events[0].CountInGroup=3
Events[0].IndexInGroup=1

Events[0].PTS=150.0
Events[0].UTC=1670842479
Events[0].UTCMS=123
Events[0].EventID=5864

Events[0].Object.BoundingBox[0]=3848
Events[0].Object.BoundingBox[1]=6128
Events[0].Object.BoundingBox[2]=4280
Events[0].Object.BoundingBox[3]=6288

Events[0].Object.MainColor[0]=255
Events[0].Object.MainColor[1]=255
Events[0].Object.MainColor[2]=255
Events[0].Object.MainColor[3]=0

Events[0].Object.Text=AC00003

Events[0].Vehicle.BoundingBox[0]=1341
Events[0].Vehicle.BoundingBox[1]=2451
Events[0].Vehicle.BoundingBox[2]=4513
Events[0].Vehicle.BoundingBox[3]=4135

Events[0].Vehicle.Category=Bus

Events[0].TriggerType=0
Events[0].TriggerOccur=0

Events[0].Mark=10
Events[0].Source=5678
Events[0].FrameSequence=12345
Events[0].Lane=1

Events[0].RedLightUTC=74874395
Events[0].Sequence=1
Events[0].Speed=80

Events[0].TrafficCar.RecNo=1234
Events[0].TrafficCar.PlateNumber=AC00003

Events[0].TrafficCar.VehicleColor=White

Events[0].TrafficCar.Speed=60
Events[0].TrafficCar.Event=TrafficCarMeasurement

Events[0].TrafficCar.GroupID=123

Events[0].TrafficCar.Direction=0
Events[0].TrafficCar.UTC=1670842479

Events[0].JunctionDirection=Obverse
Events[0].LightState=2
Events[0].OpenStrobeState=Auto

Events[0].VehicleDirection=Head

Events[0].MainSeat=WithSafeBelt
Events[0].SubSeat=WithoutSafeBelt

Events[0].id=347

Events[0].PlateInfo.FrontPlateNumber=AC00003
Events[0].PlateInfo.FrontPlateColor=Blue

Events[0].PlateInfo.BackPlateNumber=AC00004
Events[0].PlateInfo.BackPlateColor=Blue

Events[0].CarWeightInfo.AxleNum=3
Events[0].CarWeightInfo.AxleWeightInfo[0]=3000
Events[0].CarWeightInfo.AxleWeightInfo[1]=5000
Events[0].CarWeightInfo.AxleWeightInfo[2]=8000

Events[0].CarWeightInfo.AxleLengthInfo[0]=3500
Events[0].CarWeightInfo.AxleLengthInfo[1]=1800

Events[0].CarWeightInfo.OverWeight=3000
Events[0].CarWeightInfo.TotalWeight=3000
Events[0].CarWeightInfo.AxisType=11

Events[0].CarWeightInfo.MeasurementScene=0
Events[0].CarWeightInfo.LimitWeight=50000
Events[0].CarWeightInfo.LimitWeightPercent=5
Events[0].CarWeightInfo.RealWeightPercent=765

Events[0].CarWeightInfo.LimitLength=800
Events[0].CarWeightInfo.LimitWidth=260
Events[0].CarWeightInfo.LimitHeight=300

Events[0].CarWeightInfo.Ultralimit=1

Events[0].CarWeightInfo.Speed=80
Events[0].CarWeightInfo.ApplicationScene=1

Events[0].CarWeightInfo.WeightCapturetime=123456
Events[0].CarWeightInfo.WeightGroupid=123
Events[0].CarWeightInfo.WeightTime=6538920

Events[0].RadarInfo.VehId=1
Events[0].RadarInfo.VehLength=4096
Events[0].RadarInfo.VehWidth=4096
Events[0].RadarInfo.VehHeight=4096
Events[0].RadarInfo.VehVolume=4096

Events[0].RadarInfo.Lane=1
Events[0].RadarInfo.Dir=1

Events[0].RadarInfo.Time=2019-04-01 18:28:00:123

Events[0].RadarInfo.RailingHigh=0
Events[0].RadarInfo.VehSpeed=60
Events[0].RadarInfo.VehType=0

Events[0].RadarInfo.AxisNum=2
Events[0].RadarInfo.AxisType=11

Events[0].RadarInfo.VehTopHeight=300.0
Events[0].RadarInfo.VehBottomHeight=500.0

Events[0].EventNo=123

Events[0].DistanceHead=50
Events[0].DistanceTail=50

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# 10.2 Traffic Flow

---

# 10.2.1 [Event] TrafficFlowStat

When traffic flow triggers the rule, send this event.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | TrafficFlowStat |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| FlowStates | object[] | R | Traffic flow information per lane | |
| Lane | int | R | Lane number, starts from 0 | 0 |
| Flow | int | R | Traffic flow count | 50 |


--- 

# 10.3.1 Insert Traffic BlockList / AllowList Record

---

# Request URL

```http
http://<server>/cgi-bin/recordUpdater.cgi?action=insert
```

---

# Method

```txt
GET
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| name | char[] | R | Record table name. `"TrafficBlackList"` for BlockList and `"TrafficRedList"` for AllowList | TrafficBlackList |
| PlateNumber | char[] | R | Car plate number, max length 31, must be unique | AC00001 |
| MasterOfCar | char[] | O | Car owner, max length 15 | ZhangSan |
| PlateColor | char[] | O | Plate color | Yellow |
| PlateType | char[] | O | Plate type | |
| VehicleType | char[] | O | Vehicle type | |
| VehicleColor | char[] | O | Vehicle color | Blue |
| BeginTime | char[] | O | Begin time | "2010-05-25 00:00:00" |
| CancelTime | char[] | O | Cancel time | "2010-06-25 00:00:00" |
| AuthorityList | object | O | Authority list, only valid for `"TrafficRedList"` | |
| AuthorityList.OpenGate | bool | O | Permission to open gate | true |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/recordUpdater.cgi?action=insert
&name=TrafficBlackList
&PlateNumber=AC00001
&MasterOfCar=ZhangSan
&PlateColor=Yellow
&VehicleColor=Blue
&BeginTime=2011-01-01%2012:00:00
&CancelTime=2011-01-10%2012:00:00
```

---

# Response Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| RecNo | int | R | New record ID, returns -1 if async | 12345 |

---

# Response Example

```txt
RecNo=12345
```

---

# 10.3.2 Update Traffic BlockList / AllowList Record

---

# Request URL

```http
http://<server>/cgi-bin/recordUpdater.cgi?action=update
```

---

# Method

```txt
GET
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| name | char[] | R | `"TrafficBlackList"` for BlockList and `"TrafficRedList"` for AllowList | TrafficBlackList |
| recno | int | R | Record ID | 12345 |
| PlateNumber | char[] | R | Plate number | AC00001 |
| MasterOfCar | char[] | O | Car owner | ZhangSan |
| PlateColor | char[] | O | Plate color | Yellow |
| PlateType | char[] | O | Plate type | |
| VehicleType | char[] | O | Vehicle type | |
| VehicleColor | char[] | O | Vehicle color | Blue |
| BeginTime | char[] | O | Begin time | "2010-05-25 00:00:00" |
| CancelTime | char[] | O | Cancel time | "2010-06-25 00:00:00" |
| AuthorityList | object | O | Only valid for `"TrafficRedList"` | |
| AuthorityList.OpenGate | bool | O | Permission to open gate | true |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/recordUpdater.cgi?action=update
&name=TrafficBlackList
&recno=12345
&PlateNumber=AC00001
&MasterOfCar=ZhangSan
&PlateColor=Yellow
&VehicleColor=Blue
&BeginTime=2011-01-01%2012:00:00
&CancelTime=2011-01-10%2012:00:00
```

---

# Response

```txt
OK
```

---

# 10.3.3 Remove Traffic BlockList / AllowList Record

---

# Request URL

```http
http://<server>/cgi-bin/recordUpdater.cgi?action=remove
```

---

# Method

```txt
GET
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| name | char[] | R | `"TrafficBlackList"` for BlockList and `"TrafficRedList"` for AllowList | TrafficBlackList |
| recno | int | R | Record ID | 12345 |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/recordUpdater.cgi?action=remove
&name=TrafficBlackList
&recno=12345
```

---

# Response

```txt
OK
```

---

# 10.3.4 Find Traffic BlockList / AllowList Record

---

# Request URL

```http
http://<server>/cgi-bin/recordFinder.cgi?action=find
```

---

# Method

```txt
GET
```

---

# Request Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| name | char[] | R | `"TrafficBlackList"` for BlockList and `"TrafficRedList"` for AllowList | TrafficBlackList |
| count | int | O | Max result count, default 1024 | 100 |
| StartTime | string | O | Start of CreateTime range | 123456700 |
| EndTime | string | O | End of CreateTime range | 123456800 |
| condition | object | O | Search condition object | |
| condition.PlateNumber | char[] | O | Exact plate number | AC00001 |
| condition.PlateNumberVague | char[] | O | Partial plate match substring | |
| condition.PlateNumberVagueGroup | char[][] | O | Partial plate substring array | |
| QueryCount | int | O | Query count, default 1000 | 500 |
| QueryResultBegin | int | O | Result start index | 0 |

---

# Request Example

```http
http://192.168.1.108/cgi-bin/recordFinder.cgi?action=find
&name=TrafficBlackList
&condition.PlateNumber=AC00001
&StartTime=123456700
&EndTime=123456800
&count=100
```

---

# Response Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| totalCount | int | O | Total records found | 1000 |
| found | int | O | Returned record count | 100 |
| records | object[] | R | Returned records | |
| records[].RecNo | int | R | Record ID | 12345 |
| records[].CreateTime | int | R | Record creation time | 123456789 |
| records[].PlateNumber | char[] | R | Car plate number | AC00001 |
| records[].MasterOfCar | char[] | O | Car owner | ZhangSan |

---

# Response Example

```txt
totalCount=1000
found=100

records[0].RecNo=12345
records[0].CreateTime=123456789
records[0].PlateNumber=AC00001
records[0].MasterOfCar=ZhangSan

records[1].RecNo=13579
records[1].CreateTime=123456799
records[1].PlateNumber=AC00001
records[1].MasterOfCar=LiSi
```

---

# Table Types

| Table Name | Purpose |
|---|---|
| TrafficBlackList | Vehicle Block List |
| TrafficRedList | Vehicle Allow List |

---

# Notes

## BlockList

```txt
TrafficBlackList
```

Used to:
- deny vehicle access
- trigger alarms
- monitor suspicious vehicles

---

## AllowList

```txt
TrafficRedList
```

Used to:
- whitelist vehicles
- auto open gate/barrier
- parking access control

---

# Example Production Use Cases

## Add Vehicle To AllowList

```http
/cgi-bin/recordUpdater.cgi?action=insert
&name=TrafficRedList
&PlateNumber=LAG123AA
&MasterOfCar=Oluwaseun
&AuthorityList.OpenGate=true
```

---

## Remove Blocked Vehicle

```http
/cgi-bin/recordUpdater.cgi?action=remove
&name=TrafficBlackList
&recno=12345
```

---

## Search Vehicles

```http
/cgi-bin/recordFinder.cgi?action=find
&name=TrafficBlackList
&condition.PlateNumberVague=ABC
```

This matches:
- ABC123
- XXABC
- 12ABC45
```


---

# 9.1.13 [Event] CarDrivingInOut

CarDrivingInOut Event.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | CarDrivingInOut |
| Event Action | Pulse |
| Event Index | 0 |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Name | char[128] | R | Event name | "CarDrivingInOut" |
| GroupID | int | O | Group ID | 123 |
| CountInGroup | int | O | Count in event group | 3 |
| IndexInGroup | int | O | Capture sequence number within group | 1 |
| PTS | double | O | Relative event timestamp in milliseconds | 150.0 |
| UTC | int64 | O | Event occurrence time in seconds | 1465389120 |
| UTCMS | uint32 | O | Event millisecond timestamp | 123 |
| EventID | uint32 | O | Unique event identifier | 5864 |

---

# Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | O | Plate information | Object |
| Vehicle | object | O | Vehicle information | Object |

---

# Frame Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| FrameSequence | int | R | Video analysis frame number | 12345 |
| Sequence | int | R | Capture sequence number | 1 |

---

# Sequence Meaning

```txt
3-2-1-0

1 = Normal end to capture
0 = Abnormal end to capture

Only valid during Stop event.
```

---

# Driving Direction

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Entering |
| 2 | Drive Out |

---

# Driving Direction Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DrivingDirection | enumint | R | Vehicle travel direction | 0 |

---

# Global Scene Image Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| GlobalScene | object | O | Big picture information | |
| GlobalScene.IndexInData | uint | O | Image index in uploaded image data | 0 |
| GlobalScene.Length | uint | R | Image length | 1000 |
| GlobalScene.Offset | uint | R | Image offset | 0 |
| GlobalScene.Width | uint | O | Image width | 100 |
| GlobalScene.Height | uint | O | Image height | 50 |

---

# Parking Image Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ParkingImage | object | O | Parking space cutout information | |
| ParkingImage.IndexInData | uint | O | Image index in uploaded image data | 0 |
| ParkingImage.Length | uint | R | Image length | 1000 |
| ParkingImage.Offset | uint | R | Image offset | 0 |
| ParkingImage.Width | uint | O | Image width | 100 |
| ParkingImage.Height | uint | O | Image height | 50 |

---

# Parking Space Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ParkingNum | char[32] | R | Parking space (geomagnetic) number | "A101" |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=CarDrivingInOut
Events[0].EventBaseInfo.Action=Pulse
Events[0].EventBaseInfo.Index=0

Events[0].Name=CarDrivingInOut
Events[0].GroupID=123
Events[0].CountInGroup=3
Events[0].IndexInGroup=1

Events[0].PTS=150.0
Events[0].UTC=1465389120
Events[0].UTCMS=123
Events[0].EventID=5864

Events[0].FrameSequence=12345
Events[0].Sequence=1

Events[0].DrivingDirection=0

Events[0].GlobalScene.IndexInData=0
Events[0].GlobalScene.Length=1000
Events[0].GlobalScene.Offset=0
Events[0].GlobalScene.Width=100
Events[0].GlobalScene.Height=50

Events[0].ParkingImage.IndexInData=0
Events[0].ParkingImage.Length=1000
Events[0].ParkingImage.Offset=0
Events[0].ParkingImage.Width=100
Events[0].ParkingImage.Height=50

Events[0].ParkingNum=A101

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>
```

---

# Notes

## DrivingDirection

```txt
0 = Unknown
1 = Entering
2 = Drive Out
```

Used for:
- parking entry systems
- vehicle access control
- smart parking management
- parking occupancy analytics

---

# Common Production Use Cases

## Vehicle Entering Parking

```txt
DrivingDirection=1
```

Trigger:
- open barrier
- mark parking occupied
- save entry timestamp

---

## Vehicle Leaving Parking

```txt
DrivingDirection=2
```

Trigger:
- release parking slot
- calculate parking fee
- mark parking available

---

# Binary Multipart Response Structure

The event response uses multipart HTTP format:

```txt
text/plain
```

contains:
- metadata
- event attributes
- parking details

and:

```txt
image/jpeg
```

contains:
- captured vehicle image
- parking snapshot
- scene image
```