Behavioral Analysis & Perimeter Breach
What it replaces: Writing custom Python zone-based logic using ByteTrack to figure out if someone is loitering or crossing a fence line. How Dahua does it: Dahua's Intelligent Video System (IVS) handles spatial triggers internally. You draw the polygon/line on the camera, and it fires a specific HTTP event only when that exact rule is broken.

[Event] CrossLineDetection (Perimeter Breach): Page 441 (Section 9.1.6)
.
[Event] CrossRegionDetection (Entering the compound): Page 443 (Section 9.1.7)
.
[Event] WanderDetection (Loitering for X seconds): Page 435 (Section 9.1.3)



# Dahua Video Analyse Events Documentation

---

# 9.1.6 [Event] CrossLineDetection

When an object crosses the configured line, this event is triggered.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | CrossLineDetection |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

## Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | R | Detected object crossing the line. Future implementations should prefer `Objects`. | |
| Object.BoundingBox | uint16[4] | R | Bounding box coordinates: `[left, top, right, bottom]`. Coordinate range remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Multiple Objects

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Objects | object[] | O | Multiple detected objects. Use first element for single detection if available. | |
| Objects[].BoundingBox | uint16[4] | R | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Detection Line

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DetectLine | int[20][2] | R | Detection line points. First array = point list, second array = `[x,y]`. Coordinate remapped to `0-8192`. | |

---

## Direction

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Direction | string | O | Crossing direction. Possible values: `"LeftToRight"`, `"RightToLeft"`, `"Any"` | `"LeftToRight"` |

---

# Coordinate System

Bounding boxes use:

```txt
[left, top, right, bottom]
```

Coordinate range:

```txt
0 → 8192
```

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=CrossLineDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Object.BoundingBox[0]=4392
Events[0].Object.BoundingBox[1]=4136
Events[0].Object.BoundingBox[2]=6960
Events[0].Object.BoundingBox[3]=6512

Events[0].Objects[0].BoundingBox[0]=4392
Events[0].Objects[0].BoundingBox[1]=4136
Events[0].Objects[0].BoundingBox[2]=6960
Events[0].Objects[0].BoundingBox[3]=6512

Events[0].DetectLine[0][0]=192
Events[0].DetectLine[0][1]=192

Events[0].DetectLine[1][0]=562
Events[0].DetectLine[1][1]=552

Events[0].DetectLine[2][0]=600
Events[0].DetectLine[2][1]=733

Events[0].DetectLine[3][0]=200
Events[0].DetectLine[3][1]=270

...

Events[0].Direction=LeftToRight

...

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# Common Use Cases

## Perimeter Security

Detect when:
- people cross restricted areas
- vehicles cross security lines
- objects move across protected zones

---

## Traffic Monitoring

Detect:
- wrong-way driving
- lane crossing
- restricted road access

---

## Smart Parking

Detect:
- vehicle entry/exit
- barrier crossing
- unauthorized access

---

# Direction Values

| Value | Meaning |
|---|---|
| LeftToRight | Object crossed left to right |
| RightToLeft | Object crossed right to left |
| Any | Trigger on any direction |

---

# Multipart Response Structure

Response contains:

## Metadata

```txt
Content-Type: text/plain
```

Contains:
- event details
- line coordinates
- direction
- object metadata

---

## Image Data

```txt
Content-Type: image/jpeg
```

Contains:
- captured snapshot image
- event evidence frame

---

# 9.1.7 [Event] CrossRegionDetection

Triggered when an object crosses or interacts with a configured region.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | CrossLineDetection |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

## Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | R | Detected object crossing region. Future implementations should prefer `Objects`. | |
| Object.BoundingBox | uint16[4] | R | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Multiple Objects

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Objects | object[] | O | Multiple detected objects. | |
| Objects[].BoundingBox | uint16[4] | R | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Detection Region

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DetectRegion | int[20][2] | R | Detection polygon points. Coordinate remapped to `0-8192`. | |

---

