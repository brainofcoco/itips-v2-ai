Bonus: Advanced Site Safety
Since the ultimate goal is protecting the site and the equipment, Dahua offers environmental AI that the PRD hasn't even factored in yet, available for free natively:

[Event] SmokeDetection: Page 460 (Section 9.1.11)
.
[Event] FireDetection: Page 465 (Section 9.1.12)
.
[Event] WorkClothesDetection: Page 447 (Section 9.1.10)
.

# 9.1.11 [Event] SmokeDetection

## Event Information

| Field | Value |
|---|---|
| Event Code | `SmokeDetection` |
| Event Action | `Start/Stop` |
| Event Index | |
| Event Data | |

---

# Event Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Name | char[128] | O | Event name | `"SmokeDetection1"` |
| +Class | char[16] | O | Event class | `"Normal"` / `"SmokeFire"` |
| +GroupID | int | O | Group ID | `123` |
| +CountingGroup | int | O | Number of captured photos within an event group | `3` |
| +IndexInGroup | int | O | Capture sequence number within event group | `1` |
| +PTS | double | O | Relative event timestamp in milliseconds | `150.0` |
| +UTC | int64 | O | Event occurrence UTC time | `152463285` |
| +UTCMS | uint32 | O | Event time milliseconds | `123` |
| +EventID | uint32 | O | Unique event number | `5864` |
| +RuleID | uint | O | Intelligent event rule number | `1` |
| +Vehicle | VideoAnalyseObject | O | Vehicle information | |
| +Object | VideoAnalyseObject | O | Single smoke point element | |
| +Objects | VideoAnalyseObject[16] | O | Smoke detection point info array | |
| +TriggerType | enumint | O | Trigger type | `0` |
| +Mark | int | O | Used to mark captured frames | `10` |
| +Source | int | O | Video data source address | `5678` |
| +FrameSequence | int | O | Video analysis frame number | `1234` |
| +Sequence | int | O | Capture sequence number | `3` |

---

# TriggerType Enum

| Value | Description |
|---|---|
| 0 | Vehicle inspection device |
| 1 | Radar |
| 2 | Video |

---

# Detection Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +DetectRegion | uint16[20][2] | O | DetectRegion | |
| +Count | int | O | Number of times Count rule violated | `100` |
| +PresetID | uint16 | O | PTZ preset number triggered by event | `1` |

---

# SceneImage Object

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SceneImage | object | O | Panoramic image object | |
| ++IndexInData | uint | O | Image number in upload data | `0` |
| ++Offset | uint | R | Binary image data offset | `100000` |
| ++Length | uint | R | Image size in bytes | `52000` |
| ++Width | uint | O | Image width | `100` |
| ++Height | uint | O | Image height | `50` |
| ++FilePath | char[260] | O | Panoramic image path | `"/var/local/1.jpg"` |
| ++CommInfo | CommInfo | O | Transport expansion information | `null` |

---

# TrafficCar Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +TrafficCar | TrafficCar | O | Traffic expansion info | `null` |

---

# Position Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Position | int32[3] | O | PTZ position array | `[900, -900, 5]` |

---

# Position Format

| Index | Meaning | Range |
|---|---|---|
| 0 | Horizontal coordinate | `[0,3599]` |
| 1 | Vertical coordinate | `[-1800,1800]` |
| 2 | Zoom position | `[0,127]` |

---

# Channel Field Of View

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +CurChannelHFOV | uint32 | O | Horizontal field of view angle | `0` |
| +CurChannelVFOV | uint32 | O | Vertical field of view angle | `0` |

---

# GPS Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +GPS | object | O | GPS coordinates | |
| ++Longitude | int32 | O | Longitude in one millionth degree | `22222` |
| ++Latitude | int32 | O | Latitude in one millionth degree | `33333` |
| ++Altitude | double | O | Height in meters | `66.666` |

---

# Smoke Color

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SmokeColor | enumchar[16][32] | O | Smoke color array | `["White","Black"]` |

---

# SmokeColor Enum

| Value | Meaning |
|---|---|
| White | White smoke |
| Black | Black smoke |
| Red | Red smoke |
| Yellow | Yellow smoke |
| Other | Other smoke |