## Action Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Action | char[] | R | Region interaction action. Possible values: `"Appear"`, `"Disappear"`, `"Cross"`, `"Inside"` | `"Disappear"` |

---

## Direction Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Direction | char[] | O | Valid only if Action=`Cross`. Values: `"Enter"`, `"Leave"`, `"Both"` | `"LeftToRight"` |

---

# CrossRegionDetection Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=CrossLineDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Object.BoundingBox[0]=4392
Events[0].Object.BoundingBox[1]=4136
Events[0].Object.BoundingBox[2]=6960
Events[0].Object.BoundingBox[3]=6512

Events[0].Objects[0].BoundingBox[0]=4392
Events[0].Objects[0].BoundingBox[1]=4136
Events[0].Objects[0].BoundingBox[2]=6960
Events[0].Objects[0].BoundingBox[3]=6512

Events[0].DetectRegion[0][0]=192
Events[0].DetectRegion[0][1]=192

Events[0].DetectRegion[1][0]=562
Events[0].DetectRegion[1][1]=552

Events[0].DetectRegion[2][0]=600
Events[0].DetectRegion[2][1]=733

Events[0].DetectRegion[3][0]=200
Events[0].DetectRegion[3][1]=270

...

Events[0].Action=Disappear
Events[0].Direction=LeftToRight

...

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# CrossRegionDetection Actions

| Action | Meaning |
|---|---|
| Appear | Object appeared inside region |
| Disappear | Object disappeared from region |
| Cross | Object crossed region boundary |
| Inside | Object remains inside region |

---

# Direction Values (Cross Action)

| Value | Meaning |
|---|---|
| Enter | Entered region |
| Leave | Left region |
| Both | Detect both directions |

---

# Common Use Cases

## Intrusion Detection

Detect:
- humans entering restricted zones
- vehicles entering secure areas
- unauthorized perimeter access

---

## Smart Retail Analytics

Track:
- customer region occupancy
- heatmap zones
- dwell time

---

## Industrial Safety

Monitor:
- restricted machinery zones
- hazardous areas
- forklift movement regions

---

# Coordinate Notes

All polygon and bounding coordinates are normalized to:

```txt
0 → 8192
```

Convert to actual image pixels using:

```txt
pixelX = (coordX / 8192) * imageWidth
pixelY = (coordY / 8192) * imageHeight
```


# 9.1.7 [Event] CrossRegionDetection

When an object crosses or interacts with a configured region, this event is triggered.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | CrossLineDetection |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

## Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | R | Detected object crossing the region. Future implementations should prefer `Objects`. | |
| Object.BoundingBox | uint16[4] | R | Bounding box coordinates `[left, top, right, bottom]`. Coordinate range remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Multiple Objects

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Objects | object[] | O | Multiple detected objects. Use the first element for single-object detection if available. | |
| Objects[].BoundingBox | uint16[4] | R | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Detection Region

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DetectRegion | int[20][2] | R | Detection region polygon. First array is point list, second array contains `[x,y]`. Coordinates remapped to `0-8192`. | |

---

## Action Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Action | char[] | R | Cross action. Possible values: `"Appear"`, `"Disappear"`, `"Cross"`, `"Inside"` | `"Disappear"` |

---

## Direction Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Direction | char[] | O | Valid only if `Action="Cross"`. Possible values: `"Enter"`, `"Leave"`, `"Both"` | `"LeftToRight"` |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=CrossLineDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Object.BoundingBox[0]=4392
Events[0].Object.BoundingBox[1]=4136
Events[0].Object.BoundingBox[2]=6960
Events[0].Object.BoundingBox[3]=6512

Events[0].Objects[0].BoundingBox[0]=4392
Events[0].Objects[0].BoundingBox[1]=4136
Events[0].Objects[0].BoundingBox[2]=6960
Events[0].Objects[0].BoundingBox[3]=6512

Events[0].DetectRegion[0][0]=192
Events[0].DetectRegion[0][1]=192

Events[0].DetectRegion[1][0]=562
Events[0].DetectRegion[1][1]=552

Events[0].DetectRegion[2][0]=600
Events[0].DetectRegion[2][1]=733

Events[0].DetectRegion[3][0]=200
Events[0].DetectRegion[3][1]=270

...

Events[0].Action=Disappear
Events[0].Direction=LeftToRight

...

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# CrossRegionDetection Actions

| Action | Meaning |
|---|---|
| Appear | Object appeared in region |
| Disappear | Object disappeared from region |
| Cross | Object crossed region boundary |
| Inside | Object remains inside region |

---

# Direction Values

| Value | Meaning |
|---|---|
| Enter | Object entered region |
| Leave | Object left region |
| Both | Detect both directions |

---

# Coordinate Mapping

All coordinates are normalized between:

```txt
0 → 8192
```

Convert to image pixels:

```txt
pixelX = (coordX / 8192) * imageWidth
pixelY = (coordY / 8192) * imageHeight
```

---

# Multipart Response Structure

## Metadata

```txt
Content-Type: text/plain
```

Contains:
- object metadata
- region coordinates
- action type
- direction information

---

## Snapshot Image

```txt
Content-Type: image/jpeg
```

Contains:
- event snapshot
- evidence frame

---

# Common Use Cases

## Intrusion Detection

Detect:
- unauthorized entry
- perimeter violations
- restricted-area access

---

## Smart Parking

Track:
- vehicle region occupancy
- entry/exit areas
- parking violations

---

## Retail Analytics

Analyze:
- customer movement
- occupancy zones
- dwell regions

---

# 9.1.8 [Event] QueueStayDetection

Triggered when queue stay time exceeds configured threshold.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | QueueStayDetection |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

## Object Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Object | object | O | Object staying in queue. Future implementations should prefer `Objects`. | |
| Object.BoundingBox | uint16[4] | O | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Multiple Objects

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Objects | object[] | O | Multiple queued objects. Use first element for single-object detection if available. | |
| Objects[].BoundingBox | uint16[4] | O | Bounding box coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Detection Region

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DetectRegion | int[20][2] | O | Detection region polygon. Coordinates remapped to `0-8192`. | |

---

## Area Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| AreaID | int | O | Area identifier. Starts from `1`. If omitted, default single area is assumed. | `2` |

---

## Preset Information

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| PresetID | int | O | PTZ preset ID. Valid IDs start from `1`. `0` means meaningless/not involved. | `1` |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=QueueStayDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Object.BoundingBox[0]=4392
Events[0].Object.BoundingBox[1]=4136
Events[0].Object.BoundingBox[2]=6960
Events[0].Object.BoundingBox[3]=6512

Events[0].Objects[0].BoundingBox[0]=4392
Events[0].Objects[0].BoundingBox[1]=4136
Events[0].Objects[0].BoundingBox[2]=6960
Events[0].Objects[0].BoundingBox[3]=6512

Events[0].DetectRegion[0][0]=192
Events[0].DetectRegion[0][1]=192

Events[0].DetectRegion[1][0]=562
Events[0].DetectRegion[1][1]=552

Events[0].DetectRegion[2][0]=600
Events[0].DetectRegion[2][1]=733

Events[0].DetectRegion[3][0]=200
Events[0].DetectRegion[3][1]=270

...

Events[0].AreaID=2
Events[0].PresetID=1

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# QueueStayDetection Use Cases

## Crowd Monitoring

Detect:
- long queues
- congestion
- crowd buildup

---

## Retail Queue Analytics

Track:
- checkout wait times
- service delays
- customer congestion

---

## Transportation Systems

Monitor:
- boarding queues
- toll gate congestion
- traffic buildup

---

# Coordinate Mapping

Coordinates are normalized:

```txt
0 → 8192
```

Convert to pixels:

```txt
pixelX = (coordX / 8192) * imageWidth
pixelY = (coordY / 8192) * imageHeight
```

---

# Multipart Response Structure

## Metadata

```txt
Content-Type: text/plain
```

Contains:
- queue information
- detection regions
- area identifiers
- preset identifiers

---

## Snapshot Image