---

# Additional Event Fields

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +RealUTC | int64 | O | Standard UTC time | `6538920` |
| +MisReport | bool | O | Suspected false alarm flag | `false` |
| +RuleType | char[32] | O | Rule type | `"SmokeDetection"` |

---

# ViolationSnapSource Enum

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Coils |
| 2 | Radar |
| 3 | Video |
| 4 | Video and coil mixing |
| 5 | Video and radar mixing |
| 6 | Video, coil and radar hybrid |
| 7 | Force trigger |
| 8 | Parking lock status |
| 9 | Barrier status |
| 10 | Peripheral status |

---

# DST and TimeZone

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +DSTTune | uint8 | O | Daylight saving flag | `0` |
| +TimeZone | uint8 | O | Time zone index | `8` |

---

# Event Response Example

```http
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=SmokeDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0
Events[0].Name=SmokeDetection1
Events[0].Class=Normal
Events[0].GroupID=123
Events[0].CountingGroup=3
Events[0].IndexInGroup=1
Events[0].PTS=150.0
Events[0].UTC=152463285
Events[0].UTCMS=123
Events[0].EventID=5864
Events[0].RuleID=1
Events[0].Vehicle=
Events[0].Object=
Events[0].Objects[0]=
Events[0].TriggerType=null

Events[0].Mark=10
Events[0].Source=5678
Events[0].FrameSequence=1234
Events[0].Sequence=3
Events[0].DetectRegion[0][0]=
...
Events[0].Count=100
Events[0].PresetID=1

Events[0].SceneImage.IndexInData=0
Events[0].SceneImage.Offset=100000
Events[0].SceneImage.Length=52000
Events[0].SceneImage.Width=100
Events[0].SceneImage.Height=50
Events[0].SceneImage.FilePath=/var/local/1.jpg

Events[0].CommInfo=null
Events[0].TrafficCar=null

Events[0].Position[0]=
...

Events[0].CurChannelHFOV=0
Events[0].CurChannelVFOV=0

Events[0].GPS.Longitude=22222
Events[0].GPS.Latitude=33333
Events[0].GPS.Altitude=66.666

Events[0].SmokeColor[0]=
...

Events[0].RealUTC=6538920
Events[0].MisReport=false
Events[0].RuleType=SmokeDetection
Events[0].ViolationSnapSource=0
Events[0].DSTTune=0
Events[0].TimeZone=8

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

# 9.1.12 [Event] FireDetection

## Event Information

| Field | Value |
|---|---|
| Event Code | `FireDetection` |
| Event Action | `Start/Stop` |
| Event Index | |
| Event Data | |

---

# Event Parameters

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Name | char[128] | O | Event name | `"FireDetection1"` |
| +Class | char[16] | O | Event class | `"Normal"` |
| +GroupID | int | O | Group ID | `123` |
| +CountingGroup | int | O | Number of captured photos within an event group | `3` |
| +IndexInGroup | int | O | Capture sequence number within an event group | `1` |
| +PTS | double | O | Relative event timestamp in milliseconds | `150.0` |
| +UTC | int64 | O | Event occurrence UTC time | `152463285` |
| +UTCMS | uint32 | O | Event time milliseconds | `123` |
| +EventID | uint32 | O | Unique event number | `5864` |
| +Vehicle | VideoAnalyseObject | O | Vehicle information, null if none | |
| +Object | VideoAnalyseObject | O | Objects participating in gathering | |
| +TriggerType | enumint | O | Trigger type | `0` |
| +Mark | int | O | Used to mark captured frames | `10` |
| +Source | int | O | Video data source address | `5678` |
| +FrameSequence | int | O | Video analysis frame number | `1234` |
| +Sequence | int | O | Capture sequence number | `3` |
| +DetectRegion | uint16[20][2] | O | Detection area | |
| +Count | int | O | Number of Count rule violations | `100` |
| +PresetID | uint16 | O | PTZ preset point number | `1` |

---

# TriggerType Enum

| Value | Description |
|---|---|
| 0 | Vehicle inspection device |
| 1 | Radar |
| 2 | Video |

---

# PTZ Position Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Position | int[] | O | Coordinate and magnification of preset points | `[900, -900, 5]` |

## Position Format

| Index | Meaning | Range |
|---|---|---|
| 0 | Horizontal coordinate | `[0,3599]` |
| 1 | Vertical coordinate | `[-1800,1800]` |
| 2 | Zoom position | `[0,127]` |

---

# Distance Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Distance | float[2] | O | IPC fire point coordinates relative to screen center | `[2.00, -3.00]` |

---

# SceneImage Object

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SceneImage | object | O | Panoramic wide-angle view | |
| ++IndexInData | uint | O | Image number in upload data | `0` |
| ++Offset | uint | R | Binary image offset | `100000` |
| ++Length | uint | R | Image size in bytes | `52000` |
| ++Width | uint | O | Image width | `100` |
| ++Height | uint | O | Image height | `50` |
| ++FilePath | char[260] | O | Panoramic image path | `"/var/local/1.jpg"` |
| ++CommInfo | CommInfo | O | Transport expansion information | |

---

# Additional Fields

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +TrafficCar | TrafficCar | O | Traffic expansion information | |
| +MisReport | bool | O | Suspected false alarm flag | `false` |
| +RuleType | char[32] | O | Rule type corresponding to event | `"FireDetection"` |

---

# ViolationSnapSource Enum

| Value | Description |
|---|---|
| 0 | Unknown |
| 1 | Coils |
| 2 | Radar |
| 3 | Video |
| 4 | Video and coil mixing |
| 5 | Mixing video and radar |
| 6 | Video, coil, and radar hybrid |
| 7 | Force trigger |
| 8 | Parking lock status |
| 9 | Barrier status |
| 10 | Peripheral status |

---

# System Fields

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +RuleID | uint32 | O | Intelligent event rule number | `1` |
| +DSTTune | uint8 | O | Daylight saving time flag | `0` |
| +TimeZone | uint8 | O | Time zone index | `8` |
| +RealUTC | int64 | O | Standard UTC time | `1678151817` |

---

# Event Response Example

```http
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=FireDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Name=FireDetection1
Events[0].Class=Normal
Events[0].GroupID=123
Events[0].CountingGroup=3
Events[0].IndexInGroup=1
Events[0].PTS=150.0
Events[0].UTC=152463285
Events[0].UTCMS=123
Events[0].EventID=5864