```txt
Content-Type: image/jpeg
```

Contains:
- evidence image
- queue snapshot
```

# 9.1.3 [Event] WanderDetection

Triggered when an object is detected wandering within a configured area.

---

# Event Information

| Field | Value |
|---|---|
| Event Code | WanderDetection |
| Event Action | Start/Stop |
| Event Index | 0 |

---

# Event Data

## Wandering Objects

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Objects | object[] | R | Objects detected as wandering. | |
| Objects[].BoundingBox | uint16[4] | R | Bounding box coordinates `[left, top, right, bottom]`. Coordinates remapped to `0-8192`. | `[2992,1136,4960,5192]` |

---

## Wander Tracks

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| Tracks | int[][20][2] | O | Object wandering tracks represented as polylines. One polyline per object. Each point contains `[x,y]`. Coordinates remapped to `0-8192`. | |

---

## Detection Region

| Name | Type | R/O | Description | Example |
|---|---|---|---|---|
| DetectRegion | int[20][2] | R | Detection region polygon points. First array is point list, second array contains `[x,y]`. Coordinates remapped to `0-8192`. | |

---

# Event Response Example

```txt
--<boundary>
Content-Type: text/plain
Content-Length: <length>

Events[0].EventBaseInfo.Code=WanderDetection
Events[0].EventBaseInfo.Action=Start
Events[0].EventBaseInfo.Index=0

Events[0].Objects[0].BoundingBox[0]=4392
Events[0].Objects[0].BoundingBox[1]=4136
Events[0].Objects[0].BoundingBox[2]=6960
Events[0].Objects[0].BoundingBox[3]=6512

Events[0].Tracks[0][0][0]=23
Events[0].Tracks[0][0][1]=23

Events[0].Tracks[0][1][0]=500
Events[0].Tracks[0][1][1]=401

Events[0].Tracks[0][2][0]=1003
Events[0].Tracks[0][2][1]=192

...

Events[0].DetectRegion[0][0]=192
Events[0].DetectRegion[0][1]=192

Events[0].DetectRegion[1][0]=562
Events[0].DetectRegion[1][1]=552

Events[0].DetectRegion[2][0]=600
Events[0].DetectRegion[2][1]=733

Events[0].DetectRegion[3][0]=200
Events[0].DetectRegion[3][1]=270

...

--<boundary>
Content-Type: image/jpeg
Content-Length: <image size>

<Jpeg image data>

--<boundary>
```

---

# Track Structure

## Tracks Array Layout

```txt
Tracks[objectIndex][pointIndex][coordinate]
```

Where:

| Index | Meaning |
|---|---|
| objectIndex | Target object index |
| pointIndex | Point along movement path |
| coordinate | `0=x`, `1=y` |

---

# Bounding Box Format

```txt
[left, top, right, bottom]
```

Example:

```txt
[2992,1136,4960,5192]
```

---

# Coordinate System

All coordinates are normalized into:

```txt
0 → 8192
```

Convert normalized coordinates to image pixels:

```txt
pixelX = (coordX / 8192) * imageWidth
pixelY = (coordY / 8192) * imageHeight
```

---

# Wander Detection Use Cases

## Security Surveillance

Detect:
- suspicious loitering
- repeated pacing
- unusual behavior patterns

---

## Airport / Terminal Monitoring

Monitor:
- restricted-area wandering
- prolonged passenger movement
- suspicious roaming activity

---

## Smart Retail Analytics

Track:
- customer lingering behavior
- shopping engagement zones
- movement heatmaps

---

# Multipart Event Response Structure

## Metadata Section

```txt
Content-Type: text/plain
```

Contains:
- event metadata
- object bounding boxes
- movement tracks
- region coordinates

---

## Image Section

```txt
Content-Type: image/jpeg
```

Contains:
- captured event snapshot
- evidence frame

---

# Notes

- `Tracks` may contain multiple polyline paths.
- Each object can have independent wandering trajectories.
- Detection regions are polygon-based.
- Bounding boxes and tracks use the same normalized coordinate system.