Events[0].Vehicle=
Events[0].Object=
Events[0].TriggerType=0

Events[0].Mark=10
Events[0].Source=5678
Events[0].FrameSequence=1234
Events[0].Sequence=3

Events[0].DetectRegion[0][0]=
...

Events[0].Count=100
Events[0].PresetID=1

Events[0].Position[0]=
...

Events[0].Distance[0]=
...

Events[0].SceneImage.IndexInData=0
Events[0].SceneImage.Offset=100000
Events[0].SceneImage.Length=52000
Events[0].SceneImage.Width=100
Events[0].SceneImage.Height=50
Events[0].SceneImage.FilePath=/var/local/1.jpg

Events[0].CommInfo=
Events[0].TrafficCar=

Events[0].MisReport=false
Events[0].RuleType=FireDetection
Events[0].ViolationSnapSource=0

Events[0].RuleID=1
Events[0].DSTTune=0
Events[0].TimeZone=8
Events[0].RealUTC=1678151817

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```




# 9.1.10 [Event] WorkClothesDetection

PPE (safety helmet, work clothes, work pants and mask) detection is used for construction sites or safety production detection. Original images (images taken at the current preset) and human body cutouts can be reported when snapshots are taken.

| Field | Value |
|---|---|
| Event Code | `WorkClothesDetection` |
| Event Action | `Start/Stop` |
| Event Index | Video Channel No. |

---

# Event Data

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Name | char[128] | R | Event name | `"WorkClothesDetection1"` |
| +Class | char[16] | O | Category of intelligence event | `"OperateMonitor"` or `"ProtectiveSuit"` |
| +Type | char[16] | O | Alarm rule type | `"Helmet"` |
| +ObjectID | uint | R | Object ID | `12345` |
| +UTC | int64 | R | UTC event time | `1465389120` |
| +UTCMS | uint32 | R | Event time in milliseconds | `123` |
| +EventID | uint32 | O | Unique event ID | `5864` |
| +RuleID | uint | R | Rule ID | `1` |
| +SourceID | char[32] | O | Source ID of object/image | `"022019030714003000001"` |

---

# Type Enum

| Value | Meaning |
|---|---|
| Helmet | Safety helmet |
| Clothes | Work clothes |
| WorkPants | Work pants |
| ProtectiveSuit | Protective clothes |
| ShoesCover | Shoe cover |
| SafetyRope | Safety rope |
| NormalHat | Normal hat |
| Mask | Face mask |
| Apron | Apron |
| Glove | Gloves |
| Boot | Boots |
| NoHat | No hat |
| Prohelmet | Protective mask |
| FireProofClothes | Fire-resistant clothing |
| Uniform | Uniform |
| Multimeter | Multimeter |
| BreathingMask | Breathing mask |
| Glasses | Glasses |
| Vest | Reflective clothes |
| WristGuard | Wrist guard |
| SafetyShoes | Safety shoes |

---

# Group Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +GroupID | int | O | Event group ID | `123` |
| +CountingGroup | int | O | Number of snapshots in event group | `1` |
| +IndexInGroup | int | O | Snapshot number in group | `1` |

---

# HumanImage Object

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +HumanImage | object | O | Human body image information | |
| ++IndexInData | uint | O | Image number in uploaded image data | `0` |
| ++Offset | uint | O | Offset in binary data block | `0` |
| ++Length | uint | O | Image size in bytes | `100000` |
| ++Width | uint | O | Image width | `1920` |
| ++Height | uint | O | Image height | `1080` |

---

# SceneImage Object

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SceneImage | object | O | Panoramic wide-angle image | |
| ++IndexInData | uint | O | Image number in uploaded image data | `0` |
| ++Offset | uint | O | Offset in binary data block | `100000` |
| ++Length | uint | O | Image size in bytes | `52000` |
| ++Width | uint | O | Image width | `1920` |
| ++Height | uint | O | Image height | `1080` |

---

# Helmet Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Helmet | object | R | Safety helmet attributes | |
| ++HasHelmet | enumint8 | R | Wear safety helmet or not | `2` |
| ++HelmetColor | ColorEnum | O | Safety helmet color | `"Red"` |
| ++HelmetFlag | uint8 | O | Alarm upload ID | `1` |
| ++ReportFlag | uint8 | O | Alarm upload ID | `1` |
| ++HasLegalHat | enumint8 | O | Safety helmet detection result | `0` |

## HasHelmet Enum

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Without safety helmet |
| 2 | With safety helmet |
| 3 | Helmet color does not exist |

---

# Clothes Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Clothes | object | O | Work/service clothing attributes | |
| ++HasClothes | enumint8 | R | Wear work clothes or not | `2` |
| ++HasLegalClothes | enumint8 | R | Wear required work clothes or not | `2` |
| ++ClothesColor | ColorEnum | O | Work clothes color | `"Red"` |

---

# Linked PPE Database Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++LinkGroupName | char[128] | O | PPE database name | `"Group1"` |
| ++LinkGroupID | char[128] | O | PPE database ID | `"1"` |
| ++CutoutPolicy | uint32 | O | Optimization scheme | `0` |

## CutoutPolicy Enum

| Value | Meaning |
|---|---|
| 0 | Full body |
| 1 | Upper body |

---

# LinkGroupInfo

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| ++LinkGroupInfo | object[16] | O | PPE detection database linked to alarm | |
| +++GroupID | char[128] | O | PPE detection database ID | `"123"` |
| +++FeatureName | char[128] | O | Feature name | `"feature_1"` |
| +++Similarity | uint8 | O | Similarity value | `22` |
| +++SampleAttributes | enumint | O | Sample attributes | `0` |
| +++GroupName | char[128] | O | PPE detection database name | `"Group1"` |

## SampleAttributes Enum

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Positive sample |
| 2 | Negative sample |

---

# WorkPants Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +WorkPants | object | O | Work pants attributes | |
| ++HasPants | enumint8 | R | Wear work pants or not | `2` |
| ++PantsColor | ColorEnum | O | Work pants color | `"Red"` |

---

# AlarmType Enum

| Value | Meaning |
|---|---|
| 0 | Unknown |
| 1 | Protective clothes are not compliant |
| 2 | Work clothes are not compliant |
| 3 | Safety helmet not compliant |
| 4 | Safety helmet and work clothes not compliant |

---

# SafetyRope Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SafetyRope | object | O | Safety rope attributes | |
| ++CompliantType | enumint8 | R | Whether safety rope is compliant | `1` |

## CompliantType Enum

| Value | Meaning |
|---|---|
| 0 | Noncompliant |
| 1 | Compliant |

---

# HardHat Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +HardHat | object | O | Helmet attributes | |
| ++HasHat | enumint8 | R | Wear safety helmet or not | `2` |
| ++HatColor | ColorEnum | O | Hat color | `"Red"` |

---

# Ushanka Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Ushanka | object | O | Winter hat attributes | |
| ++HasHat | enumint8 | R | Wear winter hat or not | `2` |
| ++HatColor | ColorEnum | O | Hat color | `"Red"` |

---

# Prohelmet Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Prohelmet | object | O | Protective mask attributes | |
| ++HasHat | enumint8 | R | Wear protective mask or not | `2` |
| ++HatColor | ColorEnum | O | Hat color | `"Red"` |

---

# NormalHat Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +NormalHat | object | O | General hat attributes | |
| ++HasHat | enumint8 | O | Wear general hat or not | `0` |
| ++HasLegalHat | enumint8 | O | Hat detection results | `0` |

---

# Mask Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Mask | object | O | Face mask attributes | |
| ++HasMask | enumint8 | O | Wear face mask or not | `0` |
| ++HasLegalMask | enumint8 | O | Face mask detection results | `0` |

---

# Apron Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Apron | object | O | Apron attributes | |
| ++HasApron | enumint8 | O | Wear apron or not | `0` |
| ++HasLegalApron | enumint8 | O | Apron detection results | `0` |

---

# Glove Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Glove | object | O | Gloves attributes | |
| ++HasGlove | enumint8 | O | Wear gloves or not | `0` |
| ++HasLegalGlove | enumint8 | O | Gloves detection results | `0` |

---

# Boot Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Boot | object | O | Boots attributes | |
| ++HasBoot | enumint8 | O | Wear boots or not | `0` |
| ++HasLegalBoot | enumint8 | O | Boots detection results | `0` |

---

# ShoesCover Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +ShoesCover | object | O | Shoe cover attributes | |
| ++HasCover | enumint8 | O | Wear shoe covers or not | `0` |
| ++HasLegalCover | enumint8 | O | Shoe cover detection results | `0` |

---

# NoHat Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +NoHat | object | O | No-hat attributes | |
| ++HasHat | enumint8 | O | Wear hat or not | `0` |
| ++HasLegalHat | enumint8 | O | No-hat detection results | `0` |

---

# ProtectiveSuit Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +ProtectiveSuit | object | O | Protective suit attributes | |
| ++HasProtectiveSuit | enumint8 | O | Wear protective suit or not | `2` |
| ++ProtectiveSuitColor | ColorEnum | O | Protective suit color | `"Red"` |

---

# FireProofClothes Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +FireProofClothes | object | O | Fire-resistant clothing attributes | |
| ++HasFireProofClothes | enumint8 | O | Wear fire-resistant clothing or not | `2` |
| ++FireProofClothesColor | ColorEnum | O | Fire-resistant clothing color | `"Red"` |

---

# Additional Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +DetectRegion | uint16[20] | O | Detection area | |
| +Objects | VideoAnalyseObject[] | O | Detected object information | `[...]` |
| +Uniform | object | O | Work uniform attributes | |
| ++HasUniform | enumint8 | O | Wear work uniform or not | `1` |
| ++UniformColor | ColorEnum | O | Work uniform color | `"Red"` |

---

# Multimeter Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Multimeter | object | O | Multimeter attributes | |
| ++HasMultimeter | enumint8 | O | Take multimeter or not | `0` |
| ++HasLegalHat | HasLegalHat | O | Multimeter detection result | `0` |

---

# BreathingMask Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +BreathingMask | object | O | Breathing mask attributes | |
| ++HasBreathingMask | enumint8 | O | Wear breathing mask or not | `0` |
| ++HasLegalBreathingMask | enumint8 | O | Breathing mask detection result | `0` |

---

# Glasses Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Glasses | object | O | Glasses attributes | |
| ++GlassesType | enumint | O | Glasses type | `0` |
| ++GlassesLegalMask | int | O | Glasses detection result | `0` |

## GlassesType Enum

| Value | Meaning |
|---|---|
| 0 | No glasses |
| 1 | Sunglasses |
| 2 | Black-rimmed glasses |
| 3 | Half-rimmed glasses |
| 4 | Rimless glasses |
| 5 | General glasses |
| 6 | Industrial goggles |

---

# LegalAlarmType

| Value | Meaning |
|---|---|
| 0 | Alarm triggered when non-compliant items detected |
| 1 | Alarm triggered when all items compliant |

---

# SafeBelt Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SafeBelt | object | O | Seatbelt attributes | |
| ++HasSafeBelt | enumint8 | O | Wear seatbelt or not | `0` |
| ++HasLegalSafeBelt | enumint8 | O | Seatbelt detection result | `0` |

---

# Vest Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Vest | object | O | Safety vest attributes | |
| ++HasVest | enumint8 | O | Wear safety vest or not | `0` |
| ++HasLegalVest | enumint8 | O | Safety vest detection result | `0` |

---

# SafetyShoes Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +SafetyShoes | object | O | Safety shoes attributes | |
| ++HasSafetyShoes | enumint8 | O | Wear safety shoes or not | `0` |
| ++HasLegalSafetyShoes | enumint8 | O | Safety shoes detection result | `0` |

---

# WristGuard Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +WristGuard | object | O | Wrist guard attributes | |
| ++HasWristGuard | enumint8 | O | Wear wrist guard or not | `0` |
| ++HasLegalWristGuard | enumint8 | O | Wrist guard detection result | `0` |

---

# Hood Attributes

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| +Hood | object | O | Head cover attributes | |
| ++HasLegalHood | int32 | O | Head cover detection result | `0` |

---

# Event Response Example

```http
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=WorkClothesDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Name=WorkClothesDetection1
Events[0].Class=OperateMonitor
Events[0].Type=Helmet
Events[0].ObjectID=12345

Events[0].UTC=1465389120
Events[0].UTCMS=123
Events[0].EventID=5864
Events[0].RuleID=1

Events[0].SourceID=022019030714003000001

Events[0].GroupID=123
Events[0].CountingGroup=1
Events[0].IndexInGroup=1

Events[0].HumanImage.IndexInData=0
Events[0].HumanImage.Offset=0
Events[0].HumanImage.Length=100000
Events[0].HumanImage.Width=1920
Events[0].HumanImage.Height=1080

Events[0].SceneImage.IndexInData=0
Events[0].SceneImage.Offset=100000
Events[0].SceneImage.Length=52000
Events[0].SceneImage.Width=1920
Events[0].SceneImage.Height=1080

Events[0].Helmet.HasHelmet=2
Events[0].Helmet.HelmetColor=Red

Events[0].Clothes.HasClothes=2
Events[0].Clothes.HasLegalClothes=2
Events[0].Clothes.ClothesColor=Red

Events[0].WorkPants.HasPants=2
Events[0].WorkPants.PantsColor=Red

Events[0].AlarmType=1

Events[0].ProtectiveSuit.HasProtectiveSuit=2
Events[0].ProtectiveSuit.ProtectiveSuitColor=Red

Events[0].FireProofClothes.HasFireProofClothes=2
Events[0].FireProofClothes.FireProofClothesColor=Red

Events[0].Uniform.HasUniform=1
Events[0].Uniform.UniformColor=Red

Events[0].BreathingMask.HasBreathingMask=0
Events[0].BreathingMask.HasLegalBreathingMask=0

Events[0].Glasses.GlassesType=0

Events[0].SafeBelt.HasSafeBelt=0
Events[0].Vest.HasVest=0
Events[0].SafetyShoes.HasSafetyShoes=0
Events[0].WristGuard.HasWristGuard=0

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```