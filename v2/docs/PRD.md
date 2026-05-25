
ITIPS — Intelligent Telecom Infrastructure Protection System
Internal Product Requirements Document
Version 2.5 | May 2026 | STRICTLY CONFIDENTIAL 
Prepared by: Seismic Digital & Innovations Limited
Document Authority: This document is the authoritative technical specification for the ITIPS system. All design decisions, architectural choices, and implementation approaches must conform to the requirements stated here. Where a requirement is marked MANDATORY, no deviation is permitted without written approval from the ITIPS Programme Director. Where a requirement is marked HIGH, deviation requires documented justification. Where a requirement is marked DESIRED, the team has discretion to implement or defer based on capacity.

HOW TO READ THIS DOCUMENT
This document is structured in reading order. Every person on the ITIPS team, regardless of discipline, must read Sections 1 and 2 in full before proceeding to their specialist section. Section 1.4 (Team Roles and Boundaries) is mandatory for every engineer on the project without exception. It defines who owns what and where one team’s work ends and another’s begins.
Everyone reads: Sections 1 and 2, especially Section 1.4 (Team Roles and Boundaries)
Dashboard and frontend team reads: Section 3, all five access tiers now have full requirement specifications
Backend and platform engineers read: Section 4. Section 4.7 (Jetson-to-Backend API Contract) is the critical interface document both backend and AI teams must agree on before any implementation begins
AI engineers read: Section 4.7 (API Contract) AND Section 5. The AI team is the client on every outbound API call; understand the contract before building
Hardware and integration engineers read: Section 6
Critical note for AI engineers: Section 5.1 contains a mandatory note on the current development platform (Jetson Orin — now active). The primary video pipeline is GStreamer with CUDA acceleration, not DeepStream. DeepStream has been removed from the ITIPS stack. Read Section 5.1 before building any pipeline. The AI team no longer owns outbound API calls to the cloud, that responsibility has moved to the Jetson Sync Agent owned by the backend team. Read Section 1.4 carefully before starting any implementation.
The AI section deliberately recommends existing open-source and commercial software rather than building from scratch. These recommendations are mandatory starting points. The AI team must evaluate each recommended tool against ITIPS requirements and document their final selection with justification before beginning integration.

SECTION 1 — PURPOSE AND SCOPE
1.1 What ITIPS Is
ITIPS is an AI-powered critical infrastructure protection platform deployed at Nigerian telecom tower sites. Its purpose is to detect, deter, identify, and document criminal attacks against tower infrastructure in a manner that produces tamper-proof, prosecution-ready evidence. Every technical decision in this document traces back to that purpose.
Nigeria’s tower infrastructure sustains approximately 99 equipment theft incidents per week across 40,000+ active sites. Across five years and over 50,000 documented incidents, not a single prosecution has resulted. The reason is not a shortage of incidents, witnesses, or even detection. It is the consistent absence of evidence that meets the standard required for prosecution. ITIPS exists to close that gap.
1.2 What This Document Covers
This document specifies the complete technical requirements for the ITIPS platform across four layers:
The Command Centre: the real-time operational and regulatory intelligence interface accessible to operators and regulatory bodies
The Software Platform: the backend services, APIs, data architecture, evidence assembly engine, and RAPID dispatch system
The AI Layer: the edge intelligence running on the Jetson processor at each site, covering detection, identification, behavioural analysis, and evidence assembly
The Hardware Layer: the four-camera POC configuration across two sites, gate sensor, edge processor, power systems, and physical enclosures
1.3 Deployment Scale
All requirements in this document must be evaluated against the target deployment scale of 44,000 tower sites across Nigeria. Requirements that are technically feasible at two sites but operationally unmanageable at 44,000 sites are not acceptable. This applies particularly to provisioning, configuration, maintenance, firmware updates, and any process that requires human intervention per device.
1.4 Team Roles and Boundaries — MANDATORY READING FOR ALL ENGINEERS
This section defines territory. Every engineer on the ITIPS project must read it and understand it before writing a single line of code. The most damaging thing that can happen to this project is one team writing code that belongs to another team. This section exists to prevent that.
The Golden Rule
The Jetson is the edge. The cloud is the platform. These are two completely separate systems that communicate through a defined API contract. The AI team owns the Jetson entirely. The backend team owns the cloud platform entirely. Neither team writes code in the other’s territory under any circumstances.

AI Team — Owns Everything on the Jetson Except the Sync Agent
The AI team’s territory is the Jetson edge processor at each tower site. Everything that runs on the Jetson is the AI team’s responsibility, except the Jetson Sync Agent, which is a backend team deliverable that runs alongside the AI pipeline (see below). The AI team owns:
The video analytics pipeline (GStreamer with CUDA acceleration, ingesting RTSP streams from all cameras)
All AI inference, human detection (YOLO11), face detection and recognition (InsightFace), multi-object tracking (ByteTrack), license plate recognition (Plate Recognizer), behavioural analysis
The threat confirmation rules engine. The logic that aggregates all sensor signals and AI outputs into a threat classification
The AX PRO hub API integration running on the Jetson, receiving and decoding sensor events from all AX PRO sensors via the hub API. The inter-site LoRa mesh relay stack running on the Jetson for site-to-site heartbeat and alarm relay
The local personnel database cache, storing face embeddings and serving recognition requests without any network call
Evidence assembly, collecting all video, face captures, plate captures, sensor logs, and event logs into the evidence package structure
SHA-256 signing of evidence packages. This happens on the Jetson, not in the cloud (see Section 5 for implementation)
Running the port 8443 local HTTP server to receive push commands from the backend (personnel sync, configuration updates, maintenance window instructions, PTZ overrides)
Handing completed data to the Jetson Sync Agent: the AI team’s API responsibility ends at dropping structured data packets into the Sync Agent’s local intake. The AI team does not make any direct calls to cloud APIs. All outbound communication to the cloud is the Sync Agent’s job.
The AI team does not build: any backend service, any cloud database, any dashboard component, any RAPID dispatch logic, any responder tracking logic, and critically, no direct cloud API calls. The AI team produces data and hands it to the Sync Agent. What happens after that handoff is entirely the backend team’s responsibility.

Backend and Platform Team — Owns Everything in the Cloud and the Jetson Sync Agent
The backend team’s territory is every service, database, and API that runs in the cloud, plus one process that runs on the Jetson hardware: the Jetson Sync Agent. This includes:
All backend services listed in Section 4.1 (Ingest, Event Processing, Evidence, RAPID, Personnel, Dashboard API, Notification, Device Management, and the new Jetson Sync Agent)
The Jetson Sync Agent: a lightweight process deployed on the Jetson alongside the AI pipeline. It receives data from the AI pipeline via a local interface, queues it when the cloud is unreachable, drains it in priority order when connectivity restores, and makes all outbound API calls (A1–A8) to the cloud. The backend team owns this entirely. The AI team has no visibility into or responsibility for how it works. The backend team can update sync rules, priority logic, and retry behaviour by pushing a config update without touching AI code
Receiving and persisting all data sent by the Jetson Sync Agent (heartbeats, events, evidence chunks, completed packages)
RAPID dispatch logic, all logic after the Sync Agent triggers a dispatch request
Personnel database. The authoritative record of all enrolled persons and their face embeddings
Evidence vault, storing, protecting, and providing access to signed evidence packages
Dashboard API, all data served to the frontend comes from backend APIs
Pushing configuration, personnel changes, firmware updates, and maintenance windows to the Jetson via the port 8443 server
The Device Management Service, fleet health monitoring, remote firmware updates, zero-touch provisioning
The backend team does not build: any AI inference pipeline, any video processing, any sensor protocol stack, or any code that touches the AI pipeline directly. The Jetson Sync Agent communicates with the AI pipeline only through the agreed local intake interface. It does not reach into AI pipeline code.

Dashboard and Frontend Team — Owns the User Interface
The frontend team’s territory is the browser-based Command Centre and all user-facing interfaces. Everything the frontend team builds consumes backend APIs. It never communicates directly with a Jetson. The frontend team does not write backend logic or database queries directly. They request APIs from the backend team and build UI on top of the responses.

Hardware Team — Owns Physical Integration
The hardware team owns: physical installation of all cameras, sensors, Jetson enclosures, power systems, and the AX PRO hub at tower sites. They do not write application software. They work from the hardware specifications in Section 6. When a sensor or camera needs to be registered in software — AX PRO hub for sensors, camera API for cameras — they hand it off to the AI team.

The Interface Between AI Pipeline and Sync Agent
The AI pipeline and the Jetson Sync Agent communicate through a local intake interface: a simple local socket or shared SQLite file on the Jetson’s NVMe. The AI team drops structured data packets into this intake. The Sync Agent picks them up. Both teams must agree on the intake schema before implementation begins: each packet carries an endpoint identifier, a payload, a timestamp, a priority level (1–5), and an incident ID where applicable. The AI team proposes the schema. The backend team approves it. This is a one-page document agreed before either team starts building.
Section 4.7 contains the complete cloud API contract. The Sync Agent implements the client side of A1–A8. The AI team no longer needs to read or implement Section 4.7 endpoints. They only need to know the intake schema.

SECTION 2 — SYSTEM ARCHITECTURE OVERVIEW
Every ITIPS-protected site contains the following physical layers, each with independent power and independent communication paths. The six layers operate on fundamentally different detection principles, an attacker cannot defeat multiple layers with a single action.
Layer 1 — Camera-Based Perimeter and Compound Detection
The primary detection layer is the camera. Every camera runs its onboard AI continuously and independently, detecting human presence, vehicle presence, and perimeter breach at all times. Detection fires directly to the Jetson via ISAPI event without waiting for any sensor to confirm. The camera is never dependent on a sensor to wake it or validate its detection. Where vibration sensors are deployed on fence or tower structures they add an earlier detection layer and strengthen threat classification — but their absence does not reduce camera detection capability. The system is designed so that the camera alone is sufficient to detect, confirm, deter, and dispatch. Sensors add confidence and additional evidence depth where present.
Layer 2 — Contact and Access Point Detection
Wireless contact sensors at key access points — gate magnetic contact, battery cabinet shock detector, shelter dual-tech detector, and generator PIRcam. These sensors fire the moment a physical access point is touched, opened, or forced — regardless of whether a camera has detected movement. They catch inside jobs where someone with a legitimate key opens the gate or cabinet quietly with no visible threat. Battery powered. Wireless. No cable connection to any other system. All communicate to the Jetson via the AX PRO hub over 868MHz frequency hopping spread spectrum. The system is designed to accept additional sensors in future through configuration updates only — no code changes required.
Layer 3 — Primary Surveillance and Deterrence
Camera 1 (wired PoE). Mounted at elevation within or alongside tower antenna hardware. Primary prosecution evidence camera. Primary active deterrence unit (strobe and siren). Powered by PoE as primary with built-in 5-hour battery backup that activates seamlessly when PoE is cut. Built-in 4G LTE SIM provides communication fallback when ethernet is severed. Continues recording locally to onboard storage during connectivity loss and syncs on restore. POC confirmed models vary by site — see Section 6.2 for full specification. Main product: single camera per site, dual sensor panoramic plus PTZ, 4MP.

Layer 4 — Independent Wireless Backup Surveillance
Camera 2 — POC only. Two camera configuration applies to the POC only to maximise demonstration capability. The main product deploys a single camera per site. Camera 2 for the POC varies by site. See Section 6.3 for POC site-specific camera models. Camera 2 is not part of the main product specification.

Layer 5 — Shelter Intrusion Detection
Wireless door sensors mounted on the equipment shelter door and on any locked cabinet or rack inside the shelter containing high-value components (batteries, rectifiers, transmission equipment). These are the last physical detection layer, when the shelter door is forced or opened, the sensor fires immediately and the event escalates the incident to confirmed theft-in-progress. Battery powered. Wireless. See Section 6.10 for full specification.
Layer 6 — Edge Intelligence
The Jetson edge processor installed in a tamper-evident locked enclosure inside the equipment shelter. Receives data from all detection layers. Runs all AI processing. Assembles and signs evidence packages. Manages RAPID dispatch. External DC UPS battery backup. Communications via site backhaul ethernet only. The Jetson Orin Nano Super Developer Kit has no SIM slot. See Section 6.6 for full specification.
2.2 The Incident Lifecycle
Understanding the complete incident lifecycle is mandatory for every engineer. Every system component must be designed around this sequence. Note that real attacks do not always follow every stage in order. The rules engine in Section 5.8 handles all combinations. This lifecycle represents the most common organised attack pattern.
Stage 0 — Idle State
All cameras record locally using their own onboard AI. Cameras continuously running onboard detection — human presence, perimeter breach, vehicle detection — and firing ISAPI events to the Jetson on any detection. Jetson in event-driven standby, waking on any camera ISAPI event or sensor event. Gate magnetic contact armed. Compound, shelter, and cabinet sensors armed. Generator PIRcam armed. Horn speaker armed. System transmitting heartbeat to Command Centre every 60 seconds. Command Centre shows site as green and online.
Stage 0.25 — Pre-Attack Visual Detection (Cameras)
Before any physical contact occurs, cameras may already see the attackers. A vehicle pulling up on the approach road, a person loitering near the fence, or a group conducting reconnaissance is visible in the cameras’ field of view. All cameras run their onboard AI continuously and fire ISAPI detection events to the Jetson. The Jetson logs these events and the pre-event buffer begins recording. No alert is generated, no RAPID dispatch is triggered, no deterrence fires at this stage. The footage is captured and held. If no attack follows, the buffer overwrites naturally. If an attack follows and a subsequent camera detection or sensor event confirms intrusion, every second of this footage is retroactively reclassified as part of the incident record. Approach, reconnaissance, and loitering footage becomes prosecution evidence from the moment the attack is confirmed.
Stage 0.5 — Threat Confirmed
A camera ISAPI event fires. The Jetson wakes, pulls the relevant frame from the camera RTSP stream, runs YOLO11n to verify human presence, and runs InsightFace face recognition. If the detected person is not in the registered personnel database and no scheduled maintenance window is active, the threat is confirmed. This is the line between ambiguous presence and confirmed attack. The camera alone is sufficient to confirm a threat. If a sensor event (gate magnetic contact, cabinet shock, shelter dual-tech, or generator PIRcam) fires simultaneously or subsequently, the threat classification escalates immediately to high-priority and the evidence package gains an additional physical detection layer. Where vibration sensors are present on the fence or tower structure, their activation at this stage adds the earliest possible physical confirmation and highest-priority classification. The pre-event buffer window (default 15 minutes) is locked and becomes part of the evidence package. RAPID dispatch is triggered immediately on confirmed threat regardless of whether any sensor has fired.
Stage 1 — Attacker Cuts Tower Power
Tower power drops. Jetson detects power loss within 500 milliseconds. Jetson UPS takes over instantly — zero interruption to AI processing. Camera 1 loses PoE and switches to its internal battery backup seamlessly, continuing to record and transmit via its built-in 4G SIM. For POC sites where Camera 1 does not have internal battery, Camera 1 goes offline and Camera 2 continues independently. All wireless sensors are unaffected — battery powered. The power loss event is logged with a precise timestamp and transmitted to the Command Centre as a detection event. Combined with a prior camera detection event this triggers an immediate high-priority alert.
Stage 2 — Attacker Cuts Ethernet Cables
Camera 1 loses ethernet and goes offline. Camera 1 offline status is logged with timestamp as a detection event. All wireless sensors are unaffected. The Jetson loses site backhaul and goes dark to the platform. The Sync Agent queues all outgoing data locally. All AI inference, evidence assembly, and sensor monitoring continue uninterrupted on the Jetson. When backhaul restores the queue drains in priority order. The Jetson simultaneously broadcasts a distress signal via LoRa — the neighbouring site detects this and relays a site-down alert to the backend.
Critically, the backend does not wait for the Jetson to send a RAPID dispatch request. The Event Processing Service monitors for the following condition: the Jetson has missed three or more consecutive heartbeats, and a camera detection event, sensor event, cable cut event, or power loss event was logged for that site within the prior 30 minutes. When these conditions are met, the backend triggers RAPID dispatch automatically. The Jetson going dark after a detection signal is itself treated as an attack indicator. The towerco operator simultaneously receives a push notification alerting them to the site status. Automatic dispatch and operator alert run in parallel.
Stage 3 — Attacker Breaches Perimeter
The camera detects the person entering the compound. ISAPI event fires to the Jetson. The Jetson runs YOLO11n to verify human presence, then runs InsightFace face recognition against the registered personnel database. Threat confirmed. Deterrence fires from the camera’s onboard AI chip — strobe and speaker — within 500 milliseconds of detection. Simultaneously the Jetson sends an HTTP command to the horn speaker triggering 120dB siren and voice announcement in local language. If the gate magnetic contact has fired on gate opening it adds a physical confirmation event to the evidence package. RAPID dispatch is triggered. Alert transmitted to Command Centre with GPS coordinates, live camera feed link, and evidence package.
Stage 4 — Evidence Assembly Begins
From the moment of confirmed threat the Jetson begins assembling the evidence package continuously and in real time. All confirmed camera footage is captured from all active streams. Face detection and recognition run against all face captures. Vehicle plate recognition runs against any vehicle imagery. All sensor events — gate contact, cabinet shock, shelter dual-tech, generator PIRcam, temperature anomalies, power loss events, cable cut events — are included in the event log where they occurred. The evidence package is pushed to the cloud evidence vault in real time. By the time attackers reach the equipment shelter the cloud copy already exists and cannot be destroyed by physical theft of the Jetson.
Stage 4.5 — Equipment Shelter Breached (Door Sensor)
When the equipment shelter door is forced or opened, the door sensor fires immediately. This event escalates the incident classification from perimeter breach to confirmed equipment access. All cabinet door sensors inside the shelter that fire within the incident window are logged as separate events with individual timestamps. Each cabinet door event creates an additional line in the evidence log, directly supporting prosecution by documenting which specific equipment was accessed. Door sensor events are transmitted to the dashboard within 5 seconds of firing.
Stage 5 — Response and Aftermath
RAPID-dispatched responders are tracked to the site in real time on the Command Centre. When a responder arrives their GPS confirmation is added to the evidence package. Post-incident the Jetson completes the evidence package with the full chronological event log — all camera detection events, all sensor events, all deterrence activations, all communication events — applies the SHA-256 cryptographic signature, and marks it complete. The complete signed package is available for download from the Command Centre by authorised law enforcement.
2.3 The Three-Tier Storage Architecture
All footage and data in ITIPS operates across three storage tiers. Every engineer must understand this architecture and design their component to respect it.
Tier 1 — On-Camera Local Storage
Each camera records continuously to local storage (SD card or onboard flash) at all times, regardless of network connectivity. This is the raw footage layer. Camera 1: minimum 256GB. Camera 2: minimum 256GB (built-in eMMC). Local recording is write-priority, network transmission never interrupts or delays the local write.
Tier 2 — Jetson Edge Storage
The Jetson maintains a separate copy of all confirmed incident footage. Not continuous recording, event-triggered capture with a configurable pre-event buffer (default: 15 minutes before trigger) and post-event buffer (default: 30 minutes after all-clear). The Jetson stores on NVMe SSD, minimum 2TB per site, providing approximately 60–90 days of incident footage storage. SHA-256 signing of evidence packages happens here, at the edge, at the moment of incident. The signed package is the authoritative record.
Tier 3 — Cloud Evidence Vault
Confirmed incident packages are pushed to encrypted cloud storage from the Jetson. For confirmed threats, push begins immediately and continues in real time throughout the incident. For completed packages, push is verified and retried until cloud confirmation is received. The cloud vault is the long-term evidence repository accessible from the Command Centre. Operators and law enforcement access evidence from here, not from local cameras or the Jetson.
2.4 Offline Operation — A Core Design Principle
ITIPS core security functions are internet-independent by design. Connectivity enhances the system’s reach and coordination capability but is never a dependency for the primary security mission.
Every engineer must understand precisely which functions require connectivity and which do not, because designing any core security function with a dependency on internet connectivity is an architectural defect.
Functions that operate with zero connectivity:
All AI inference runs entirely on the Jetson’s GPU from models stored on the NVMe SSD. YOLO11, InsightFace, Plate Recognizer, GStreamer pipeline, ByteTrack, none of these make any network calls during inference. A tower site with no mobile coverage and no ethernet gets exactly the same detection, identification, and analysis capability as a site with a gigabit connection.
The personnel database runs from an encrypted local cache on the NVMe. The Jetson syncs with the central database when connectivity exists. When connectivity is lost, the local cache is used. All enrolled personnel remain recognisable and all unrecognised persons continue to trigger alerts. The known limitation: personnel deactivations do not propagate to the Jetson until connectivity is restored. This is acknowledged and acceptable. It is a communications lag, not a system failure.
Evidence assembly and SHA-256 signing both happen on the Jetson locally. The signed evidence package is created on-device at the moment of incident, stored on the NVMe, and pushed to the cloud vault when connectivity is available. If connectivity is never restored, the evidence still exists on the device and can be extracted physically.
Deterrence fires from the cameras’ own onboard AI. The Jetson is not in the loop for the first 500 milliseconds. The first security response at every site requires no network connection of any kind.
All detection layers — cameras, contact sensors, PIRcam, temperature sensor — transmit to the Jetson over the local site wireless network, not over the internet.

The Jetson Sync Agent — Offline Queue
All outbound data from the Jetson to the cloud passes through the Jetson Sync Agent. When the cloud is reachable, the Sync Agent sends data immediately in real time. When the cloud is unreachable, the Sync Agent writes all outgoing data to a persistent local queue on the NVMe and continues without interruption. The AI pipeline does not slow down, does not retry failed connections, and does not know or care whether the cloud is reachable. It simply hands data to the Sync Agent and moves on.
When connectivity restores, the Sync Agent drains the queue automatically in the following fixed priority order:

Priority
Data
Behaviour
1
RAPID dispatch requests
Sent immediately — life safety, cannot wait
2
Active incident events and stage updates
Sent immediately — operators need this now
3
Face and plate captures from active incidents
Sent immediately — evidence integrity
4
Completed evidence packages (video)
Sent when bandwidth allows — large files
5
Heartbeats and fleet health telemetry
Batched every 15 minutes — non-urgent

The queue is persistent. It survives a Jetson restart. The queue is bounded, maximum 30 days of data. When the queue approaches capacity, non-incident telemetry (Priority 5) is purged first. Incident data (Priorities 1–4) is never purged automatically.
Functions that require connectivity:
RAPID dispatch requires connectivity, dispatching responders, tracking their movement, and documenting response outcomes all happen over the network. If connectivity is lost during an active incident, deterrence continues, evidence continues to be recorded and signed locally, and the RAPID dispatch request is queued and sent the moment connectivity restores.
Dashboard updates require connectivity. The Command Centre will show the site as offline but the site continues to protect itself.
Personnel changes (enrolments, deactivations, maintenance windows) require connectivity to propagate to the Jetson. Changes made to the central database are queued and pushed to the Jetson as soon as connectivity is restored.
Firmware and configuration updates require connectivity.

2.5 Sensor Network Architecture — How Sensors Connect to the Jetson Hub
This section is mandatory reading for hardware engineers and backend engineers. It explains exactly how the AX PRO sensors and cameras communicate with the Jetson. Every engineer must understand this architecture before designing any component that interacts with sensor data.

The Communication Stack
The ITIPS sensor network uses the Hikvision AX PRO ecosystem as the wireless protocol between sensors and the Jetson. All sensors communicate with the AX PRO hub over 868MHz frequency hopping spread spectrum — 64 hops per second, 128-bit AES encrypted, two-way communication. The hub connects to the Jetson via ethernet and exposes an open API that the Jetson uses to receive sensor events, configure sensors, and manage the network. This architecture is the correct choice for ITIPS because: sensors operate on battery for years, the frequency hopping protocol cannot be defeated by 4G jammers, a single hub covers an entire tower site compound, the open API means the Jetson controls all sensor behaviour without any proprietary management software, and the AX PRO ecosystem is available in Nigeria today.
The complete data path from sensor to ITIPS application is:
[AX PRO Sensor] → (868MHz frequency hopping, AES-128 encrypted) → [AX PRO Hub] → (ethernet, open API) → [Jetson AI Pipeline]
Everything in this chain runs locally on the Jetson. No internet connection is required at any point for sensor data to reach the ITIPS application.
Component 1 — The Sensors
AX PRO sensors contain a 868MHz frequency hopping radio module and transmit encrypted data packets directly to the AX PRO hub. Each sensor has a unique device ID. Communication is two-way — the hub can also send commands to sensors. Encryption uses AES-128. No unencrypted data travels over the air at any point. Battery life ranges from 3 to 10 years depending on the sensor type and activation frequency.
Transmissions are triggered by: a threshold event (vibration above threshold, door opening), a periodic heartbeat (configurable, default 15 minutes), or a downlink command from the Jetson.
Component 2 — The AX PRO Hub
The AX PRO hub is the central wireless receiver for all sensors on site. It receives transmissions from all paired sensors simultaneously, manages two-way communication, processes alarm events, and exposes an open API over ethernet that the Jetson uses to receive sensor events in real time. The hub handles all sensor authentication, encryption, and communication management. The Jetson does not communicate directly with individual sensors — it communicates only with the hub API.
The confirmed hub for the POC is the Hikvision DS-PWA64-Kit-WB AX PRO hub. POC only. Main product will use DS-PWA96-M-WE or equivalent higher capacity hub. The hub connects to the Jetson via ethernet. The Jetson integrates with the hub via the Hikvision AX PRO open API to receive sensor events, arm and disarm zones, and manage maintenance windows.. The USB variant connects to the Jetson via USB 3.2 port. The Jetson Orin Nano Super Developer Kit has no mPCIe slot. The USB variant is the only compatible form factor. It supports EU868 (the correct band for Nigeria) and is production-proven in industrial deployments.
Resource
Link
RAK2287 product page
https://store.rakwireless.com/products/rak2287-lpwan-gateway-concentrator-module
RAK2287 quickstart guide
https://docs.rakwireless.com/product-categories/wislink/rak2287/quickstart/
RAK2287 datasheet
https://docs.rakwireless.com/product-categories/wislink/rak2287/datasheet/
RAK2287 Pi HAT (reference board for embedded integration)
https://docs.rakwireless.com/product-categories/wishat/rak2287-pi-hat/overview/

An external antenna must be mounted outside the Jetson enclosure and positioned for optimal site coverage.
Component 3 — AX PRO Hub API Integration (On the Jetson)
The AX PRO hub API integration is the component that bridges the sensor network to the Jetson AI pipeline. The Jetson connects to the AX PRO hub via ethernet and uses the hub’s open API to receive sensor events in real time, arm and disarm individual zones, configure maintenance windows, and monitor sensor health. Sensor events arrive as structured JSON payloads containing sensor ID, site ID, event type, timestamp, and zone information. The AI team builds this integration as a persistent service on the Jetson that subscribes to hub events and feeds them into the threat rules engine.
The AX PRO API integration runs fully offline with zero dependency on any external service. All sensor event processing happens on the Jetson. The system is designed to accept additional sensors through the same hub API without requiring code changes — sensor additions and changes are configuration updates only.
Resource
Link
ChirpStack main site
https://www.chirpstack.io/
ChirpStack full documentation
https://www.chirpstack.io/docs/
Getting started with Docker
https://www.chirpstack.io/docs/getting-started/docker.html
Device profiles and payload codecs
https://www.chirpstack.io/docs/chirpstack/use/device-profiles.html
MQTT integration (sensor data → ITIPS app)
https://www.chirpstack.io/docs/chirpstack/integrations/mqtt.html
RAK gateway integration guide
https://www.chirpstack.io/docs/gateway-configuration/rak.html
ChirpStack REST API reference
https://www.chirpstack.io/docs/chirpstack/api/rest.html

Component 4 — MQTT Broker (On the Jetson)
The AX PRO hub API delivers all sensor events to the Jetson in real time over the ethernet connection. The Jetson AI pipeline subscribes to hub events and processes them immediately. No MQTT broker or intermediate message queue is required for sensor events. The hub API handles all sensor communication, authentication, and event delivery.
Resource
Link
Eclipse Mosquitto
https://mosquitto.org/
Mosquitto Docker image
https://hub.docker.com/_/eclipse-mosquitto
ChirpStack MQTT topic structure
https://www.chirpstack.io/docs/chirpstack/integrations/mqtt.html#topic-templates


Sensor Registration and Provisioning
Every sensor must be paired with the AX PRO hub before it can communicate. Pairing is done through the AX PRO hub interface by the hardware team during installation. Once paired, the sensor appears in the hub API with its unique zone ID, device type, and status. The AI team registers the zone ID in the ITIPS configuration file and maps it to its site location and detection role. No code changes are required to add a new sensor — only a configuration update mapping the zone ID to its role.
The hardware team must build a provisioning checklist covering: pairing sensor with AX PRO hub, confirming zone ID and sensor type in hub interface, handing zone ID to AI team, AI team adding zone ID to ITIPS configuration with site location and detection role, testing sensor event delivery to Jetson, and confirming event reaches the threat rules engine before sensor is installed at a live site.
Recommended Sensor Products with Documentation
Vibration Sensors:
Vibration Sensor — NOT IN POC — Main Product Specification
Vibration sensor functional requirements for future integration:
Any vibration sensor integrated into ITIPS must meet the functional requirements listed in this section. No sensor model name or specific protocol must be hardcoded in the pipeline. Sensors integrate through configuration only.
Resource
Link
LHT65N-VIB product page
https://www.dragino.com/products/lorawan-nb-iot-door-sensor-water-leak/item/262-lht65n-vib.html
LHT65N-VIB user manual
https://www.dragino.com/downloads/index.php?dir=LHT65N-VIB/
Dragino ChirpStack payload decoders
https://github.com/dragino/dragino-end-node-decoder
Dragino documentation wiki
https://wiki.dragino.com/

Vibration sensors are not deployed in the POC. When confirmed for the main product they will be paired with the AX PRO hub or integrated via LoRaWAN depending on the supplier’s protocol. The Jetson pipeline accepts events from either source through configuration only.
Door Sensors:
Confirmed POC Door and Contact Sensors — Hikvision AX PRO Ecosystem
Specifically designed for outdoor use. IP65 rated. Magnetic reed switch mechanism. Reports open/close status, open duration, and open count, all directly relevant to the ITIPS evidence log requirement. Powered by an internal battery rated for multi-year life.
Resource
Link
LDS03A product page
https://www.dragino.com/products/lorawan-nb-iot-door-sensor-water-leak/item/196-lds03a.html
LDS03A user manual (download)
https://www.dragino.com/downloads/index.php?dir=LDS03A/
LDS02 ChirpStack integration guide (indoor sibling device, identical protocol)
https://www.dragino.com/products/lorawan-nb-iot-door-sensor-water-leak/item/181-lds02.html
Dragino decoder library for ChirpStack
https://github.com/dragino/dragino-end-node-decoder

All contact and detection sensors for the POC are from the Hikvision AX PRO ecosystem. They pair directly with the DS-PWA64-Kit-WB hub and communicate via 868MHz frequency hopping. No separate receiver, decoder, or protocol stack is required. See the confirmed POC sensor stack in Appendix B for full model list.
Where outdoor IP65 rating is not required (internal cabinet doors inside the equipment shelter), the Milesight WS301 is a compact and well-documented alternative.
Resource
Link
WS301 product page
https://www.milesight.com/iot/product/lorawan-sensor/ws301
Milesight LoRaWAN sensor documentation
https://support.milesight.com/hc/en-us/categories/360002108818-LoRaWAN-Sensors




Nigerian Frequency Band — Critical
The AX PRO hub and all AX PRO sensors operate on 868MHz. All sensors must be the WB or WE suffix variants confirming 868MHz compatibility. Sensors in the 433MHz variants will not pair with the confirmed hub and must not be ordered. This must be verified with the supplier before every procurement order.
Resource
Link
LoRaWAN regional parameters specification
https://lora-alliance.org/resource_hub/rp2-1-0-3-lorawan-regional-parameters/
ChirpStack EU868 band configuration
https://www.chirpstack.io/docs/chirpstack/configuration.html


SECTION 3 — NATIONAL DASHBOARD
3.1 Access Tiers — Full Specification
The Command Centre has five distinct access tiers. Access tier determines which views are visible, which actions can be taken, what data can be exported, and what the default landing page is on login. The frontend team must implement these tiers as discrete role configurations enforced both client-side and server-side. No tier must ever be able to access a view, action, or data belonging to a different tier.


Tier 1 — NCC Regulatory Access
Purpose: Give the NCC Technical Directorate independent, real-time visibility into infrastructure protection performance across all operators, without depending on operator self-reporting.
Default landing view: National site map (all 44,000 sites, colour-coded by status)
REQ-TIER1-01 | MANDATORY
NCC sees the full national site map across all operators simultaneously. No operator filter is applied by default. NCC can filter by operator, state, or alert status but defaults to the all-Nigeria view.
REQ-TIER1-02 | MANDATORY
NCC sees the live incident feed across all operators in real time. Each entry shows site ID, operator name, alert type, stage, and RAPID dispatch status. NCC cannot suppress or delay any incident entry.
REQ-TIER1-03 | MANDATORY
NCC has access to the national regulatory analytics view: incident frequency by state and operator, response time statistics aggregated nationally, false alarm rates by operator, evidence package completion rates nationally. This data is shown in aggregate. NCC can see cross-operator comparisons but operator-specific data is anonymised in the comparison view.
REQ-TIER1-04 | MANDATORY
NCC can view and download any signed evidence package from any site across any operator. Download events are logged in the audit trail of each package.
REQ-TIER1-05 | MANDATORY
NCC receives the national monthly report automatically on the 1st of each month covering: total incidents, total prosecutable evidence packages generated, average response time, operator performance rankings, and RAPID compliance by agency.
REQ-TIER1-06 | MANDATORY — What NCC Cannot Do
NCC cannot: modify any site configuration, access operator commercial or billing data, enrol or deactivate personnel, schedule maintenance windows, send commands to any Jetson or camera, or access the fleet health management controls. NCC is read-only across all operator data.

Tier 2 — NSCDC Command Access
Purpose: Give NSCDC state directors and national command visibility into their officers’ performance against their CNII statutory mandate, and access to incident data relevant to their operations.
Default landing view: NSCDC incident map filtered to the user’s state (state directors) or national (national command)
REQ-TIER2-01 | MANDATORY
NSCDC state directors see all confirmed infrastructure security incidents within their state in real time. The national command sees all states. Incidents show: location, time, alert classification, whether an NSCDC officer was dispatched, and whether a response was confirmed on site.
REQ-TIER2-02 | MANDATORY
NSCDC command sees the officer performance view: every RAPID dispatch sent to their officers with dispatch timestamp, acceptance status, response time, GPS-confirmed on-site time, and outcome classification. State directors see their state’s officers only. National command sees all officers across all states.
REQ-TIER2-03 | MANDATORY
NSCDC command receives the CNII compliance report automatically on the 1st of each month: total incidents in coverage area, dispatches to NSCDC officers, acceptance rate, average response time, outcome distribution. Delivered by email and available for download from the dashboard.
REQ-TIER2-04 | HIGH
Where an NSCDC officer was the dispatched responder on a confirmed incident, NSCDC command can access the evidence package for that incident for operational debriefing. Access is logged. NSCDC cannot access evidence packages for incidents where no NSCDC officer was involved.
REQ-TIER2-05 | HIGH
Formation coverage map overlay: shows which tower sites fall within each NSCDC formation’s designated coverage zone. Helps command identify gaps and optimize deployment.
REQ-TIER2-06 | MANDATORY — What NSCDC Cannot Do
NSCDC cannot: see operator commercial data or network performance metrics, modify site configurations, access personnel databases, enrol or deactivate personnel, see evidence packages unrelated to their officers, or access fleet health data. NSCDC’s view is security incidents and their officers’ response to them, nothing else.

Tier 3 — Tower Company (Towerco) Operator Access
Purpose: Full operational management of the towerco’s ITIPS-protected portfolio, security operations, personnel management, maintenance, evidence, and analytics.
Default landing view: Towerco site map showing all sites in their portfolio, with active incident counter in the top bar
REQ-TIER3-01 | MANDATORY
Towerco sees all sites in their portfolio on the site map. Cannot see any other towerco’s sites. Map shows site status, last heartbeat, and active incident flag per site.
REQ-TIER3-02 | MANDATORY
Full live incident feed for all towerco sites. Towerco operators can open any active incident, view all live camera feeds from that site, see the RAPID dispatch tracker, and view the evidence package assembly progress in real time.
REQ-TIER3-03 | MANDATORY
Full camera feed access, live and historical. Historical footage playback from any camera at any site in their portfolio. Footage clearly marked with storage tier (edge or cloud) and signing status.
REQ-TIER3-04 | MANDATORY
Camera 1 PTZ manual override during active incidents. Operators can redirect the PTZ camera without disabling autonomous evidence capture.
REQ-TIER3-05 | MANDATORY
Full personnel management: enrol new personnel, assign to sites, view enrolment status, deactivate records, view access history. See Section 3.9 and 4.5 for complete enrolment and deactivation workflow specifications.
REQ-TIER3-06 | MANDATORY
Full maintenance window management: schedule windows, assign technicians, set duration, view window history. See Section 3.9 for complete specification.
REQ-TIER3-07 | MANDATORY
Full evidence library access for all sites in the portfolio. Download complete signed evidence packages. Verify package signatures. View law enforcement download history per package.
REQ-TIER3-08 | MANDATORY
Towerco-level analytics: incidents by site, region, and time period; response time trends; false alarm rates; evidence package completion rates. Exportable as PDF and CSV.
REQ-TIER3-09 | MANDATORY
Full fleet health management: per-site and per-device health dashboard, automated maintenance alerts, predictive maintenance indicators, firmware version tracking.
REQ-TIER3-10 | MANDATORY — What Towerco Cannot Do
Towerco cannot: see any other towerco’s sites or data, modify NCC regulatory views, access NSCDC officer performance data, modify RAPID responder records, or access platform administration functions.

Tier 4 — Mobile Network Operator (MNO) Access
Purpose: Give MNOs visibility into how tower infrastructure attacks are affecting their network service continuity, without giving them operational control over physical security or evidence.
Default landing view: MNO network impact dashboard, showing sites with recent incidents that affected service continuity
REQ-TIER4-01 | MANDATORY
MNO sees a site map filtered to tower sites that carry their network. Site status shows: no incident (green), incident with no service impact (amber), incident with confirmed service impact (red), site offline (grey). MNO cannot see sites that don’t carry their network.
REQ-TIER4-02 | MANDATORY
MNO sees the incident summary feed, site ID, incident date and time, incident type, and whether service was disrupted. MNO does not see camera footage, face captures, plate captures, or evidence package contents. MNO sees that an incident happened and its impact on their service, not the evidence gathered.
REQ-TIER4-03 | MANDATORY
MNO analytics view: incident frequency by site and state for their affected sites, service disruption events (incidents that caused downtime), downtime duration by site, trends over configurable date range. Exportable as PDF for regulatory and investor reporting.
REQ-TIER4-04 | HIGH
MNO receives an automated weekly incident summary by email covering: incidents at their affected sites, service impact events, and comparison to the previous week.
REQ-TIER4-05 | MANDATORY — What MNO Cannot Do
MNO cannot: view camera footage or live feeds, access evidence packages (those belong to the towerco), enrol or deactivate personnel, schedule maintenance windows, access fleet health data, see RAPID dispatch details, modify any site configuration, or see any other MNO’s data. MNO access is strictly read-only and limited to service continuity information.

Tier 5 — Site-Level Access
Purpose: Allow field technicians and site managers to monitor their assigned sites, acknowledge alarms, and initiate maintenance mode, without giving them access to evidence, cross-site data, or configuration.
Default landing view: List of sites assigned to this user with current status
REQ-TIER5-01 | MANDATORY
Site-level users see only sites explicitly assigned to them. Cannot navigate to any other site.
REQ-TIER5-02 | MANDATORY
For each assigned site the user can see: current site status (online/alert/offline), camera statuses, last heartbeat, battery levels, and the 5 most recent incidents (summary only, no evidence package access).
REQ-TIER5-03 | MANDATORY
Site-level users can acknowledge an alarm at their assigned site. Acknowledgement is logged with the user’s identity and timestamp and appears in the incident record.
REQ-TIER5-04 | MANDATORY
Site-level users can initiate maintenance mode for themselves at their assigned site, provided they are a registered personnel member with an active or scheduled maintenance window. They cannot initiate maintenance mode for other personnel.
REQ-TIER5-05 | MANDATORY — What Site-Level Users Cannot Do
Site-level users cannot: access evidence packages, view historical footage, access any cross-site data, enrol or deactivate personnel, modify site configurations, access analytics, view fleet health across sites, or see RAPID dispatch details.

3.2 General Dashboard Requirements
The Command Centre must be a web-based application accessible from any modern browser without installation. A mobile-responsive layout is required. A dedicated mobile application for towerco command centre use is a HIGH requirement for Phase 1.
REQ-DASH-GEN-01 | MANDATORY
All dashboard real-time updates use WebSocket connections. Polling is not acceptable for incident alerts or map status updates.
REQ-DASH-GEN-02 | MANDATORY
Every action taken on the dashboard, alarm acknowledgement, personnel change, maintenance window, evidence download, configuration change, is logged to an audit trail with the user’s identity, their access tier, timestamp, and the specific action taken.
REQ-DASH-GEN-03 | MANDATORY
Session timeout after 30 minutes of inactivity. Re-authentication required. Active incident views are exempt from timeout while a confirmed incident is actively in progress.
REQ-DASH-GEN-04 | MANDATORY
All dashboard data is served from backend APIs. The frontend team never calls Jetson APIs directly. All data flows from Jetson → backend → dashboard API → frontend.
3.3 Real-Time Site Map
REQ-DASH-01 | MANDATORY
A live national map displaying every ITIPS-protected site. Each site is represented by a pin whose colour reflects its current status: green (online, no alerts), amber (alert in progress, response dispatched), red (confirmed incident, no response confirmed), grey (offline or communication lost).
REQ-DASH-02 | MANDATORY
The map must update in real time. A site that changes status must update its pin colour within 5 seconds. The map must support zoom from national level (all 44,000 sites) to individual site level. At site level the map shows the compound footprint, camera positions, and active alert zones.
REQ-DASH-03 | MANDATORY
Clicking any site pin opens a site detail panel showing: site ID, operator, location, current status, last heartbeat timestamp, active cameras and their status, current battery levels for wireless cameras, and the 5 most recent incidents at that site.
REQ-DASH-04 | HIGH
Map supports filtering by operator, state, alert status, and camera type. For regulatory access, the filter must support viewing all sites or filtering by operator. Heatmap overlay showing incident density by geography is a HIGH requirement.
REQ-DASH-05 | HIGH
Map includes a coverage gap analysis overlay, showing tower sites that do not yet have ITIPS coverage. This supports deployment planning and operator conversations.
3.4 Live Incident Feed
REQ-DASH-06 | MANDATORY
A real-time chronological incident feed displaying all active and recent alerts across the operator’s portfolio (or all operators for regulatory access). Each entry shows: site ID, alert type, timestamp, alert stage, RAPID dispatch status, and a thumbnail from the triggering camera.
REQ-DASH-07 | MANDATORY
Clicking an incident entry opens the full incident view: live camera feeds from all active cameras at the site, the timeline of events from the incident lifecycle, the current RAPID dispatch status including responder locations on a local map, and the evidence package assembly status.
REQ-DASH-08 | MANDATORY
The incident feed must distinguish between confirmed threats (human detection confirmed, deterrence fired, RAPID dispatched) and preliminary alerts (power loss, cable cut, or detection event pending AI confirmation). Preliminary alerts appear with lower visual priority and do not trigger RAPID dispatch until AI confirmation.
REQ-DASH-09 | MANDATORY
Audio notification option for confirmed threats. Dashboard users who have notifications enabled receive an audible alert when a confirmed threat fires at any site in their access scope. Notification must identify the site and alert type without requiring the user to look at the screen.
3.5 Live Camera Feeds
REQ-DASH-10 | MANDATORY
Live camera feed viewer accessible from the site detail panel and from the incident view. Must support simultaneous viewing of all cameras at a site in a configurable grid layout (1-up, 2-up, 4-up). Feed latency must not exceed 3 seconds under normal network conditions.
REQ-DASH-10a | MANDATORY — Live Feed Streaming Protocol
Live camera feeds must be delivered to the dashboard using WebRTC. HLS, RTSP relay, and MJPEG are not acceptable for live feeds. HLS introduces a minimum 4 to 10 second buffer by design and will never meet the 3 second latency requirement regardless of implementation quality. WebRTC delivers sub-500 millisecond latency on normal network conditions.
The backend team must implement a WebRTC media server (Janus, mediasoup, or equivalent). When a live view is requested, the Jetson initiates a WebRTC push stream to the media server. The dashboard connects to the media server via WebRTC. The Jetson is never exposed directly to the internet. The media server handles all WebRTC negotiation and relay.
Live feed requests flow as follows: dashboard operator opens incident view → backend sends B4 request_stream command to Jetson → Jetson initiates WebRTC push to media server → media server returns WebRTC session ID to backend → backend passes session ID to dashboard → dashboard connects to media server. Total time from request to first frame must not exceed 3 seconds on a standard Nigerian 4G connection.
REQ-DASH-11 | MANDATORY
During an active incident, live feeds from all cameras at the affected site are automatically surfaced in the incident view without requiring the operator to navigate to them.
REQ-DASH-12 | HIGH
Camera 1 PTZ controls accessible from the dashboard during an active incident. The operator can override the AI’s automatic tracking to manually direct the PTZ camera to a specific area of the compound. Manual override does not disable autonomous evidence capture.
REQ-DASH-13 | HIGH
Historical footage playback from any camera. The operator selects a site, a camera, and a time window, and the dashboard streams the stored footage from the Jetson or cloud vault. Footage is clearly marked with its storage tier (edge or cloud) and its integrity status (signed/verified or unsigned).
3.6 RAPID Dispatch Tracker
REQ-DASH-14 | MANDATORY
A real-time responder tracking view for each active incident. Shows a local map of the incident site and the GPS locations of all RAPID-dispatched responders updating in real time. Each responder is shown with their estimated time of arrival, their acceptance status (dispatched, accepted, en route, on site), and their agency/unit.
REQ-DASH-15 | MANDATORY
Dispatch timeline visible for each incident: when the RAPID alert was sent, which responders were notified, when each responder accepted, and the documented response outcome. This timeline becomes part of the evidence package.
REQ-DASH-16 | HIGH
Manual dispatch override. If RAPID’s automated dispatch does not receive an acceptance within a configurable timeout (default 2 minutes), the dashboard presents the operator with a manual dispatch option and a list of available responders ordered by proximity.
3.7 NSCDC Command Dashboard
REQ-DASH-NSCDC-01 | MANDATORY
A dedicated NSCDC view accessible only to Tier 2 (NSCDC command) users. This view is security and mandate-focused. It must not surface operator commercial data, network performance metrics, or any information that is not directly related to physical security incidents and NSCDC officer performance.
REQ-DASH-NSCDC-02 | MANDATORY
Incident map view for NSCDC. Shows all confirmed infrastructure security incidents across Nigeria in real time, filterable by state. Incident pins show location, time, and whether an NSCDC officer was dispatched and responded. NSCDC command can see the full distribution of incidents relevant to their mandate without any filtering by operator.
REQ-DASH-NSCDC-03 | MANDATORY
Officer performance view. For every RAPID dispatch sent to an NSCDC officer, the dashboard shows: officer name and formation, dispatch timestamp, acceptance status, response time, on-site confirmation time, and outcome classification. State directors see their own state’s officers only. The national command sees all states. This data is the accountability record for CNII mandate compliance.
REQ-DASH-NSCDC-04 | MANDATORY
CNII compliance report. An automated monthly report generated for each NSCDC formation showing: total infrastructure incidents in their coverage area, total RAPID dispatches to their officers, acceptance rate, average response time, and outcome distribution. Report is generated on the 1st of each month for the preceding month and delivered by email to the designated NSCDC command contact and made available for download from the dashboard.
REQ-DASH-NSCDC-05 | HIGH
Evidence package access for NSCDC. Where an NSCDC officer was the dispatched responder on a confirmed incident, the NSCDC command tier can access the evidence package for that incident for operational debriefing and investigation support. Access is logged with a full audit trail. NSCDC cannot access evidence packages for incidents where no NSCDC officer was involved.
REQ-DASH-NSCDC-06 | HIGH
Formation coverage map. An overlay showing which tower sites fall within each NSCDC formation’s designated coverage zone, helping command identify coverage gaps and optimize responder deployment across states.
3.8 Evidence Library
REQ-DASH-17 | MANDATORY
A searchable archive of all completed evidence packages from all sites within the operator’s access scope. Each package entry shows: site ID, incident date and time, incident type, evidence package status (complete/incomplete), signing status (signed/unsigned), and available download format.
REQ-DASH-18 | MANDATORY
Evidence packages are downloadable in a standardized format that includes: all video files, all face capture images, all vehicle plate captures, a machine-readable JSON event log, a human-readable PDF incident summary, and the SHA-256 cryptographic signature file. The download is a single encrypted archive with a verification key.
REQ-DASH-19 | MANDATORY
Evidence package integrity verification. The dashboard provides a one-click verification function that confirms the SHA-256 signature of any downloaded package matches the signature recorded at the time of creation. The verification result is timestamped and logged against the package record.
REQ-DASH-20 | HIGH
Law enforcement download portal. A separate access tier for designated law enforcement officers who can request and download specific evidence packages. Their access is logged, their identity is verified, and their download creates an auditable access record that is appended to the evidence package metadata.
3.8a Stolen Asset Registry — Phase 2
This capability is scheduled for Phase 2 and must not be built as part of the POC or Phase 1 scope. The database schema and API design should be considered during Phase 1 so that Phase 2 integration does not require architectural rework, but no frontend or backend implementation is required before Phase 2 begins.
The Stolen Asset Registry will be a searchable database of serialised equipment registered at each ITIPS-protected site. When an asset is reported stolen, a theft record will be created linking to the incident evidence package. Law enforcement will be able to search for serialised equipment at markets, checkpoints, and border crossings. Full specification will be produced as a Phase 2 requirements document.
3.9 Analytics and Reporting
REQ-DASH-24 | MANDATORY
Operator-level analytics dashboard showing: total incidents by site and region over a configurable date range, incident frequency trends (are attacks increasing or decreasing), response time statistics (average time from RAPID dispatch to responder on site), evidence package completion rate, and false alarm rate by site.
REQ-DASH-25 | MANDATORY
Regulatory analytics view (NCC access only) showing the same metrics aggregated across all operators, enabling cross-operator comparison and national trend analysis without revealing one operator’s data to another.
REQ-DASH-26 | HIGH
Automated weekly and monthly reports generated as PDFs and delivered by email to designated recipients. Report content is configurable per recipient by access tier.
REQ-DASH-27a | MANDATORY — National Critical Infrastructure Security Report (ONSA)
The Office of the National Security Adviser (ONSA), through the Department of Critical Infrastructure Protection, oversees all critical national infrastructure protection in Nigeria including all sector regulators. ONSA requires a strategic national overview of infrastructure security performance, not operational access to the dashboard.
ONSA has been elevated to full real-time dashboard access. The monthly PDF report described below remains as an automated export. However ONSA now receives a dedicated real-time dashboard view — the only view in ITIPS that sees the full national picture simultaneously across every site, every operator, every agency, and every active incident. ONSA dashboard access includes: national site map with live green/amber/red status, national threat pattern intelligence (attack clustering by state and corridor), RAPID agency compliance tracking (which agencies respond, how fast, coverage gaps), case tracking (investigation stage and assigned court per prosecuted incident), and operator coverage map (protected versus exposed sites nationally). The Notification Service must continue to generate the monthly National Critical Infrastructure Security Report as a PDF delivered by email to designated ONSA recipients on the 1st of each month. No dashboard login is required for the PDF report. Dashboard login is a separate provision for designated ONSA analysts.
The report must contain: total infrastructure security incidents nationally for the preceding month; incident trend versus the prior 3 months; geographic distribution of incidents by state and geopolitical zone; deterrence effectiveness rate nationally (incidents where attackers were deterred before equipment access); RAPID response compliance rate across all enrolled security agencies; operator coverage percentage (sites with active ITIPS protection versus total national tower inventory); and a one-page executive summary suitable for briefing at national security council level.
The report must not contain: individual site identifiers, operator commercial data, evidence package contents, personnel records, or any information that identifies specific towercos or MNOs by name in comparative rankings. All operator data in the ONSA report is aggregated at national level only.
3.10 Personnel Management
REQ-DASH-27 | MANDATORY
A personnel management interface for each operator to manage their registered personnel database. See Section 4.5 for the complete personnel database specification. The dashboard is the primary interface through which operators enrol personnel, manage access, and schedule maintenance windows.
REQ-DASH-28 | MANDATORY
Enrolment workflow accessible from the dashboard. Operator initiates enrolment for a new technician. The system generates a unique enrolment link sent to the technician. Technicians complete a face capture sequence (minimum 5 images at different angles) using their mobile device camera. Images are submitted to the AI layer for quality validation. On validation the technician is added to the database and assigned to the relevant sites. Maximum time from enrolment initiation to database activation: 2 hours.
REQ-DASH-29 | MANDATORY
Termination workflow. Operator clicks deactivate on any personnel record. The deactivation is propagated to all sites the person is assigned to within 15 minutes. The deactivated person will trigger a confirmed alert if they approach any site they were previously registered at.
REQ-DASH-30 | MANDATORY
Maintenance window scheduling. Operator selects a site, selects a registered technician, sets a start time and duration (maximum 8 hours per window). The system disarms that site for that technician during that window only, then re-arms automatically at the end of the window. The technician’s face detection events during the window are logged but do not trigger deterrence or RAPID dispatch. All windows are auditable.
3.11 Fleet Health Monitoring
REQ-DASH-31 | MANDATORY
A fleet health overview showing the operational status of every system component across all sites. For each site the Command Centre shows: online/offline status of each camera, last heartbeat timestamp, storage capacity remaining, Jetson UPS battery level, site backhaul status, and any fault conditions.
REQ-DASH-32 | MANDATORY
Automated alerts for fleet health events: any camera going offline, Jetson UPS battery below 30%, storage above 80%, site backhaul connectivity degraded, Jetson temperature above threshold, firmware version out of date. These alerts go to the operator’s maintenance team, not to security operations.
REQ-DASH-33 | HIGH
Predictive maintenance indicators. Based on battery discharge rates, signal quality trends, and historical fault patterns, the dashboard highlights sites that are likely to experience hardware issues within the next 7 days and recommends maintenance dispatch.

SECTION 4 — SOFTWARE PLATFORM
4.1 Architecture Philosophy
The ITIPS software platform must be designed for national-scale deployment from day one. Every architectural decision, database selection, API design, caching strategy, message queue implementation, must be evaluated against the operational demands of 44,000 simultaneously connected sites, each generating heartbeat data every 60 seconds, each capable of triggering a real-time incident stream at any moment.
The platform is divided into the following core services:
Ingest Service: receives all data from Jetson Sync Agents (heartbeats, alerts, evidence packages, health metrics)
Event Processing Service: processes incoming events, applies business logic, triggers RAPID dispatch, updates dashboard state
Evidence Service: manages the evidence vault, handles package uploads, manages SHA-256 verification, provides law enforcement download
RAPID Service: manages the responder network, processes dispatch, tracks responder movement
Personnel Service: manages the personnel database, handles enrolment, propagates changes to edge devices
Dashboard API: serves the frontend dashboard, manages WebSocket connections for real-time updates
Notification Service: manages alerts, emails, SMS, and in-app notifications
Device Management Service: manages firmware updates, remote configuration, fleet health monitoring
Jetson Sync Agent: a lightweight process deployed on each Jetson device (not in the cloud). Receives data from the AI pipeline via a local intake interface, queues it persistently when the cloud is unreachable, and drains the queue in priority order when connectivity restores. Makes all outbound API calls (A1–A8) to the cloud on behalf of the Jetson. Owned and maintained entirely by the backend team. The AI team has no visibility into or responsibility for this process. The backend team can update sync rules, priority tiers, and retry logic via config push without touching AI code
The Ingest Service must handle data arriving from the Sync Agent gracefully regardless of whether it was sent in real time or arrived after a connectivity outage. A heartbeat that arrives 6 hours late must update the fleet health history without overwriting the current live status. Out-of-order delivery must never corrupt the incident record.
Backend Fallback Intelligence Mode
The Event Processing Service must implement an independent RAPID dispatch rule that operates without any input from the Jetson. This rule activates when the backend observes all three of the following conditions simultaneously for a single site: the Jetson has missed three or more consecutive 60-second heartbeats; camera data or a neighbouring site LoRa relay is still arriving from that site; and a camera detection event, sensor event, cable cut event, or power loss event was logged for that site within the prior 30 minutes.
When all three conditions are met, the backend triggers RAPID dispatch automatically using the site’s stored GPS coordinates and the Camera 2 feed URL. No face recognition, no personnel database check, and no Jetson input are required. The three facts together are sufficient to confirm an attack is in progress.
Simultaneously with the RAPID dispatch, the Event Processing Service triggers the Notification Service to send a push notification to the towerco operator for that site. The notification includes the site ID, the triggering conditions, and a direct link to Camera 2’s live feed from the dashboard. The operator alert and the RAPID dispatch fire at the same time. Neither waits for the other.
This mode does not replace the Jetson’s own dispatch capability. When the Jetson is online and functioning, it remains the primary dispatch decision-maker via the A8 API call. The backend fallback mode is a safety net that activates only when the Jetson is unreachable during a confirmed attack signal window.
These services may be implemented as microservices or as a well-structured monolith at the team’s discretion, provided the design can scale to 44,000 sites without architectural rework. The team must document their architecture decision with justification before beginning implementation.
4.2 Real-Time Communication Requirements
REQ-SW-01 | MANDATORY
All dashboard connections must use WebSocket (or Server-Sent Events as fallback) for real-time updates. Polling is not acceptable for incident alerts or RAPID dispatch tracking. A confirmed incident alert must appear on all relevant dashboard sessions within 3 seconds of the Jetson transmitting it.
REQ-SW-02 | MANDATORY
The Ingest Service must be capable of receiving simultaneous connections from a minimum of 44,000 edge devices. Design must account for the scenario where a widespread power grid event causes a large number of sites to simultaneously generate power-loss alerts. The system must not drop events under load.
REQ-SW-03 | MANDATORY
All communication between Jetson devices and the platform must be authenticated and encrypted. Each Jetson device is provisioned with a unique device certificate at deployment. The certificate is used for mutual TLS authentication on every connection. Compromised certificates must be revocable from the Device Management Service.
REQ-SW-04 | MANDATORY
The platform must implement a message queue (Apache Kafka or equivalent) between the Ingest Service and downstream processing services. Raw events must be persisted to the queue before any processing begins. This ensures no events are lost during processing service restarts or failures.

 4.3 Evidence Package Specification
Ownership note: This section specifies the evidence package FORMAT and CONTENTS that the backend must receive and store. The AI team is responsible for ASSEMBLING and SIGNING the evidence package on the Jetson. The implementation of that assembly and signing is specified in Section 5. The backend team does not build the signing logic. The backend team receives already-signed packages and verifies them. If there is any confusion about who builds what in this area, refer to Section 1.4 (Team Roles and Boundaries).
This is the most critical specification in the software platform section. Every evidence package produced by ITIPS must conform to this specification without exception.
REQ-EV-01 | MANDATORY — Evidence Package Contents
Every confirmed incident evidence package must contain the following components. The package is incomplete and must not be marked as complete until all available components are present:
incident_metadata.json, machine-readable incident record containing: site_id, incident_id, timestamp_utc, gps_coordinates, incident_classification, alert_stage_log (array of all alert stages with timestamps), camera_ids_active, sensor_events (array of all vibration and door sensor events), responder_dispatch_log
event_log.json, complete timestamped log of every sensor event (gate contact, cabinet shock, shelter dual-tech, generator PIRcam, temperature), AI detection event, deterrence activation, camera event, communication event, and evidence assembly event from the full incident window. This is the master chronological record of everything that happened from the first detection event to incident close
sensor_log.json, dedicated log of all physical sensor events: gate magnetic contact events, cabinet shock detector events, shelter dual-tech events, generator PIRcam events, temperature readings, and any vibration sensor events where sensors are deployed. Separated from the main event log for ease of use by investigators who need the physical evidence chain
video_camera_[id]_full.mp4, full incident video from each active camera, from 15 minutes before first trigger to 30 minutes after last event, H.265 encoded
video_camera_[id]_highlight.mp4. 30-second highlight clip from each camera covering the primary identification moment, H.265 encoded
face_captures/face_[n]_[timestamp].jpg, every face image captured during the incident with confidence score embedded in filename
plate_captures/plate_[n]_[timestamp].jpg, every vehicle plate capture with OCR result and confidence score embedded in filename
incident_summary.pdf, human-readable PDF summary of the incident formatted for law enforcement and prosecution use, auto-generated from the above data. Must include a plain-language timeline of events that a non-technical person (magistrate, prosecutor, senior police officer) can read and understand without technical training
signature.sha256. The cryptographic signature file (see REQ-EV-02)
REQ-EV-02 | MANDATORY — SHA-256 Signing Protocol
The SHA-256 signing process must conform to the following protocol without deviation:
All evidence files are assembled on the Jetson at the time of incident
A SHA-256 hash is computed for each individual file
A master manifest file (manifest.json) is created listing every file in the package with its individual hash and the exact UTC timestamp of creation
The manifest itself is SHA-256 hashed to produce the package signature
The package signature, the Jetson device ID, the site ID, the incident ID, and the UTC timestamp are all combined into a single string
This string is hashed with HMAC-SHA256 using a device-specific private key provisioned at deployment
The resulting HMAC is the final package signature stored in signature.sha256
The signing happens on the Jetson, not in the cloud. The cloud receives the signed package and stores it. The cloud cannot modify a signed package, any modification invalidates the signature
Verification is performed by recomputing the HMAC using the device’s public key, which is stored in the Device Management Service and never leaves the platform
REQ-EV-03 | MANDATORY — Real-Time Push During Incident
As soon as a confirmed threat is detected, the Jetson begins pushing evidence to the cloud vault in real time. The push is not queued until the incident ends. It streams continuously throughout the incident. The sequence is: (1) incident_metadata.json pushed immediately, (2) event_log.json updated and re-pushed every 30 seconds, (3) video streams pushed as they are recorded in minimum 30-second chunks, (4) face and plate captures pushed within 10 seconds of capture. If site backhaul drops during an incident, all buffered data is pushed as soon as connectivity restores. Local NVMe SSD recording continues throughout any connectivity loss with zero evidence gap.
REQ-EV-04 | MANDATORY — Package Immutability
Once a package is signed and pushed to the cloud vault, it cannot be modified, replaced, or deleted from within the platform. An immutable storage backend must be used for the evidence vault. Any attempt to modify a package must be logged as a security event. Package deletion requires a judicial order and is executed by a separate administrative process with a full audit trail.
4.4 RAPID Dispatch System
REQ-RAPID-01 | MANDATORY — Responder Network
The RAPID system maintains a database of enrolled responders. Each responder record contains: responder ID, name, agency/unit, badge number, verified mobile number, device token for push notifications, GPS location (updated in real time when the responder’s RAPID app is active), assigned coverage zones, and availability status.
REQ-RAPID-02 | MANDATORY — Dispatch Algorithm
When a confirmed threat is detected at a site, the RAPID service executes the following dispatch sequence:
Query all available responders within a configurable radius of the incident GPS coordinates (default: 15km for the initial query, expanding to 30km if insufficient responders are found)
Rank results by: (a) straight-line distance from current GPS position to incident site, (b) estimated drive time based on road network if available, (c) responder availability status
Dispatch to the top 3 nearest available responders simultaneously
Each responder receives: a push notification with site location, a link to the live camera feed, the distance from their current position, and an accept/decline prompt
If no acceptance is received within 2 minutes, expand the search radius and dispatch to the next available responders
Continue until a minimum of 1 acceptance is confirmed or the search radius reaches its maximum
REQ-RAPID-03 | MANDATORY — Responder Tracking
Once a responder accepts a dispatch, their GPS position must be tracked and transmitted to the RAPID service every 30 seconds until they mark the incident as closed. Their movement is displayed on the Command Centre in real time. The tracking data becomes part of the evidence package.
REQ-RAPID-04 | MANDATORY — Responder Mobile Application
The RAPID responder application is a separate mobile application (iOS and Android) for enrolled security responders. Requirements for this application are covered in the RAPID Application Specification document. The software platform must provide the backend APIs that the RAPID application consumes.
REQ-RAPID-05 | MANDATORY — Response Outcome Documentation
Every RAPID dispatch must produce an outcome record, regardless of what happens. The outcome record includes: dispatch timestamp, responders notified, responders who accepted, time of acceptance, estimated arrival time, actual arrival time (GPS confirmed), outcome classification (apprehension, deterred/fled, false alarm, no response, site inaccessible), and free-text notes from the responding officer. The outcome record is appended to the incident evidence package.
REQ-RAPID-06 | HIGH — CNII Mandate Reporting
The RAPID system must generate a monthly compliance report for each security agency enrolled in the responder network, showing: total dispatches to that agency, total acceptances, average response time, and outcome classifications. This report supports accountability under the CNII Order 2024 mandate.
4.5 Personnel Database System
REQ-PDB-01 | MANDATORY — Database Structure
The personnel database maintains a record for every person authorised to access any ITIPS-protected site. Each record contains: person_id, full name, employer (operator or contractor company), role/designation, enrolment date, enrolled_by (operator user who initiated enrolment), face_embeddings (array of normalised 512-dimensional face embeddings generated by the InsightFace pipeline, see Section 5.4), assigned_sites (array of site IDs), access_status (active/suspended/terminated), and a complete audit log of all changes.
REQ-PDB-02 | MANDATORY — Enrolment Process
The enrolment process must follow this exact sequence:
Operator initiates enrolment from the personnel management dashboard, entering the person’s name, employer, and role
System generates a secure time-limited enrolment URL (valid for 24 hours)
URL is sent to the enrolling technician by the operator (via WhatsApp, email, or SMS, operator’s choice)
Technician opens the URL on their mobile device, which activates a face capture flow requiring: 5 face images at different angles (frontal, left 30°, right 30°, slight upward, slight downward), captured in adequate lighting, with liveness detection to prevent photo spoofing
Images are submitted to the backend Personnel Service for quality validation and embedding generation. The backend runs the InsightFace pipeline (SCRFD + ArcFace) as a cloud microservice to: validate image quality (blur check, occlusion check, minimum face size), verify liveness, and generate the 512-dimensional face embeddings. This is a backend responsibility. The backend team builds and runs the face embedding generation service in the cloud, not on any Jetson. The Jetson receives the finished embeddings via personnel sync (see REQ-PDB-04). It never generates embeddings from raw images during normal operation
If validation passes, embeddings are stored in the central Personnel database and propagated to all Jetsons at the relevant sites within 15 minutes
If validation fails, the technician receives specific feedback on what to retake and the URL remains valid for retry
The operator receives a confirmation notification when enrolment is complete
REQ-PDB-03 | MANDATORY — Termination Process
When an operator deactivates a personnel record, the following must occur within 15 minutes:
The record is marked as terminated in the central database
A propagation event is pushed to all Jetson devices at sites the person was assigned to
Each Jetson removes the person’s face embeddings from its local personnel cache
The platform logs the deactivation with: who initiated it, when, and which sites were affected
The deactivation is irreversible from within the operator interface, a request to reactivate a terminated record goes to a platform administrator for review
REQ-PDB-04 | MANDATORY — Local Cache on Jetson
Each Jetson maintains a local copy of the face embeddings for all personnel assigned to that site. This local cache is used for real-time face recognition during incidents. The Jetson does not make a network call to the central database for every face comparison. The local cache is encrypted and updated from the central database on a configurable sync interval (default: every 15 minutes) and on every personnel change event.
REQ-PDB-05 | MANDATORY — Maintenance Window Management
A maintenance window record contains: window_id, site_id, person_id, start_time_utc, end_time_utc, scheduled_by, purpose (free text). At the window start time, the Jetson at the affected site receives a disarm instruction for that specific person. At the window end time, the Jetson re-arms automatically. The window is logged and any detection events for that person during the window are recorded as maintenance events (not security incidents).
4.6 Device Management Service
REQ-DM-01 | MANDATORY — Remote Firmware Updates
All Jetson devices and all cameras must support over-the-air firmware and software updates initiated from the Device Management Service. Updates are pushed as cryptographically signed packages. Each device verifies the signature before applying the update. Updates can be targeted to: all devices, all devices at a specific operator’s sites, a specific site, or a specific device. A staged rollout capability is required. The ability to push an update to 1% of devices, monitor for failures, then expand the rollout.
REQ-DM-02 | MANDATORY — Remote Configuration
All configurable parameters on every Jetson and camera must be modifiable remotely from the Device Management Service without requiring physical site access. Configuration changes are versioned and auditable. Rolling back to a previous configuration version must be possible from the dashboard.
REQ-DM-03 | MANDATORY — Device Provisioning
New devices must support zero-touch provisioning. When a new Jetson or camera is connected to a site for the first time, it contacts the provisioning endpoint, authenticates with its factory-provisioned device certificate, receives its site-specific configuration, downloads its initial software version, and becomes operational without manual per-device setup. The provisioning process must complete within 15 minutes of first connection.
REQ-DM-04 | MANDATORY — Health Monitoring
Each Jetson transmits a heartbeat to the Device Management Service every 60 seconds containing: device_id, site_id, timestamp, jetson_temperature, jetson_cpu_load, jetson_memory_available, storage_remaining_gb, ups_battery_percentage, ups_charging_status, ethernet_status, and the status of every connected camera. Any device that misses 3 consecutive heartbeats is flagged as offline and the operator is notified.
4.7 Jetson Sync Agent — Cloud API Contract — MANDATORY FOR BACKEND TEAM
This section defines every API endpoint the Jetson Sync Agent calls to the cloud, and every endpoint the cloud calls back to the Jetson. The Sync Agent is a backend team deliverable that runs on the Jetson hardware. The backend team implements both sides. The Sync Agent client code and the cloud server endpoints.
The AI team does not read or implement this section. The AI team’s only responsibility is dropping correctly structured data packets into the Sync Agent’s local intake. The Sync Agent handles everything from that point forward.
How this works: The Sync Agent communicates over HTTPS with mutual TLS authentication. Every request carries the Jetson’s device certificate. The base URL for all outbound calls is https://api.itips.seismic.io/api/v1. The Jetson also runs a small local HTTPS server on port 8443 to receive inbound commands. The AI team builds this port 8443 server (Section 5), the backend team builds the client that calls it.
Change control: Any change to an endpoint after implementation has begun requires written sign-off from the backend team lead. The AI team is not a party to this contract.


Part A — Outbound from Jetson (AI team implements client, backend team implements server)
A1 — Device Heartbeat
POST /api/v1/devices/{device_id}/heartbeat
Frequency: Every 60 seconds
Payload:
{
  "timestamp_utc": "2026-04-17T09:00:00Z",
  "jetson_temperature_c": 52,
  "cpu_load_pct": 34,
  "memory_available_gb": 8.2,
  "storage_remaining_gb": 820,
  "ups_battery_pct": 87,
  "ups_charging": true,
  "ethernet_status": "connected",
  "cameras": [
    {"id": "cam1", "status": "online", "battery_pct": null, "storage_gb": 210},
    {"id": "cam2", "status": "online", "battery_pct": 76, "storage_gb": 98}
  ],
  "sensors": [
    {"id": "vib_tower", "type": "vibration", "status": "armed", "battery_pct": 91},
    {"id": "door_shelter", "type": "door", "status": "armed", "battery_pct": 88}
  ]
}
Response: 200 OK or 401 Unauthorized (certificate failure)
Backend action: Update fleet health record for this device. Flag device as online. If 3 consecutive heartbeats are missed, flag the device as offline and trigger maintenance alert.

A2 — Sensor Event
POST /api/v1/sites/{site_id}/events
Triggered: Immediately on any above-threshold sensor event
Payload:
{
  "event_id": "uuid-v4",
  "event_type": "vibration | door_open | door_close | power_loss | cable_cut | gate_detection | tamper",
  "timestamp_utc": "2026-04-17T09:01:00Z",
  "sensor_id": "vib_tower",
  "data": {
    "magnitude": 4.7,
    "classification": "cutting",
    "duration_ms": 1200
  }
}
Response: 201 Created with {"event_id": "...", "incident_id": null | "uuid"}
Backend action: Persist event. Apply alert escalation logic. If this event in combination with recent events crosses a high-priority threshold, return an incident_id for the AI team to use in subsequent calls. Otherwise return incident_id: null.

A3 — Create Incident
POST /api/v1/incidents
Triggered: When AI threat confirmation logic determines a confirmed threat (REQ-AI-22)
Payload:
{
  "site_id": "site-ng-0042",
  "incident_type": "confirmed_threat | preliminary_alert",
  "initial_classification": "perimeter_breach | equipment_access | reconnaissance",
  "timestamp_utc": "2026-04-17T09:01:30Z",
  "trigger_event_ids": ["uuid1", "uuid2"],
  "deterrence_fired": true,
  "active_camera_ids": ["cam1_panoramic", "cam1_ptz", "cam2"]
}
Response: 201 Created with {"incident_id": "uuid-v4"}
The AI team uses this incident_id in all subsequent evidence upload and stage update calls for this incident.

A4 — Update Incident Stage
PATCH /api/v1/incidents/{incident_id}/stage
Triggered: Each time the incident advances through a lifecycle stage
Payload:
{
  "stage": "power_cut | cable_cut | perimeter_breach | human_confirmed | shelter_breach | evidence_assembling | complete",
  "timestamp_utc": "2026-04-17T09:02:00Z",
  "classification": "confirmed_theft | deterred | false_alarm | unknown",
  "notes": "Door sensor shelter_main fired at 09:02:01"
}
Response: 200 OK
Backend action: Update incident record. Push stage update to dashboard WebSocket connections. Escalate to high-priority alert if appropriate.

A5 — Upload Evidence Video Chunk
POST /api/v1/incidents/{incident_id}/evidence/video
Triggered: Continuously during active incident, minimum 30-second chunks
Headers: Content-Type: multipart/form-data, X-Camera-ID, X-Chunk-Index, X-Total-Chunks, X-Timestamp-Start-UTC
Payload: Binary video chunk (H.265 encoded)
Response: 202 Accepted with {"chunk_id": "...", "received": true}
Backend action: Store chunk in evidence vault. Assemble chunks in sequence. Do not mark video as complete until all chunks are received.

A6 — Upload Face and Plate Captures
POST /api/v1/incidents/{incident_id}/evidence/media
Triggered: Within 10 seconds of any qualifying face or plate capture
Payload:
{
  "media_type": "face | plate",
  "camera_id": "cam1",
  "timestamp_utc": "2026-04-17T09:02:15Z",
  "confidence": 0.91,
  "ocr_result": "LND-423-XY",
  "image_base64": "..."
}
Response: 201 Created with {"media_id": "..."}

A7 — Complete Evidence Package
POST /api/v1/incidents/{incident_id}/evidence/complete
Triggered: When AI team finalises and signs the evidence package on the Jetson
Payload:
{
  "package_signature": "hmac-sha256-hex-string",
  "signing_timestamp_utc": "2026-04-17T09:45:00Z",
  "jetson_device_id": "jtx-0042",
  "manifest": {
    "files": [
      {"filename": "incident_metadata.json", "sha256": "abc123..."},
      {"filename": "event_log.json", "sha256": "def456..."},
      {"filename": "video_cam1_full.mp4", "sha256": "ghi789..."}
    ],
    "manifest_hash": "overall-manifest-sha256"
  }
}
Response: 200 OK with {"package_status": "verified | signature_mismatch"}
Backend action: Verify the HMAC signature using the device’s stored public key. If verified, mark the package as complete, immutable, and available for download. If a signature mismatch, flag the package for investigation and alert the platform administrator. The backend verifies signatures. It does not create them.

A8 — Request RAPID Dispatch
POST /api/v1/incidents/{incident_id}/dispatch
Triggered: When AI threat confirmation rules engine confirms a threat requiring physical response
Payload:
{
  "site_id": "site-ng-0042",
  "gps_coordinates": {"lat": 9.0578, "lng": 7.4951},
  "incident_classification": "confirmed_threat",
  "threat_level": "high",
  "camera_feed_urls": [
    "rtsp://...",
    "rtsp://..."
  ]
}
Response: 202 Accepted with {"dispatch_id": "...", "responders_notified": 3}
Ownership boundary: The AI team calls this endpoint and receives confirmation that dispatch was triggered. Everything after this point, selecting responders, sending notifications, tracking movement, recording outcomes, is entirely the backend team’s responsibility. The AI team does not implement any RAPID dispatch logic.

Part B — Inbound to Jetson (Backend team implements client, AI team implements local server on port 8443)
The Jetson runs a local HTTPS server to receive commands from the backend. All inbound calls use the same mutual TLS authentication. The backend authenticates to the Jetson using a platform certificate. The Jetson only accepts connections from the authorised backend platform.
B1 — Personnel Sync
POST https://{jetson_local_ip}:8443/local/api/v1/personnel/sync
Triggered: On any personnel change (enrolment, deactivation, maintenance window)
Payload:
{
  "action": "add | update | deactivate",
  "person_id": "person-uuid",
  "full_name": "Firstname Lastname",
  "embeddings": [[0.12, -0.34, ...], [...]],
  "assigned_sites": ["site-ng-0042"],
  "access_status": "active | terminated"
}
Response: 200 OK with {"synced": true, "cache_updated_at": "timestamp"}
AI team action: Update local encrypted personnel cache on NVMe. Confirm sync to backend.
B2 — Configuration Push
POST https://{jetson_local_ip}:8443/local/api/v1/config
Triggered: When backend pushes a configuration change
Payload:
{
  "config_version": "1.4.2",
  "parameters": {
    "vibration_threshold": 3.5,
    "loitering_seconds_outer": 120,
    "loitering_seconds_gate": 30,
    "heartbeat_interval_seconds": 60,
    "pre_event_buffer_minutes": 15,
    "post_event_buffer_minutes": 30
  }
}
Response: 200 OK with {"applied": true, "version": "1.4.2"}
B3 — Maintenance Window
POST https://{jetson_local_ip}:8443/local/api/v1/maintenance/window
Triggered: When a maintenance window is scheduled or cancelled on the dashboard
Payload:
{
  "action": "arm | disarm",
  "window_id": "window-uuid",
  "person_id": "person-uuid",
  "start_utc": "2026-04-17T10:00:00Z",
  "end_utc": "2026-04-17T14:00:00Z"
}
Response: 200 OK
AI team action: When an arm is received, suppress deterrence and RAPID dispatch for this person during the window. When end_utc is reached, re-arm automatically regardless of whether a disarm command is received.
B4 — Remote Commands
POST https://{jetson_local_ip}:8443/local/api/v1/commands
Triggered: On dashboard operator action during active incident
Payload:
{
  "command_type": "ptz_override | deterrence_standdown | request_stream",
  "parameters": {
    "camera_id": "cam1",
    "pan_degrees": 45,
    "tilt_degrees": -20,
    "zoom_x": 15
  },
  "issued_by": "operator-uuid",
  "expires_utc": "2026-04-17T09:15:00Z"
}
Response: 200 OK with {"command_accepted": true} or {"command_accepted": false, "reason": "..."}
AI team action: For ptz_override, send ONVIF PTZ command to Camera 1. For deterrence_standdown, send standdown command to camera only if a registered maintenance window is active for the site (standdown is not permitted during a confirmed threat without an active maintenance window). For request_stream, initiate a WebRTC push stream from the requested camera to the platform media server and return the WebRTC session ID. Do not return a raw RTSP URL. The Jetson must never be directly exposed to external connections.
B5 — Firmware Update Instruction
POST https://{jetson_local_ip}:8443/local/api/v1/firmware/update
Triggered: When backend initiates a staged firmware rollout
Payload:
{
  "version": "2.1.4",
  "download_url": "https://updates.itips.seismic.io/jetson/v2.1.4.tar.gz",
  "signature": "signed-package-hash",
  "schedule_utc": "2026-04-18T02:00:00Z",
  "requires_restart": true
}
Response: 202 Accepted with {"scheduled": true}
AI team action: Download the update package at the scheduled time, verify the signature, apply the update, and report the outcome to the backend via the heartbeat. Updates are never applied during an active incident.

5.1 Overview and Philosophy
The AI layer is the intelligence of ITIPS. It runs entirely on the Jetson edge processor at each site. It must operate without internet connectivity, all AI inference happens at the edge. Cloud connectivity is used by the Jetson Sync Agent for evidence upload and platform communication. The AI pipeline itself never makes a network call.
Critical principle: The AI team must not build from scratch what already exists at production quality. This section identifies the best available tools for each function. The AI team’s job is integration, fine-tuning for Nigerian conditions, pipeline design, and producing correctly structured data for the Jetson Sync Agent’s local intake.
Why GStreamer, not DeepStream
DeepStream has been removed from the ITIPS stack. DeepStream is NVIDIA’s framework for large-scale multi-camera deployments, typically 8, 16, or 32 cameras simultaneously. ITIPS runs 3 camera streams per site (Camera 1 panoramic, Camera 1 PTZ, Camera 2). For 3 streams, DeepStream introduces significant complexity, a steep learning curve, and a heavy dependency that is not justified by the performance gain.
The replacement is GStreamer with NVIDIA CUDA acceleration. GStreamer is the underlying library that DeepStream itself is built on, using GStreamer directly gives the team full control, easier debugging, and all the same hardware acceleration on the Jetson’s GPU. NVIDIA’s nvvideoconvert, nvv4l2decoder, and nvinfer GStreamer plugins provide hardware-accelerated decode and inference on the Jetson without the overhead of the full DeepStream framework.
The AI team builds parallel GStreamer pipelines, one per active camera stream at each site. The number of pipelines is determined by configuration, not hardcoded. For the POC, each site has two cameras, giving two active pipelines per site. The pipeline architecture must support any number of concurrent streams. Each pipeline runs decode, inference, tracking, and output independently. However these pipelines are event-driven, not continuous. See the event-driven architecture section below.
Event-Driven Pipeline Architecture
The ITIPS pipeline is event-driven, not continuous. This is a fundamental architectural decision based on the confirmed hardware and camera capabilities.
All cameras at a site have onboard AI chips that run human detection, vehicle detection, and perimeter breach detection continuously and independently. Every camera fires ONVIF/ISAPI events to the Jetson when it detects something. The Jetson does not need to watch all streams continuously. The cameras do that work themselves.
Normal state: Jetson in standby. Cameras watching and firing ISAPI events on detection. All sensors armed.
On an ISAPI event from camera: Jetson wakes. Pulls the relevant frame from the camera’s RTSP stream at the event timestamp. Crops the detection bounding box. Runs YOLO11n to verify the detection (false alarm filter). If confirmed, runs InsightFace face recognition on the crop. Checks against the personnel database. If unrecognised: incident created, RAPID dispatch triggered, evidence assembly begins.
During the confirmed incident: Jetson actively processes all active camera RTSP streams with YOLO11 to capture additional persons and vehicles during confirmed incidents. All cameras deliver ISAPI events. YOLO11n runs as a verification layer on ISAPI event crops and as active processing during confirmed incidents.
This architecture reduces Jetson compute by approximately 80% compared to continuous stream monitoring, making the Jetson Orin Nano Super (67 TOPS, 8GB RAM) fully capable for the ITIPS workload.
Dahua ISAPI — Camera 1 (Confirmed Available)
All four POC cameras are Dahua. SDK and ISAPI integration is being obtained for all four simultaneously. All four deliver RTSP streams and ISAPI event feeds to the Jetson. The AI team must build the ISAPI event listener and GStreamer pipeline to be fully camera-agnostic — any camera is a stream source identified by a camera ID, not by model name. POC urban site Camera 1 uses DH-SD8C848PA-HNF (8MP, ISAPI full AI feed). POC urban site Camera 2 uses DH-SD5A432XA-HNR (4MP, ISAPI integration required). POC remote site Camera 1 uses SD49425GB-HNR (4MP, TiOC, ISAPI full AI feed). POC remote site Camera 2 uses DH-SDT4E425-4F-GB-A-PV1-S2 (4MP dual sensor, ISAPI integration required). Main product camera is 4MP dual sensor panoramic plus PTZ — same ISAPI integration applies.
The raw Dahua IVS event payload delivers two bounding boxes in every detection event:
"Object": {
  "BoundingBox": [1336, 1608, 4280, 8072],
  "FaceRect": [0, 0, 0, 0],
  "FaceFlag": 0
}
BoundingBox is always populated and contains the full person region. FaceRect is populated when the camera’s own AI has located a face within that person region. When no face is cleanly resolved by the camera, FaceRect returns all zeros.
The Camera 1 face detection pipeline uses a dual-path architecture based on this payload:
Fast path — FaceRect non-zero: The camera has already located the face. The Jetson extracts the face region using the FaceRect coordinates and passes it directly to InsightFace ArcFace for recognition. SCRFD does not run. This is the expected path for clear, frontal, well-lit detections and covers the majority of real-world events.
Fallback path — FaceRect all zeros: The camera detected a person but could not locate a face cleanly, typically due to angle, occlusion, or the person looking away. The Jetson runs InsightFace SCRFD face detection within the BoundingBox region to locate the face. If SCRFD finds a face, the crop is passed to ArcFace. If SCRFD finds nothing, the event is logged as a person detection without face capture.
This architecture is confirmed and closed. See Appendix B item 12.
Dahua ISAPI — All Four POC Cameras
All four POC cameras are Dahua and deliver ISAPI events. The dual-path face detection architecture applies to all cameras identically. When Dahua ISAPI delivers a non-zero FaceRect field, the Jetson extracts the face region directly and passes it to ArcFace — SCRFD does not run. When FaceRect is all zeros, SCRFD runs as fallback on the BoundingBox region. This architecture is confirmed for all four Dahua cameras. SDK and ISAPI integration is being obtained simultaneously for all four. The AI team must confirm FaceRect delivery for each camera model during integration testing and document the results.yet 
Current Development Platform — NVIDIA Jetson Orin Nano Super (ACTIVE)
The confirmed production Jetson is the NVIDIA Jetson Orin Nano Super Developer Kit (67 TOPS, 8GB LPDDR5 RAM). The team has received the Jetson and a confirmed NVMe SSD (1TB WD Blue SN570 M.2 PCIe Gen3). The Jetson is now the active development platform. All development previously described for the Alienware now runs directly on the Jetson. The ONNX workflow below is still valid for model portability but TensorRT conversion now happens immediately on the Jetson. The Alienware is no longer referenced in this document.
ONNX is mandatory as the model intermediate format. Export all models to ONNX on the Alienware. Do not export TensorRT on the Alienware. TensorRT engine files are architecture-specific, an engine compiled on the Alienware’s x86 GPU will not run on the Jetson’s ARM64. The correct workflow is: train and validate on Alienware → export to ONNX → test ONNX inference on Alienware → when Jetson arrives, convert ONNX to TensorRT on the Jetson using trtexec.
GStreamer with NVIDIA plugins runs directly on the Jetson. The nvinfer GStreamer plugin is available for ARM64. Build and test the pipeline on the Jetson directly. Convert ONNX models to TensorRT using trtexec on the Jetson. Document conversion commands and commit them to Git.
Docker containers built on x86 will not run on ARM64 without rebuilding. Build all containers with multi-architecture support from the start.
When working on the Jetson: Flash with JetPack 6.x. Run a simple GStreamer test pipeline with a single RTSP stream to confirm hardware decode works. Then convert ONNX models to TensorRT using trtexec. Document the conversion commands and commit them to Git.
The complete AI pipeline on the Jetson runs in the following order for every video frame received:
Pre-processing: frame decode, resize, colour space conversion
Human and vehicle detection: primary object detection model
False alarm filtering: AI confirmation that detected objects are human or vehicle, not animals, shadows, or environmental movement
Multi-object tracking: persistent ID assignment across frames
Face detection and capture: detect faces, crop best images for recognition
Face recognition: match against local personnel database
License plate detection and recognition: detect and read vehicle plates
Behavioural analysis: classify movement patterns, detect suspicious behaviour
Threat confirmation: aggregate all signals to confirm or dismiss threat
Evidence assembly: package all outputs for signing and upload
5.2 Edge AI Pipeline Framework
REQ-AI-01 | MANDATORY — Primary Framework: GStreamer with NVIDIA CUDA Acceleration The primary video analytics pipeline on the Jetson must be built on GStreamer using NVIDIA’s hardware-accelerated GStreamer plugins for the Jetson platform. This includes nvv4l2decoder for hardware-accelerated video decode, nvvideoconvert for format conversion, and nvinfer for TensorRT inference within the pipeline. GStreamer provides the same hardware acceleration available in DeepStream with significantly lower complexity for a 3-stream deployment.
The AI team builds parallel GStreamer pipelines, one per active camera stream at each site, each running independently. Pipelines are activated on ISAPI detection events from any camera. All active pipelines run simultaneously during confirmed incidents. Detection events from all active pipelines are aggregated by the threat rules engine.
Event-driven activation flow: 1. Any camera fires ISAPI detection event → Jetson pulls frame from that camera RTSP stream at event timestamp → YOLO11n verifies detection → if confirmed, InsightFace runs on detected person crop. 2. During a confirmed incident all active camera pipelines at the site process their RTSP streams with YOLO11n simultaneously. 3. All active pipelines run in parallel throughout the confirmed incident window.
GStreamer documentation: https://gstreamer.freedesktop.org/documentation/
NVIDIA GStreamer plugins for Jetson: https://docs.nvidia.com/jetson/archives/r35.1/DeveloperGuide/text/SD/Multimedia/AcceleratedGstreamer.html
nvinfer plugin (TensorRT inference in GStreamer): https://docs.nvidia.com/metropolis/deepstream/dev-guide/text/DS_plugin_gst-nvinfer.html
REQ-AI-02 | MANDATORY — Model Runtime: NVIDIA TensorRT All inference models deployed on the Jetson must be converted to TensorRT format for optimised edge performance. The nvinfer GStreamer plugin integrates directly with TensorRT. Every model the AI team selects must be convertible to TensorRT with acceptable accuracy loss. FP16 is the default target precision. INT8 quantisation is permitted where accuracy loss is below 2%.
REQ-AI-03 | MANDATORY — Model Training Platform: NVIDIA TAO Toolkit
Where models require fine-tuning for Nigerian conditions (lighting, environment, attack behaviour patterns), the team must use NVIDIA TAO (Train, Adapt, Optimise) Toolkit. TAO provides pre-trained models that can be fine-tuned on custom datasets without requiring deep ML research expertise, and produces TensorRT-compatible outputs directly.
Documentation: https://docs.nvidia.com/tao/tao-toolkit/
Pre-trained models: https://catalog.ngc.nvidia.com/models
5.3 Human and Vehicle Detection
REQ-AI-04 | MANDATORY — Primary Detection Model: YOLO11
The primary object detection model for human and vehicle detection must be YOLO11 (Ultralytics). YOLO11 provides superior accuracy over YOLOv8 with fewer parameters, faster inference, and is specifically benchmarked on the Jetson Orin Nano Super, achieving approximately 47 FPS at FP16 TensorRT precision. It has native TensorRT export and integrates directly with the GStreamer nvinfer plugin via a TensorRT engine file.
Repository: https://github.com/ultralytics/ultralytics
Documentation: https://docs.ultralytics.com/
TensorRT export guide: https://docs.ultralytics.com/integrations/tensorrt/
GStreamer + Jetson integration: https://docs.ultralytics.com/guides/nvidia-jetson/
YOLO11 on Jetson Orin Nano Super benchmarks: https://www.ultralytics.com/blog/ultralytics-yolo11-on-nvidia-jetson-orin-nano-super-fast-and-efficient
The confirmed model is YOLO11n (nano). The lightest and fastest variant, benchmarked at ~47 FPS on the Jetson Orin Nano Super at FP16 TensorRT. YOLO11n with TensorRT INT8 is the deployment target. The AI team must benchmark YOLO11n and YOLO11s on the Jetson hardware and confirm the optimal model size meets the minimum 15 FPS per stream requirement across all 3 active streams during a confirmed incident.
REQ-AI-05 | MANDATORY — False Alarm Rate Target
The human detection model must achieve a false alarm rate below 5% for Camera 1 (Citadel sites) and below 10% for Camera 2 across a 30-day POC period. False alarms are defined as detection events classified as human that are triggered by animals, shadows, blowing debris, or other non-human movement. The AI team must document their false alarm rate during the POC against this target.
REQ-AI-06 | MANDATORY — Nigerian Environment Fine-Tuning
The base YOLO11 model must be fine-tuned on footage from Nigerian tower sites before deployment. The POC sites provide the initial training data. The AI team must collect and label a minimum of 500 images per category (human present, animal movement, shadow movement, harmattan haze, dust cloud) from POC footage before the end of the POC period. Fine-tuning is performed using the TAO Toolkit.
5.4 Face Detection and Recognition
The face detection and recognition pipeline is critical to ITIPS’s prosecution capability. It serves two functions: identifying registered personnel (to suppress false alarms) and capturing unregistered intruder faces for evidence (to support prosecution). These are two different tasks that must not be conflated in the pipeline design.
REQ-AI-07 | MANDATORY — Face Detection: InsightFace SCRFD
Face detection must use InsightFace’s SCRFD (Sample and Computation Redistribution for Efficient Face Detection) model. SCRFD provides state-of-the-art face detection accuracy with efficient inference on edge hardware.
Repository: https://github.com/deepinsight/insightface
SCRFD model: https://github.com/deepinsight/insightface/tree/master/detection/scrfd
Python package: pip install insightface
Documentation: https://insightface.ai
The recommended model is SCRFD-10GF for primary detection. The team must evaluate SCRFD-2.5GF and SCRFD-34GF on the target hardware to confirm the optimal trade-off between speed and accuracy at the operating distances involved.
SCRFD role per camera: SCRFD role is consistent across all four cameras. For any camera that delivers a non-zero FaceRect via ISAPI, SCRFD does not run and ArcFace receives the camera-located face crop directly. SCRFD runs as fallback only when FaceRect is all zeros. The AI team must benchmark SCRFD latency on both paths across all camera models during integration testing and confirm the overall pipeline timing requirement is met.
REQ-AI-08 | MANDATORY — Face Recognition: InsightFace ArcFace
Face recognition (matching a detected face against the registered personnel database) must use InsightFace’s ArcFace implementation. The recommended model bundle is buffalo_l. The highest accuracy bundle available, combining SCRFD-10GF detection with ArcFace recognition, 5-point alignment, and age/gender attributes.
The face recognition pipeline must produce a 512-dimensional embedding for every detected face. This embedding is compared against all embeddings in the local personnel cache using cosine similarity. A match is confirmed when cosine similarity exceeds 0.6 (configurable threshold). A face that does not match any registered person triggers a non-personnel-alert.
REQ-AI-09 | MANDATORY — Face Capture Quality Thresholds
Not every detected face is suitable for prosecution evidence. The AI pipeline must evaluate every face crop against the following quality thresholds before including it in an evidence package:
Minimum face size: 80×80 pixels after alignment
Maximum blur score: 100 (using Laplacian variance, higher is sharper)
Maximum pose deviation: 45 degrees on any axis
Minimum confidence score: 0.7 from the detection model
Only face images that pass all four quality thresholds are included in the evidence package. All face detection events (including low-quality captures) are logged in the event log.
REQ-AI-10 | MANDATORY — Face Capture from Multiple Cameras
The face capture pipeline must be camera-aware. The same physical person appearing in footage from multiple cameras must be identified as the same person (where possible using re-identification, see Section 5.5) and their face captures from all cameras must be included in the evidence package under a unified identity record. This maximises the quality and quantity of face evidence available to prosecutors.
REQ-AI-11 | HIGH — Liveness Detection for Enrolment
The enrolment flow (see Section 4.5) must include liveness detection to prevent an attacker from enrolling using a photograph of an authorised person. The recommended library is InsightFace’s anti-spoofing module. Any approved insightface liveness detection implementation is acceptable.
5.5 Multi-Object Tracking
REQ-AI-12 | MANDATORY — Tracker: ByteTrack
Multi-object tracking must be implemented using ByteTrack. ByteTrack is the recommended tracker because it handles occluded objects correctly, runs at real-time speeds on Jetson hardware, and integrates with the GStreamer pipeline via a custom tracker plugin or Python callback on detection events.
Repository: https://github.com/ifzhang/ByteTrack
Paper: https://arxiv.org/abs/2110.06864
GStreamer integration: Use Gst-nvtracker plugin with ByteTrack as the low-level library, or integrate ByteTrack as a Python callback receiving bounding box outputs from nvinfer
For appearance-based re-identification across cameras at the same site, StrongSORT with OSNet ReID provides better accuracy at the cost of higher compute. The AI team must benchmark both trackers on the Jetson hardware and select the appropriate tracker based on available compute budget.
StrongSORT: https://github.com/mikel-brostrom/yolo_tracking (BoxMOT library)
OSNet (ReID backbone): https://kaiyangzhou.github.io/deep-person-reid/
REQ-AI-13 | MANDATORY — Track Persistence Through Occlusion
The tracking system must maintain a person’s track identity for a minimum of 10 seconds of complete occlusion. An attacker who hides behind an obstacle for 10 seconds and then reappears must be associated with their original track. This requirement directly addresses the attack behaviour of seeking cover during an active incident.
5.6 License Plate Recognition
REQ-AI-14 | MANDATORY — Primary ANPR: Plate Recognizer
Automated Number Plate Recognition (ANPR) must be implemented using Plate Recognizer. Plate Recognizer supports Nigerian license plates, runs on-premise (no cloud call required), provides a REST API, and has documented performance on edge hardware including Jetson platforms.
API documentation: https://docs.platerecognizer.com/
On-premise (Stream) product: https://platerecognizer.com/stream/
Jetson deployment guide: https://platerecognizer.com/alpr-on-jetson-nano-agx/
Supported countries: 90+ including Nigeria
The Plate Recognizer Stream product must be deployed on the Jetson, not the cloud-only Snapshot product. All ANPR inference happens at the edge.
REQ-AI-15 | HIGH — Fallback ANPR: OpenALPR
OpenALPR must be integrated as a fallback ANPR system. If the Plate Recognizer fails to read a plate with confidence above 0.7, the same image is passed to OpenALPR for a second attempt. The result with the higher confidence score is used.
Repository: https://github.com/openalpr/openalpr
Commercial support (Rekor): https://www.openalpr.com/
REQ-AI-16 | MANDATORY — Plate Capture Quality
A plate capture is included in the evidence package only if: the plate is detected with minimum confidence 0.7, the plate text is readable (minimum 4 characters recognised), and the image resolution is sufficient for human verification of the OCR result. All plate detection events are logged regardless of quality.
5.7 Behavioural Analysis
Behavioural analysis is the capability that enables ITIPS to detect threats before the physical attack begins, during reconnaissance, loitering, and approach phases. This capability must not be built from scratch. The following tools provide production-ready behavioural analysis that must be evaluated and integrated.
REQ-AI-17 | MANDATORY — Loitering Detection Loitering detection is implemented as zone-based logic running on ByteTrack outputs. A person whose tracking ID remains within a defined zone polygon for longer than the configured threshold triggers a loitering event. Default thresholds: 120 seconds for the outer perimeter zone, 30 seconds for the gate zone.
REQ-AI-18 | MANDATORY — Line Crossing Detection Perimeter line crossing detection is implemented as directional line logic running on ByteTrack track positions. Define perimeter crossing lines at the compound boundary. Any track that crosses a defined line in the inward direction triggers an alert classified as perimeter breach.
REQ-AI-19 | HIGH — Advanced Behavioural Analysis: NVIDIA TAO Action Recognition
For advanced action and pose-based behavioural classification (crouching, tool-carrying, coordinated group movement), the team must evaluate NVIDIA TAO’s action recognition models as the primary option before considering any alternative.
TAO action recognition: https://docs.nvidia.com/tao/tao-toolkit/text/action_recognition/index.html
Pre-trained action recognition models: https://catalog.ngc.nvidia.com/orgs/nvidia/teams/tao/models/actionrecognitionnet
If TAO action recognition does not meet performance requirements on the Jetson hardware, the following open-source alternatives must be evaluated in this order:
MMAction2 (OpenMMLab), comprehensive action recognition library with many pre-trained models. Repository: https://github.com/open-mmlab/mmaction2. Documentation: https://mmaction2.readthedocs.io/
SlowFast Networks: Facebook AI Research action recognition. Repository: https://github.com/facebookresearch/SlowFast
PoseC3D: skeleton-based action recognition, lighter weight than video-based models. Repository: https://github.com/kennymckormick/pyskl
The AI team must document their evaluation of each option with benchmark results on the Jetson hardware before selecting a final implementation.
REQ-AI-20 | MANDATORY — Camera Vendor AI API Integration
Camera AI APIs are a confirmed and critical component of the ITIPS architecture. The event-driven pipeline (Section 5.1) depends on camera-side AI for primary detection. The following is the confirmed status per camera:
REQ-AI-20 | MANDATORY — Camera Vendor AI API IntegrationAll four POC cameras are Dahua. All four deliver full ISAPI AI event feeds to the Jetson. SDK and ISAPI integration is being obtained for all four cameras simultaneously. The AI pipeline must be built camera-agnostic — every camera is an RTSP stream source identified by a camera ID. No camera model name, model number, or stream count is hardcoded in pipeline logic. Adding, removing, or changing a camera requires only a configuration update.POC CAMERA ISAPI STATUS:Site 1 Camera 1 — Dahua DH-SD8C848PA-HNF — ISAPI full AI event feed confirmed available. Human detection events, vehicle detection events, bounding box coordinates, confidence scores, and timestamps. YOLO11n runs as a verification layer on ISAPI event crops.Site 1 Camera 2 — Dahua DH-SD5A432XA-HNR — ISAPI integration in progress. NOTE: This model is discontinued. POC only. Full ISAPI event feed expected — confirm with SDK during integration.Site 2 Camera 1 — Dahua SD49425GB-HNR — ISAPI full AI event feed confirmed available. TiOC active deterrence accessible via API.Site 2 Camera 2 — Dahua DH-SDT4E425-4F-GB-A-PV1-S2 — ISAPI integration in progress. Dual sensor — panoramic and PTZ — both streams accessible via RTSP.All four cameras: Dahua ISAPI delivers both BoundingBox and FaceRect fields per detection event. The dual-path face detection architecture (fast path when FaceRect is non-zero, SCRFD fallback when FaceRect is all zeros) applies to all four cameras identically. Dahua ISAPI exposes full AI event feed: human detection events, vehicle detection events, bounding box coordinates, confidence scores, and timestamps. The AI team has these endpoints and must build the ISAPI event listener as the primary Phase 0 deliverable on the Jetson. The ISAPI integration replaces continuous YOLO11 watching for Camera 1. YOLO11 runs on demand as a verification layer when ISAPI fires.
  
REQ-AI-21 | DESIRED — Anomaly Detection
Anomaly detection, learning the baseline pattern of activity at a specific site and alerting on statistical deviations from that baseline, is a desired capability for long-term deployment. The AI team should evaluate:
PyOD (Python outlier detection library): https://github.com/yzhao062/pyod
River (online/streaming anomaly detection): https://riverml.xyz/latest/
Isolation Forest (scikit-learn): https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html
Anomaly detection is a Phase 2 capability. It requires a minimum of 30 days of baseline data per site before it can produce reliable results. It must not be deployed as part of the POC or Phase 1 rollout.
5.8 Threat Confirmation Logic
REQ-AI-22 | MANDATORY — Threat Confirmation Rules Engine
The AI layer must implement a rules engine that aggregates all AI signals to produce a final threat classification. The threat classification must not rely on any single signal. The following rules are mandatory:
Power loss alone: log event, call A2 (Sensor Event API), do not fire deterrence
Cable cut alone: log event, call A2 (Sensor Event API), low-priority alert
Power loss + cable cut within 10 minutes: call A2 twice, call A3 (Create Incident) with preliminary_alert, begin pre-event recording capture
Human detection (unregistered) at gate: confirm with AI (face detection cross-checked against local personnel cache), if confirmed non-personnel → call A3 (Create Incident) with confirmed_threat → call A8 (Request RAPID Dispatch) → fire deterrence standdown or confirm → begin evidence assembly and upload via A5/A6
Human detection (registered personnel) during scheduled maintenance window: maintenance event logged only, no API calls that trigger alerts
Human detection (registered personnel) outside scheduled maintenance window: call A2 with classification registered_personnel_unscheduled, call A3 with preliminary_alert, hold for operator confirmation via dashboard before RAPID dispatch is triggered
Ownership reminder: The AI team calls A8 to request RAPID dispatch. The AI team does not implement any RAPID logic. The decision of when to call A8 is the AI team’s. Everything that happens after A8 returns is the backend team’s.
REQ-AI-23 | MANDATORY — Deterrence Firing Decision
Deterrence (strobe and siren) fires from the camera’s own onboard intelligence, not from a command from the Jetson. The camera fires deterrence within 500 milliseconds of its own AI confirming human presence. The Jetson is notified of the deterrence event, logs it, and uses it as an additional signal. The Jetson cannot prevent deterrence from firing once the camera’s onboard AI has confirmed a human. This is by design. The Jetson can send a “stand down” command to the camera only if it determines the detected person is a registered authorised person within an active maintenance window, and only within 2 seconds of the initial detection.
5.9 What the AI Team Must Deliver
The following is the complete list of deliverables expected from the AI engineering team. The AI team does not implement any cloud API calls, that is the Jetson Sync Agent’s responsibility (backend team). The AI team’s output boundary is the Sync Agent’s local intake interface.
Phase 0 — Setup and Development (On Jetson — active now) - Dahua ISAPI event listener built and working. Subscribes to Camera 1 ISAPI events, parses the full IVS event payload including BoundingBox, FaceRect, and FaceFlag fields. Implements dual-path branching: if FaceRect is non-zero, extract face crop and pass directly to ArcFace; if FaceRect is all zeros, run SCRFD on BoundingBox region as fallback. Parses detection type, confidence score, and timestamp. This is the primary Phase 0 deliverable. The team has the ISAPI endpoints and must begin immediately - RTSP frame puller built and working. On ISAPI event receipt, pulls the specific frame from Camera 1’s RTSP stream at the event timestamp and crops the detection bounding box region - InsightFace pipeline (SCRFD + ArcFace) running on the Jetson, face detection and recognition latency benchmarked in milliseconds against test face crops from the ISAPI bounding box - YOLO11n ONNX model loaded via nvinfer GStreamer plugin: human and vehicle detection confirmed on test footage. Role: verification layer on ISAPI event crops and active processing of Camera 2 RTSP during incidents. Speed and accuracy documented. TensorRT conversion runs on Jetson using trtexec - GStreamer pipeline running 3 simultaneous RTSP test streams at minimum 15 FPS using NVIDIA hardware-accelerated plugins (test: Camera 1 panoramic, Camera 1 PTZ, Camera 2, using test streams on Jetson) - Plate Recognizer Stream installed and tested with Nigerian plate samples - ByteTrack integrated as callback on GStreamer pipeline detection outputs, tracking IDs persist through 10-second occlusion test - Zone-based loitering detection implemented and tested, fires at 120-second threshold - Line-crossing detection implemented and tested, fires on compound boundary crossing - AX PRO hub API integration running on Jetson, test sensor connected and sending decoded events to AI pipeline - Threat confirmation rules engine coded, all scenarios in REQ-AI-22 tested with simulated inputs - Local intake interface agreed with backend team: schema document signed by both team leads before Phase 1 begins
Phase 0.5 — Jetson Migration (On Jetson Orin Nano Super) - Jetson flashed with JetPack 6.x. GStreamer hardware-accelerated test pipeline confirmed running on Jetson with single RTSP stream - All ONNX models converted to TensorRT on Jetson using trtexec. Conversion commands documented and committed to Git - Full 3-stream GStreamer pipeline benchmarked on Jetson. FPS, latency, GPU temperature at full load documented - Docker containers rebuilt for ARM64 and tested on Jetson - ISAPI event listener tested end-to-end on Jetson against real Camera 1
Phase 1 — POC Integration (During POC) - Complete AI pipeline integrated end-to-end on the Jetson at both POC sites - False alarm rate measured and documented against targets (REQ-AI-05) across 30 days - Face enrolment end-to-end, backend generates embeddings, Sync Agent receives B1 Personnel Sync, AI pipeline confirms recognition works against synced embeddings - Threat confirmation rules engine tested against all scenarios in REQ-AI-22 at live POC sites - All data types confirmed flowing correctly through Sync Agent intake: heartbeats, sensor events, incidents, stage updates, face and plate captures, evidence packages, dispatch triggers - SHA-256 signing confirmed on Jetson, backend returns verified: true via Sync Agent upload - Evidence packages confirmed in cloud vault for minimum 10 simulated incidents per site
Phase 2 — Evaluation and Optimisation - POC footage labelled and used for YOLO11 fine-tuning on Nigerian conditions using NVIDIA TAO Toolkit - Revised model benchmarked against original, improvement documented - Behavioural analysis (loitering, line crossing) tested and benchmarked at POC sites - Performance report produced covering all AI components across both POC sites

SECTION 6 — HARDWARE
6.1 Overview
This section specifies the hardware requirements for all physical components of the ITIPS system. Hardware vendors are not named in this document, vendor selection and negotiations are managed separately. The specifications here define what the hardware must do. Any hardware that meets these specifications is acceptable. Any hardware that does not is not acceptable regardless of cost, relationship, or convenience.
All hardware must be evaluated and accepted before deployment through a structured POC process. The POC evaluation criteria in Section 6.8 are the acceptance gate for all hardware. Hardware that passes the POC evaluation criteria moves to Phase 1 procurement. Hardware that fails is rejected and replaced.
6.2 Camera 1 — Wired PoE Primary Surveillance and Deterrence Camera
Camera 1 is the primary prosecution evidence camera and the primary active deterrence unit. It is mounted at elevation within or alongside tower antenna hardware, positioned so it is not visible or accessible from the ground.
POC CAMERA CONFIGURATION — TWO SITES, TWO CAMERAS EACH. All four cameras are Dahua. All four deliver RTSP streams and ISAPI events to the Jetson. The AI pipeline and backend must be built to handle any number of concurrent RTSP streams — not hardcoded to two cameras. If a camera changes model or an additional camera is added the software must accommodate it without architectural rework.SITE 1 — URBAN SHOWCASE SITE (primary demonstration site for operator and stakeholder visits):Camera 1 (elevation): Dahua DH-SD8C848PA-HNF. 8MP PTZ. 48x optical zoom. 250 metre IR range. WizMind AI. No TiOC active deterrence. Primary role: long-range evidence capture and identification. ISAPI full AI event feed available.Camera 2 (shelter wall / ground level): Dahua DH-SD5A432XA-HNR. 4MP PTZ. 32x zoom. NOTE: This model is discontinued by Dahua. It is used for the POC only and must not be specified for main product or Phase 1. For POC purposes it functions as a secondary evidence angle at ground level. ISAPI integration required.SITE 2 — REMOTE OPERATIONAL SITE (proves autonomous operation without supervision):Camera 1 (elevation): Dahua SD49425GB-HNR. 4MP PTZ. 25x optical zoom. TiOC active deterrence — strobe, siren, speaker. Auto Tracking. Proven demo unit already in possession. Primary role: surveillance, auto tracking, and deterrence at elevation. ISAPI full AI event feed available.Camera 2 (shelter wall / ground level): Dahua DH-SDT4E425-4F-GB-A-PV1-S2. 4MP dual sensor — panoramic 180° plus PTZ 25x zoom simultaneously. TiOC active deterrence. Auto Tracking 3.0. This is the S2 updated variant. Secondary role at this site: ground level deterrence and second evidence angle.Main product confirmed: single 4MP dual sensor panoramic plus PTZ camera per site with 5-hour battery backup, built-in 4G LTE SIM, and seamless PoE to battery switchover. See Section 6.2 for full specification.: 8MP panoramic + 4MP PTZ dual-lens, 25x optical zoom, Auto Tracking 3.0, active deterrence, Smart Dual Light, PoE+, IP66, -40°C to +60°C, ISAPI confirmed available.
POC cameras are wired PoE. They have no built-in battery or 4G modem. Power and data run on a single PoE+ cable routed through the tower structure to the equipment shelter. If that cable is cut, the camera goes offline. This is accepted for the POC. The Jetson continues operating on its own UPS and solar power. All sensors remain active. The LoRa mesh relay ensures neighbouring sites detect the site going dark and relay an alarm to the backend. For the main product, Camera 1 will have built-in battery backup and 4G SIM so it continues operating through a cable cut.
PHYSICAL DESIGN
Req ID
Priority
Requirement
C1-PD-01
MANDATORY
Camouflage housing designed to blend visually with tower antenna hardware and metalwork. Must not be visually identifiable as a security camera from ground level. Housing colour, texture, and form factor must match the aesthetic of antenna equipment at Nigerian tower sites.
Vendors must propose specific camouflage treatments for review.
C1-PD-02
MANDATORY
Compact form factor suitable for concealment within or alongside antenna arrays at height. Maximum dimensions to be agreed based on site survey data from the two POC sites.
C1-PD-03
MANDATORY
IP67 rated minimum. Fully sealed against dust and water ingress. Harmattan dust penetration is a primary concern.
C1-PD-04
MANDATORY
Operating temperature: -10°C to 65°C continuous. Full operational capability at 45°C ambient — documented peak temperature at northern Nigerian tower sites during dry season.
C1-PD-05
MANDATORY
Reinforced metal housing resistant to physical attack. Must sustain attempts to dislodge or destroy using tools accessible from the ground without falling from its mount.
C1-PD-06
MANDATORY
Anti-corrosion treatment on all external surfaces. Coastal and Niger Delta deployments expose hardware to salt air and high humidity simultaneously. Salt spray resistance: minimum 500 hours to ISO 9227.
C1-PD-07
HIGH
Anti-vandal rated mounting bracket. The camera must remain operational and correctly oriented after attempted physical interference with the mount from below using a pole or similar instrument.
C1-PD-08
HIGH
Lens coating rated for continuous UV exposure in tropical conditions. Lens image quality must not degrade materially within a 5-year operational lifespan under Nigerian solar irradiance.

POWER ARCHITECTURE — WIRED PoE ONLY
Req ID
Priority
Requirement
C1-PWR-01
MANDATORY
PoE+ (IEEE 802.3at) as the sole power and data source. Single cable delivers both. The camera operates in full-capability mode on PoE at all times.
C1-PWR-02
MANDATORY
PoE loss is treated as a detection event. The camera going offline is logged with timestamp and transmitted to the Command Centre as a low-priority alert. Combined with prior camera detection events this escalates to high-priority.
C1-PWR-03
MANDATORY
The PoE switch supplying Camera 1 must be connected to the Jetson UPS circuit, ensuring Camera 1 remains powered through a tower power cut for the duration of the UPS battery runtime.

CONNECTIVITY — WIRED ETHERNET ONLY
Req ID
Priority
Requirement
C1-CON-01
MANDATORY
Wired ethernet as the sole data channel, delivered via the PoE+ cable. RTSP streams transmitted over ethernet at all times.
C1-CON-02
MANDATORY
H.265 video compression as default with H.264 fallback.
C1-CON-03
MANDATORY
Dual stream: high-resolution main stream (minimum 4MP on the PTZ channel, 8MP on the panoramic channel) for local recording and evidence; lower-resolution substream for live dashboard viewing during incidents.
C1-CON-04
HIGH
Local SD card storage, minimum 256GB supported. Local recording continues even if Jetson or network connectivity fails.
C1-CON-05
MANDATORY
WiFi 802.11ac integrated. Used for maintenance and commissioning access only. Not an operational data path.
C1-CON-06
MANDATORY
Full ISAPI (HTTP API) access for AI event delivery to Jetson. Camera fires detection events including bounding boxes and confidence scores to the Jetson via ISAPI. This is the confirmed integration path — see REQ-AI-20 and Section 5.1.
C1-CON-07
MANDATORY
ONVIF Profile S and Profile T compliance for PTZ control and event notification.

OPTICAL — PANORAMIC LENS
Req ID
Priority
Requirement
C1-OPT-01
MANDATORY
Minimum 8MP panoramic wide-angle lens, minimum 180° horizontal field of view. Provides full compound situational awareness in a single frame.
C1-OPT-02
MANDATORY
Full colour imaging in low light without infrared switching. Night attacks are the primary scenario. Colour footage is required for prosecution — infrared black and white is not sufficient for face and clothing identification at prosecution standard.
C1-OPT-03
MANDATORY
Minimum 30 metres effective colour night vision range without supplemental white light.
C1-OPT-04
HIGH
Integrated white light supplemental illumination activating on detection event only (not continuous). Event-triggered white light illumination at time of face capture significantly improves evidence quality.

OPTICAL — PTZ ZOOM LENS
Req ID
Priority
Requirement
C1-PTZ-01
MANDATORY
Minimum 25x optical zoom. Maximum 30x optical zoom. No digital zoom substitution. Optical zoom in this range is required to capture face detail at prosecution evidence standard from mount height to compound perimeter distance.
C1-PTZ-02
MANDATORY
Full 360° continuous pan rotation with no mechanical stop.




C1-PTZ-03
MANDATORY
Minimum 90° tilt range. The camera must track from near the base of the tower to the far perimeter fence from its mounted position.
C1-PTZ-04
MANDATORY
Auto-tracking capability via ONVIF. The camera locks on to a detected person and maintains tracking automatically across the full compound without operator intervention. Tracking update rate sufficient to follow a person moving at running pace.
C1-PTZ-05
MANDATORY
Tracking survives occlusion — camera must relocate and reacquire target when they reappear rather than abandoning the track. Minimum 10-second occlusion tolerance.
C1-PTZ-06
MANDATORY
Vehicle plate capture capability. PTZ lens must achieve sufficient resolution at operating distances to capture vehicle registration plates in both daylight and low light conditions.
C1-PTZ-07
HIGH
Preset tour capability when no active tracking event is in progress.
C1-PTZ-08
HIGH
PTZ speed sufficient to lock on to a detected person within 2 seconds of receiving a tracking command via ONVIF.

ACTIVE DETERRENCE
Req ID
Priority
Requirement
C1-DET-01
MANDATORY
Built-in white strobe light firing autonomously from the camera’s own onboard intelligence within 500 milliseconds of human detection. Strobe must fire independently of the Jetson, the Hub, and all other external system components. If every other ITIPS component is offline this camera still activates its deterrence.
C1-DET-02
MANDATORY
Built-in siren with minimum 110 dB, maximum 130 dB output from housing. Vendors must confirm achievable output levels within the camouflage housing constraints during POC. Audio deterrence fires simultaneously with the strobe on the same detection trigger.
C1-DET-03
MANDATORY
Deterrence activates from the camera’s own AI chip without external command. The camera is not dependent on receiving an instruction from any other device to fire deterrence.
C1-DET-04
MANDATORY
Maximum deterrence activation time: 500 milliseconds from detection to strobe fire.
C1-DET-05
HIGH
Configurable deterrence schedule — active during night hours, standing down during registered maintenance windows. Configurable remotely.
C1-DET-06
HIGH
Two-way audio. Operator or AI-generated audio warning broadcast through integrated speaker on detection, in addition to the siren.

AI AND ANALYTICS
Req ID
Priority
Requirement
C1-AI-01
MANDATORY
Onboard AI chip providing human detection with under 5% false alarm rate in outdoor conditions including animal movement, tree shadow, and blowing debris. Camera’s AI is the deterrence trigger.
C1-AI-02
MANDATORY
Full REST API access to all onboard AI outputs: detection events, confidence scores, bounding box coordinates, classification results. The Jetson must be able to receive all AI outputs in real time. This is non-negotiable.
C1-AI-03
MANDATORY
ONVIF Profile S and Profile T compliance for PTZ control and event notification.
C1-AI-04
MANDATORY
Complete REST API documentation and Python SDK provided. The ITIPS AI pipeline is built in Python and requires programmatic API access.
C1-AI-05
HIGH
Face detection output via API — face crop images delivered to the Jetson on detection.
C1-AI-06
HIGH
Vehicle detection and plate recognition via API.
C1-AI-07
HIGH
Loitering detection — alert if a person remains in a defined zone beyond the configurable threshold. Via API.
C1-AI-08
HIGH
Line crossing detection with configurable direction. Via API.
C1-AI-09
DESIRED
Behavioural analysis models exposed via API — coordinated group movement, tool-carrying, crouching. If the camera vendor has trained models for these behaviours, API access is required.

MANAGEMENT AT SCALE
Req ID
Priority
Requirement
C1-MGT-01
MANDATORY
Remote firmware update over network. Manual firmware updates at 44,000 sites are not operationally possible.
C1-MGT-02
MANDATORY
Remote configuration management for all parameters without physical site access.
C1-MGT-03
MANDATORY
Health status reporting via API: online/offline, power status, battery level, storage status, tamper status, temperature. On configurable heartbeat interval.
C1-MGT-04
HIGH
Bulk provisioning — configuration pushed from central platform to new devices on first connection without manual per-device setup.
C1-MGT-05
HIGH
Tamper detection — alert via API if the camera is physically moved, repositioned, or its view is obstructed.

6.3 Camera 2 — Fully Wireless Independent Backup Surveillance Camera
Camera 2 provides a second evidence angle at ground level complementing Camera 1 at elevation. It is wired PoE and connects to the Jetson via ethernet through the PoE switch. Camera 2 is a POC-only configuration. The main product deploys a single camera per site.
Camera 2 details are documented in the Camera 1 section above alongside the full four-camera POC configuration. See Section 6.2. Camera 2 is POC-only. The main product deploys one camera per site.CRITICAL ARCHITECTURAL NOTE FOR AI AND BACKEND TEAMS: The camera pipeline must be built camera-agnostic. Every camera — regardless of model, position, or site — is treated as an RTSP stream source delivering an ISAPI event feed. The pipeline must support any number of concurrent streams dynamically. Adding a camera, removing a camera, or changing a camera model must not require code changes — only configuration updates (stream URL, camera ID, site assignment). This applies to the POC four-camera configuration and to any future deployment scale. No camera model name, model number, or hardcoded stream count must appear in pipeline logic. All camera references in code use a camera ID assigned at provisioning time.POC confirmed models: Site 1 Camera 2 — Dahua DH-SD5A432XA-HNR: 4MP PTZ, 32x optical zoom, IP66, active deterrence. NOTE: Discontinued model, POC only. Site 2 Camera 2 — Dahua DH-SDT4E425-4F-GB-A-PV1-S2: 4MP dual sensor panoramic plus PTZ, 25x zoom, TiOC active deterrence, Auto Tracking 3.0. Both cameras connect to the Jetson via ethernet. Both deliver RTSP streams and ISAPI events.
Camera 2 is not the primary prosecution evidence camera. Its design priorities, in order, are: (1) survivability and independence, (2) continuous operation, (3) image quality sufficient for prosecution evidence.
PHYSICAL DESIGN
Req ID
Priority
Requirement
C2-PD-01
MANDATORY
Camouflage housing designed to blend with the tower site environment. Must not be immediately identifiable as a camera from ground level.
C2-PD-02
MANDATORY
Compact self-contained unit with solar panel, battery, camera, and 4G modem in a single sealed housing or minimal two-piece assembly.
C2-PD-03
MANDATORY
IP67 rated minimum. No external connectors or access points that allow dust or water ingress under normal operation.
C2-PD-04
MANDATORY
Operating temperature: -10°C to 65°C continuous. Full operational capability at 45°C ambient. The solar panel must maintain specified charging output at this temperature.
C2-PD-05
MANDATORY
Anti-corrosion on all external surfaces and solar panel frames. Salt spray resistance: minimum 500 hours to ISO 9227.
C2-PD-06
MANDATORY
Reinforced housing resistant to physical attack. Must remain operational and recording after an attempted strike with a hand tool.

POWER SYSTEM — FULLY INDEPENDENT
Req ID
Priority
Requirement
C2-PWR-01
MANDATORY
Completely independent solar power. No connection to tower power, ITIPS main solar, or any other power source on site.
C2-PWR-02
MANDATORY
Minimum 10 days continuous operation on battery alone without solar input. This covers extended rainy season and harmattan conditions.
C2-PWR-03
MANDATORY
Solar panel sized for Nigerian solar irradiance across all six geopolitical zones including harmattan conditions where dust reduces panel efficiency. Vendors must provide irradiance calculations.
C2-PWR-04
MANDATORY
Battery status reporting to Jetson and platform on configurable heartbeat. Low battery warning at minimum 48 hours before projected exhaustion.
C2-PWR-05
MANDATORY
Power management firmware reduces frame rate and transmission frequency in low battery conditions. Camera prioritises detection capability over video quality below configurable battery threshold.
C2-PWR-06
HIGH
Solar panel efficiency minimum 20% under standard test conditions.
C2-PWR-07
HIGH
LiFePO4 battery chemistry. Mandatory for thermal stability at sustained 45°C ambient.

CONNECTIVITY
Req ID
Priority
Requirement
C2-CON-01
MANDATORY
Camera 2 connects to the Jetson via ethernet through the PoE switch. Camera 2 is PoE powered and does not have an independent 4G SIM or solar power. It operates as a second evidence capture angle at ground level. If ethernet is cut, Camera 2 goes offline alongside Camera 1. This is acceptable for the POC — the Jetson continues operating, sensors remain armed, and the LoRa mesh relay handles alarm communication.
C2-CON-02
MANDATORY
Nigerian 4G LTE frequency band compatibility confirmed before supply.
C2-CON-03
MANDATORY
Event-triggered transmission as default. Camera records locally at all times. Video transmitted over 4G only on detection event or live view request. Idle: heartbeat only.
C2-CON-04
MANDATORY
Local storage minimum 128GB. Recording continues regardless of 4G connectivity.
C2-CON-05
MANDATORY
Automatic queue and upload of recorded footage when connectivity is restored after outage. Zero data loss during connectivity gaps.
C2-CON-06
MANDATORY
RTSP stream available to Jetson over 4G for AI analysis.
C2-CON-07
HIGH
WiFi 802.11ac for maintenance and commissioning access.
C2-CON-08
HIGH
Data consumption reporting per SIM via API. Essential for SIM management at scale.
C2-CON-09
HIGH
5G ready modem hardware for future upgrade path without hardware replacement.

OPTICAL
Req ID
Priority
Requirement
C2-OPT-01
MANDATORY
Minimum 5MP resolution. Sufficient for human identification and scene documentation at prosecution evidence standard.
C2-OPT-02
MANDATORY
Minimum 120° horizontal field of view. Backup coverage for all wired cameras that may be offline.
C2-OPT-03
MANDATORY
Full colour imaging in low light. Infrared black and white is not sufficient for evidence.
C2-OPT-04
MANDATORY
Minimum 20 metres effective colour night vision range.
C2-OPT-05
HIGH
Pan-tilt motorised lens. 355° pan, minimum 90° tilt. The power budget for PTZ must not compromise the 10-day battery autonomy requirement. Vendors must demonstrate both requirements can be met simultaneously before POC.
C2-OPT-06
HIGH
Minimum 8x optical zoom. Sufficient to close in on specific compound areas during an active incident.
C2-OPT-07
HIGH
Hydrophobic and oleophobic lens coating. Harmattan dust accumulation on the lens degrades image quality over weeks of exposure.

AI, MANAGEMENT, AND SCALE
Camera 2 AI, management, and scale requirements mirror Camera 1 requirements C1-AI-01 through C1-AI-08 and C1-MGT-01 through C1-MGT-05, with the following modifications:
False alarm rate target: below 10% (vs 5% for Camera 1)
API communication is over 4G, not ethernet. API latency requirements must account for mobile network latency
Jamming detection applies at the site level via the Jetson. If the Jetson detects that its 4G fallback path is being deliberately interfered with it logs the event locally with timestamp as evidence. The jamming event is transmitted via LoRa mesh relay to the neighbouring site and onwards to the backend.
6.4 Gate Sensor — PIRcam
The gate sensor is an outdoor IP66-rated wireless magnetic contact sensor mounted on the gate structure. It fires the moment the gate is physically opened or forced — whether with a key or by force. It is the access point detection layer. Its position on the gate is not negotiable. The gate magnetic contact catches inside jobs where someone opens the gate quietly with a legitimate key — a scenario a camera watching the compound may not immediately classify as a threat. The magnetic contact fires independently of the camera and adds a physical access event to the evidence package regardless of whether the camera has detected movement.
Confirmed model: Hikvision DS-PDPC12PF-EG2-WE(B): AX PRO ecosystem PIRcam, 868MHz wireless, battery powered, 2MP camera with PIR trigger, IP66, operating temperature -10°C to 55°C. Integrates directly with the confirmed AX PRO hub. NOTE: Gate PIRcam role has changed. The PIRcam is now deployed at the generator area to capture visual evidence of anyone approaching the generator and diesel tank. The gate is now covered by a dedicated outdoor IP66 magnetic contact sensor. See updated sensor stack in Section 6.10.
Temperature note for northern Nigeria deployments: The DS-PDPC12PF-EG2-WE(B) is rated to 55°C. Ambient air temperature at northern Nigerian tower sites reaches 45°C in dry season, which is within specification. The risk is radiant heat from direct sun on exposed metal gate frames, which can push surface temperature 10–15°C above ambient. This is managed through installation practice. The hardware team must mount the PIRcam on the shaded side of the gate post, or install a small shade bracket above the housing. This is standard practice for outdoor security hardware in West Africa and is a mandatory item in the site installation checklist for all sites above the 10th parallel. It does not require a different product.
REQ-GATE-01 | MANDATORY
The gate magnetic contact must be battery powered with no cable connection to any other component on site. It communicates with the Jetson via the AX PRO hub (DS-PWA64-Kit-WB confirmed for POC, DS-PWA96-M-WE for main product) over 868MHz wireless. IP66 outdoor rated minimum. Fires immediately on gate opening or forced entry. No separate wireless receiver is required.
REQ-GATE-02 | MANDATORY
The gate magnetic contact event is transmitted to the Jetson within 2 seconds of activation. The event includes timestamp, site ID, sensor ID, and event type (opened or forced). The camera at elevation simultaneously covers the gate approach zone and captures visual evidence of whoever opened the gate. The magnetic contact and camera events are combined in the evidence package as independent confirmations of the same access event.
REQ-GATE-03 | MANDATORY
Face capture at the gate is handled by the camera at elevation which covers the gate approach zone continuously. The magnetic contact sensor provides the physical access event. The camera provides the visual identification. These are two independent detection systems that complement each other — the magnetic contact fires on gate opening regardless of camera detection, and the camera fires on human detection regardless of gate status.
REQ-GATE-04 | MANDATORY
Battery life must be sufficient for minimum 30 days of operation without replacement under typical site access frequencies (average 2–5 access events per day).
REQ-GATE-05 | MANDATORY
Shaded mounting on gate post. The sensor must not be mounted on a surface exposed to direct solar radiation without a shade bracket. This requirement applies to all sites and is mandatory for sites above the 10th parallel. The hardware team must include shade brackets in the site installation kit.
REQ-GATE-06 | HIGH
Vandal resistant housing. The gate sensor is at ground level and is accessible to attackers. Housing must withstand a direct strike without dislodging from mount or interrupting operation.
6.5 Edge Processor — Jetson
The Jetson edge processor is the intelligence hub at each site. It runs all AI inference, manages evidence assembly, coordinates the camera network, and communicates with the national platform.
REQ-JETSON-01 | MANDATORY — Hardware Specification
The confirmed production hardware is the NVIDIA Jetson Orin Nano Super Developer Kit: 67 TOPS AI performance, 8GB LPDDR5 RAM, NVIDIA Ampere GPU with 1024 CUDA cores and 32 tensor cores, 6-core ARM Cortex-A78AE CPU. This hardware has been benchmarked with YOLO11 and confirmed capable of the ITIPS 3-stream event-driven pipeline. No hardware escalation is required or permitted within current budget constraints.
Key physical characteristics of the carrier board relevant to ITIPS integration: 2x M.2 Key M slots (PCIe Gen3) — one for NVMe SSD. 1x M.2 Key E slot (pre-populated with Wi-Fi module). 4x USB 3.2 Gen2 Type-A. 1x Gigabit Ethernet port connected to site network via unmanaged switch. 40-pin GPIO expansion header. No mPCIe slot. No SIM slot.
REQ-JETSON-02 | MANDATORY — Storage
NVMe SSD minimum 1TB. M.2 2280 form factor. PCIe Gen3 interface. SATA SSDs are not compatible with this hardware. Gen4 NVMe drives will function at Gen3 speeds but Gen3-native drives are preferred to avoid wasted cost. ITIPS uses event-based recording not continuous recording. At 8MP H.265 event clips averaging 1 minute each, 1TB provides approximately 333 days of event storage. 2TB is not required. Confirmed BOM item: WD Blue SN570 1TB M.2 2280 NVMe PCIe Gen3 or equivalent.
REQ-JETSON-03 | MANDATORY — UPS Power Architecture
The Jetson must have an independent uninterruptible power supply that maintains operation when tower power is cut. The Jetson Orin Nano Super Developer Kit uses a 19V DC barrel jack power input, pogo-pin UPS modules designed for other Jetson variants (Orin NX, Xavier NX) are not compatible and must not be used.
The required implementation is an external DC inline UPS supporting 19V DC input/output at minimum 3A, with LiFePO4 battery chemistry rated for 45°C ambient operation, and minimum 4-hour runtime at 25W full load. The UPS sits in-line between the site power supply and the Jetson DC jack. The UPS communicates battery status to the Jetson via USB or I2C, integrating battery status monitoring into the Jetson health reporting.
REQ-JETSON-04 | MANDATORY — Solar Power
The Jetson must have a dedicated solar power system independent of the main tower power supply. This solar system charges the external UPS battery when external power is available. The solar panel and charge controller must be sized to maintain the UPS battery above 80% under Nigerian solar irradiance conditions during normal operation.
REQ-JETSON-05 | MANDATORY — Connectivity
The Jetson Orin Nano Super Developer Kit has one Gigabit Ethernet port and no SIM slot. Connectivity is via site backhaul ethernet only. A 5-port unmanaged switch is required inside the enclosure: one port connects to site backhaul, one port connects to the PoE switch for cameras, one port connects to the Jetson. The Sync Agent queues all outbound data locally when backhaul is unavailable and drains the queue when connectivity restores, no 4G fallback is available or required on the Jetson.
REQ-JETSON-06 | MANDATORY — Physical Security Enclosure
The Jetson and its associated hardware (UPS, storage, connectivity) must be housed in a tamper-evident locked metal enclosure installed inside the equipment shelter. The enclosure must have the following properties:
Steel construction, minimum 2mm wall thickness
Locking mechanism requiring a unique key per site (no master key system)
Tamper-detection sensor (magnetic reed switch or accelerometer) that triggers a tamper alert to the platform if the enclosure is opened without a scheduled maintenance window active
Cable entry points sealed against dust ingress
Ventilation adequate to maintain internal temperature below 70°C at 45°C ambient external temperature
Wall-mounted, not floor-standing, to resist flooding and vermin access
REQ-JETSON-07 | MANDATORY — Software Environment
Operating system: Ubuntu 22.04 LTS on JetPack 6.x. The team must not use any OS version that is not supported by the current JetPack release and its bundled GStreamer NVIDIA plugins. All ITIPS application software must run in Docker containers for portability, version management, and isolation. Container orchestration using Docker Compose is acceptable for the POC. Kubernetes on the Jetson is a HIGH requirement for Phase 1.
REQ-JETSON-08 | MANDATORY — Boot Recovery
The Jetson must implement a watchdog process that monitors all critical ITIPS services and restarts them automatically on failure. A hardware watchdog timer must be configured to reboot the Jetson if the software watchdog itself fails. The system must return to fully operational state within 3 minutes of an unexpected reboot without human intervention.
6.6 Vibration Sensor
The vibration sensor is an additive detection layer for fence and tower structure attacks. It is not part of the POC hardware. Where vibration sensors are added to future deployments they will integrate via the AX PRO hub or LoRaWAN depending on the supplier’s protocol. The Jetson pipeline is designed to accept vibration sensor events through configuration updates only — no code changes required. The specifications below describe the functional requirements any future vibration sensor must meet.
Vibration sensors are not deployed in the POC. For the main product, a minimum of two vibration sensors per site is the target specification — one on the tower structure lower third and one on the equipment shelter exterior wall. Additional sensors on secondary perimeter structures or equipment cabinets are recommended for high-risk sites. All sensors must communicate wirelessly to the Jetson via AX PRO hub or LoRaWAN. No cable connection to any other component.
PHYSICAL AND ENVIRONMENTAL
Req ID
Priority
Requirement
VIB-PD-01
MANDATORY
Compact weatherproof housing. IP66 minimum. Suitable for permanent outdoor installation on metal tower structures and concrete/metal shelter walls.
VIB-PD-02
MANDATORY
Operating temperature: -10°C to 65°C. Full operational capability at 45°C ambient.
VIB-PD-03
MANDATORY
Anti-tamper housing. Physical removal or striking of the sensor must generate a tamper event logged by the Jetson.
VIB-PD-04
HIGH
Low visual profile. Sensor should not be immediately identifiable as a security device from casual inspection.

DETECTION
Req ID
Priority
Requirement
VIB-DET-01
MANDATORY
Detects vibration events consistent with cutting (angle grinder, hacksaw), impact (hammer, crowbar), and sustained physical pressure on the mounting surface.
VIB-DET-02
MANDATORY
Configurable sensitivity threshold. The threshold must distinguish between attack-level vibration and ambient vibration from wind loading, generator operation, and wildlife contact. Calibration must be performed per-site during installation to account for local ambient vibration profiles.
VIB-DET-03
MANDATORY
Transmits vibration event to Jetson within 2 seconds of threshold crossing. Event payload includes: sensor_id, site_id, timestamp_utc, vibration_magnitude (raw value), classification (impact/cutting/sustained_pressure/tamper/unknown), and duration.
VIB-DET-04
MANDATORY
Continuous monitoring — the sensor does not sleep between events. All vibration above a minimum floor threshold is recorded to the local buffer and transmitted on a configurable interval. Only above-threshold events generate immediate alerts.
VIB-DET-05
HIGH
Machine learning-based pattern classification distinguishing attack vibration signatures from ambient noise. Where the sensor vendor provides a pre-trained classification model for common attack tools (angle grinder, hacksaw, impact wrench), this must be activated. Where no vendor model is available, raw magnitude data is passed to the Jetson AI layer for classification.

POWER AND CONNECTIVITY
Req ID
Priority
Requirement
VIB-PWR-01
MANDATORY
Battery powered. No cable connection to any site power source. Battery life minimum 12 months under normal operation (below-threshold ambient monitoring).
VIB-PWR-02
MANDATORY
Battery status reported to Jetson on configurable heartbeat. Low battery warning at minimum 30 days before projected exhaustion.
VIB-CON-01
MANDATORY
Wireless communication to the Jetson via AX PRO hub over 868MHz frequency hopping spread spectrum. For future sensors that do not support the AX PRO protocol, LoRaWAN is the preferred fallback — long range, low battery consumption, and compatible with the existing LoRa hardware on the Jetson. No sensor protocol should be hardcoded in the pipeline. New sensor types integrate through configuration only.
VIB-CON-02
MANDATORY
Communication range sufficient to reach the Jetson enclosure from all sensor mounting positions on site without relay hardware. If direct range is insufficient, the hardware team must propose a relay solution and include it in the site installation specification.
VIB-CON-03
MANDATORY
Encrypted communication. All sensor-to-Jetson communication must be encrypted. Sensors must authenticate with the Jetson on first connection and on every reconnection after a communication gap.

MANAGEMENT
Req ID
Priority
Requirement
VIB-MGT-01
MANDATORY
Remote configuration of sensitivity thresholds and alert parameters via Jetson without physical sensor access.
VIB-MGT-02
MANDATORY
Health status reported to Jetson: online/offline, battery level, last event timestamp, communication quality.
VIB-MGT-03
HIGH
Over-the-air firmware update capability where supported by the sensor hardware.

6.7 Door Sensor
The door sensor is the last physical detection layer in the ITIPS system. When the equipment shelter door is breached, the door sensor fires immediately and the incident classification escalates to confirmed equipment access. The door sensor does not prevent the breach. The breach has already happened and been captured on camera before the door sensor fires. Its value is as an evidence event: the exact timestamp at which equipment was accessed is logged and signed, directly supporting prosecution for theft of specific equipment.
A minimum of one door sensor must be deployed on the main equipment shelter door. Additional sensors must be deployed on any locked cabinet or equipment rack inside the shelter that contains components of sufficient value to warrant individual tracking (batteries, rectifiers, transmission equipment, circuit boards). Each sensor is individually identified in the evidence log.
PHYSICAL AND ENVIRONMENTAL
Req ID
Priority
Requirement
DOOR-PD-01
MANDATORY
Compact two-part magnetic reed switch or equivalent contact sensor. Housing suitable for installation on metal shelter doors and cabinet frames.
DOOR-PD-02
MANDATORY
IP65 minimum for external shelter door installation. Internal cabinet sensors may be IP54.
DOOR-PD-03
MANDATORY
Operating temperature: -10°C to 65°C.
DOOR-PD-04
MANDATORY
Tamper detection. Physical removal or destruction of the sensor generates a tamper event immediately.

DETECTION
Req ID
Priority
Requirement
DOOR-DET-01
MANDATORY
Detects door/cabinet open state (magnetic contact broken) and transmits event to Jetson within 1 second. Event payload includes: sensor_id, site_id, door_label (human-readable: e.g. “Equipment Shelter Main Door”, “Battery Cabinet North”), timestamp_utc, state (open/closed/tamper).
DOOR-DET-02
MANDATORY
Detects door/cabinet close state and transmits closed event with timestamp. The open duration (time between open and close events) is calculated and included in the evidence log. A shelter door that is forced open and never closed until after the incident window is recorded with the full open duration.
DOOR-DET-03
MANDATORY
Distinguishes between a door opened during a scheduled maintenance window (maintenance event — logged but no alert) and a door opened outside a maintenance window (security event — immediate high-priority alert regardless of other system state). A shelter door opened outside a maintenance window is a significant event even if no cameras have triggered — it must generate an alert in all circumstances.
DOOR-DET-04
HIGH
Multi-point door sensors for wide shelter doors — detect partial opening (e.g. a door forced at the bottom but not the top) as well as full opening.

POWER AND CONNECTIVITY
Req ID
Priority
Requirement
DOOR-PWR-01
MANDATORY
Battery powered. No cable connection to any site power source. Battery life minimum 18 months under normal operation (typical shelter access frequency of 2–5 events per week).
DOOR-PWR-02
MANDATORY
Battery status reported to Jetson on configurable heartbeat. Low battery warning at minimum 30 days before projected exhaustion.
DOOR-CON-01
MANDATORY
Wireless communication to Jetson via AX PRO hub over 868MHz. All confirmed POC door and contact sensors are AX PRO ecosystem and communicate through the hub without any additional hardware.
DOOR-CON-02
MANDATORY
Encrypted and authenticated communication. Same requirements as VIB-CON-03.

MANAGEMENT
Req ID
Priority
Requirement
DOOR-MGT-01
MANDATORY
Each sensor has a configurable human-readable label assigned during installation (e.g. “Main Shelter Door”, “Battery Bank A Cabinet”, “Rectifier Cabinet”). This label appears in the evidence log and dashboard alerts.
DOOR-MGT-02
MANDATORY
Remote configuration and health status reporting via Jetson.
DOOR-MGT-03
MANDATORY
Integration with maintenance window system — the Jetson suppresses door alerts for sensors assigned to a site during an active maintenance window for the authorised technician visiting that site. Door events during maintenance windows are logged as maintenance events, not security events.

6.8 Environmental Specifications — All Hardware
All hardware must meet the following environmental specifications. These apply to every component deployed at a tower site. Vendors must provide documented test results for each specification, compliance claims alone are not sufficient.
Req ID
Priority
Requirement
ENV-01
MANDATORY
Ambient temperature: -5°C to 45°C continuous. Peak 50°C for short duration without permanent damage.
ENV-02
MANDATORY
Harmattan: sustained fine dust exposure over weeks. IP67 minimum. All seals must maintain integrity after 3 years of cyclic harmattan exposure. Accelerated dust exposure test data required from vendor.
ENV-03
MANDATORY
Tropical humidity: up to 95% relative humidity non-condensing. Internal condensation resistance required.
ENV-04
MANDATORY
Heavy rainfall: monsoon season rainfall intensity. IP67 minimum from all angles.
ENV-05
MANDATORY
Salt air: minimum 500 hours salt spray resistance to ISO 9227. All external metalwork, fixings, and connectors must meet this rating.
ENV-06
MANDATORY
Solar UV: all external polymer components must not degrade, crack, or discolour within 5-year operational lifespan under Nigerian UV conditions.
ENV-07
HIGH
Wind loading: housing and mounting must withstand sustained winds of 120 km/h without repositioning.
ENV-08
HIGH
Insect resistance: sealed housing with no gaps large enough for insect ingress. Ant and wasp nest formation inside camera housing is a documented tropical deployment failure mode.

6.9 POC Evaluation Criteria
The following criteria are the acceptance gate for all hardware deployed at ITIPS sites. Hardware that passes the POC evaluation criteria moves to Phase 1 procurement. Hardware that fails is rejected and replaced.
Req ID
Priority
Criterion
POC-01
MANDATORY
Human detection false alarm rate: below 5% on Camera 1, below 10% on Camera 2, over a 30-day monitoring period. Measured against total detection events logged.
POC-02
MANDATORY
PTZ auto-tracking lock-on: camera locks on and begins tracking a detected person within 2 seconds of trigger. Measured across minimum 20 test events per camera.
POC-03
MANDATORY
Deterrence activation: strobe and siren fire within 500 milliseconds of Camera 1 human detection trigger, and AX PRO wireless siren fires within 500 milliseconds of gate PIRcam detection. Measured across minimum 20 test events.
POC-04
MANDATORY
Wireless independence: Camera 2 continues recording and transmitting through a simulated cable cut event affecting all wired infrastructure on site. Zero interruption to Camera 2 operation.
POC-05
MANDATORY
Battery performance — NOT APPLICABLE TO POC. POC cameras are PoE powered with no internal battery. Jetson UPS battery performance is tested separately as part of power independence testing.
POC-06
MANDATORY
API integration: all required API endpoints functional and delivering data to the Jetson AI pipeline within the agreed integration timeline. No critical API failures during POC period.
POC-07
MANDATORY
Evidence quality: video footage from all four POC cameras assessed as meeting minimum prosecution evidence standards for face identification at the distances present on each POC site.
POC-08
HIGH
Camouflage effectiveness: no camera identified as a security camera by a standard site access assessment conducted without prior knowledge of camera placement.
POC-09
HIGH
Comparative performance report: a structured comparison of all four POC cameras across POC-01 through POC-07 criteria, including ISAPI event latency per camera and YOLO11 processing latency per camera stream. Results documented per camera ID and site.
POC-10
MANDATORY
Vibration sensor detection — NOT APPLICABLE TO POC. Vibration sensors are not deployed in the POC. This criterion is deferred to Phase 1 when vibration sensor supplier is confirmed. Camera-based perimeter detection replaces this criterion for the POC evaluation period.
POC-11
MANDATORY
Door sensor detection: shelter door sensor fires within 1 second of door being opened. Correctly suppresses alert during an active maintenance window and correctly generates a high-priority alert when door is opened outside a maintenance window. Minimum 10 test events per condition.
POC-12
MANDATORY
Full incident lifecycle simulation: a complete simulated attack (camera detection → power cut → cable cut → perimeter breach confirmed by gate magnetic contact → shelter breach confirmed by dual-tech detector) is executed at each POC site. The resulting evidence package must contain timestamped events from all available detection layers, be SHA-256 signed by the Jetson, and be available in the cloud vault within 5 minutes of incident close.


APPENDIX A — OPEN SOURCE AND COMMERCIAL TOOLS REFERENCE
Tool
Purpose
Link
Documentation
Licence
GStreamer
Video pipeline framework — 3 parallel RTSP streams with NVIDIA hardware acceleration, event-driven activation on ISAPI events
https://gstreamer.freedesktop.org/
https://gstreamer.freedesktop.org/documentation/
LGPL
NVIDIA GStreamer Plugins (Jetson)
Hardware-accelerated decode (nvv4l2decoder), format conversion (nvvideoconvert), TensorRT inference (nvinfer)
https://developer.nvidia.com/embedded/jetpack
https://docs.nvidia.com/jetson/archives/r35.1/DeveloperGuide/text/SD/Multimedia/AcceleratedGstreamer.html
NVIDIA proprietary (free)
NVIDIA TAO Toolkit
Model fine-tuning and training for Nigerian conditions
https://developer.nvidia.com/tao-toolkit
https://docs.nvidia.com/tao/tao-toolkit/
NVIDIA proprietary (free)
NVIDIA TensorRT
Model optimisation for edge inference on Jetson
https://developer.nvidia.com/tensorrt
https://docs.nvidia.com/deeplearning/tensorrt/
NVIDIA proprietary (free)
YOLO11 (Ultralytics)
Human and vehicle detection — YOLO11n with TensorRT INT8, verification layer on ISAPI event crops, active processing of all camera RTSP streams during confirmed incidents
https://github.com/ultralytics/ultralytics
https://docs.ultralytics.com/
AGPL-3.0 (commercial licence available)
InsightFace
Face detection (SCRFD) and recognition (ArcFace)
https://github.com/deepinsight/insightface
https://insightface.ai
MIT
InsightFace InspireFace SDK
C/C++ face recognition for edge deployment
https://github.com/HyperInspire/InspireFace
https://inspireface.readthedocs.io/
Apache 2.0
ByteTrack
Multi-object tracking with occlusion handling
https://github.com/ifzhang/ByteTrack
https://arxiv.org/abs/2110.06864
MIT
BoxMOT (StrongSORT)
Multi-object tracking with ReID — fallback option
https://github.com/mikel-brostrom/boxmot
https://github.com/mikel-brostrom/boxmot#readme
AGPL-3.0
Plate Recognizer
License plate recognition on-premise
https://platerecognizer.com/stream/
https://docs.platerecognizer.com/
Commercial
MMAction2
Action recognition for behavioural analysis
https://github.com/open-mmlab/mmaction2
https://mmaction2.readthedocs.io/
Apache 2.0
Jetson Sync Agent
Backend team deliverable. Offline queue and cloud sync process running on Jetson. Receives data from AI pipeline via local intake, queues when offline, drains in priority order when online.
Internal — backend team build
Section 4.1 and Section 2.4
Proprietary (Seismic)
ChirpStack
LoRaWAN Network Server — available as future provision if LoRaWAN sensors are added. Not required for POC.
https://www.chirpstack.io/
https://www.chirpstack.io/docs/
MIT
Eclipse Mosquitto
MQTT broker — available for inter-service messaging on Jetson where needed
https://mosquitto.org/
https://mosquitto.org/documentation/
EPL-2.0
FastAPI
Backend API framework
https://github.com/tiangolo/fastapi
https://fastapi.tiangolo.com/
MIT
Apache Kafka
Message queue for cloud event ingestion
https://kafka.apache.org/
https://kafka.apache.org/documentation/
Apache 2.0


APPENDIX B — KNOWN GAPS AND OPEN ITEMS
The following items must be resolved before Phase 1 deployment. They are not blockers for the POC.
Jamming mitigation for Citadel sites: A satellite IoT beacon (Globalstar SmartOne Solar or equivalent) as a tertiary alert path on a frequency independent of 4G. Must be evaluated, specced, and added to the Citadel hardware specification before Phase 1. The beacon transmits a signed alert with GPS and timestamp only, not video.
Jetson enclosure fabrication: REQ-JETSON-06 requires a custom tamper-evident locked metal enclosure. Hardware team must produce the fabrication specification before POC site installation, including ventilation calculations for 45°C ambient and cable entry seal design.
LoRaWAN protocol selection. RESOLVED. Sensor network protocol confirmed as Hikvision AX PRO 868MHz. Hub confirmed for POC.USB variant Sensor communication protocol confirmed as Hikvision AX PRO 868MHz frequency hopping spread spectrum. All POC sensors are AX PRO ecosystem. Hub confirmed as DS-PWA64-Kit-WB for POC. The inter-site LoRa mesh relay for site-to-site heartbeat and alarm relay is a separate LoRa function handled by a dedicated LoRa module on the Jetson and is not related to sensor communication.
Legal evidence validation: Evidence package format and SHA-256 signing protocol must be reviewed by a Nigerian criminal law practitioner before Phase 1 national rollout. This is a Phase 1 pre-deployment activity, not a POC blocker.
Towerco vs MNO access configuration: Where a site is co-managed between a towerco and one or more MNOs, access rights must be established contractually before the dashboard tier system is built. Backend team must build the tier system to support flexible per-site assignment rather than hardcoded relationships.
NSCDC onboarding programme: Tier 2 dashboard access requires formal engagement with NSCDC at state and national level to enrol command users and define coverage zones per formation. Programme management activity, but the backend team must ensure Tier 2 access configuration is ready before NCC engagement progresses to the point where NSCDC involvement is proposed.
Backend AI microservice for face enrolment: Section 4.5 specifies that the backend runs InsightFace (SCRFD + ArcFace) as a cloud microservice for enrolment embedding generation. The backend team must spec, build, and test this service. The AI team provides the InsightFace configuration (model selection, embedding dimensions, quality thresholds) from their Section 5 work. This is a joint deliverable. AI team specifies the model parameters, backend team runs the service.
API contract sign-off: Section 4.7 must be formally reviewed and agreed in writing by the backend team lead before any Sync Agent implementation begins.
Jetson Sync Agent local intake schema: AI team proposes the schema (endpoint, payload, timestamp, priority level, incident ID). Backend team approves it. Both leads sign before either team starts Phase 1 implementation. This is a one-page document.
GStreamer pipeline performance validation: The AI team must benchmark the 3-stream event-driven GStreamer pipeline on the Jetson Orin Nano Super and confirm it meets the 15 FPS minimum per stream at full load (all 3 streams active simultaneously during a confirmed incident). If performance falls below target, the team must document the bottleneck and proposed fix before POC deployment.
Dahua ISAPI event type, face vs human bounding box. RESOLVED. Confirmed from real-world Dahua IVS event payload analysis. The ISAPI delivers both fields in every detection event: BoundingBox (full person region, always populated) and FaceRect (face-specific coordinates, populated when the camera AI has located a face, all zeros otherwise). The Camera 1 pipeline uses a dual-path architecture: FaceRect non-zero goes directly to ArcFace skipping SCRFD; FaceRect all zeros triggers SCRFD fallback on the BoundingBox region. Architecture is confirmed and implemented in Section 5.1 and REQ-AI-07.
All four POC cameras confirmed as Dahua with full ISAPI AI event feed: All four POC cameras are Dahua. SDK and ISAPI integration is being obtained simultaneously for all four. All four deliver full ISAPI AI event feeds to the Jetson including BoundingBox and FaceRect per detection event. The camera-agnostic pipeline architecture handles any number of camera streams through configuration only. No camera model name or stream count is hardcoded. This item is closed.
Dahua Camera 1 onboard face recognition — potential architecture change. CLOSED — architecture unchanged. Onboard face recognition with a personnel database is not confirmed in the Dahua SDT4E425 datasheet. More critically, WizSense cameras industry-wide impose database limits that make onboard recognition unviable at ITIPS scale regardless of what any individual Dahua conversation might confirm. A site with a standard towerco workforce would exceed the onboard database capacity. The architecture is unchanged: the Jetson runs InsightFace for all face recognition. The camera delivers face detection crops via ISAPI (FaceRect field — see item 12, resolved). ArcFace runs on the Jetson against the local personnel cache. This is the confirmed and final architecture. No further investigation of Dahua onboard recognition is required.
Vibration sensor supplier — STATUS: UNRESOLVED (HIGH PRIORITY BLOCKER). Dragino LHT65N-VIB was previously marked confirmed but has been superseded. Preferred spec remains Tremor Tech TTech-17W (fence) and TTech-WTTS (tower) but company is not responding to procurement enquiries. Aravali Fence Liminal-K (Dubai) is under evaluation — email sent, no response. Senstar LM100 is under evaluation as a combined fence vibration detection and strobe deterrence unit. Hardware team must resolve before POC site installation.
Single camera main product — CONFIRMED DECISION. Main product deploys one camera per site. Two-camera architecture is POC-only. Main product Camera 1 has built-in 5-hour battery backup, seamless PoE to battery switchover, and built-in 4G LTE SIM covering the survivability role previously assigned to Camera 2. Single camera reduces hardware cost per site and reduces visible high-value hardware that attracts thieves. All main product requirements referencing Camera 2 are superseded by this decision.
Two mobile apps added to product scope. (1) RAPID Mobile App for NSCDC and NPF field officers: receives automatic dispatch with GPS coordinates, live camera feed, and signed evidence package; officers log arrival, record outcome, and close incidents from the app. (2) Field Operators App for telecom operator field engineers: staff registration into site whitelist database, asset serial number enrollment for the asset registry, maintenance scheduling, and equipment checklists. Both apps are confirmed scope. Development not yet started. Backend and frontend teams must include in roadmap.
ONSA dashboard elevated — CONFIRMED. ONSA now receives full real-time dashboard access in addition to the monthly PDF report. Backend team must add ONSA as a dashboard access tier. ONSA dashboard view: full national site map with live status, national threat pattern intelligence, RAPID agency compliance by state and agency, case tracking from incident to court, and operator coverage map. Read-only. No operational controls.
Horn speaker confirmed for POC. Kougar KODS-QAZ1325G1T 25W Network Horn Speaker confirmed from Kougar Solutions Abuja. Triggered by Jetson via local network HTTP command — no Hikvision management software required. Plays pre-recorded voice announcements in Hausa, Yoruba, Igbo, English plus siren tone. 120dB output. Replaces AX PRO wireless siren as primary deterrence audio device for POC. Cameras retain built-in siren and strobe as secondary backup. Main product will use a dedicated manufactured siren, strobe, and speaker unit as a separate device.
POC sensor stack updated. Confirmed POC sensor order placed with Kougar Solutions Abuja: DS-PWA64-Kit-WB hub (x2), DS-PDTT15AM-LM-WB triple signal detector (x2), DS-PDCM15PF-IR camera module (x2), AX PRO outdoor IP66 magnetic contact for gate (x2), AX PRO magnetic shock detector for battery cabinet (x2), DS-PDD12P-EG2-WE dual-tech detector for shelter (x2), DS-PDPC12P-EG2-WB PIRcam for generator area (x2), DS-PDHT-E-WB temperature detector (x2), KODS-QAZ1325G1T 25W horn speaker (x2). Standalone siren removed — cameras and horn speaker cover deterrence.
Fuel level sensor for POC — OPEN. Dragino LDDS75 ultrasonic non-contact fuel level sensor identified for diesel tank monitoring. Mounts on top of tank, never touches fuel, no moving parts, LoRaWAN connectivity. Not yet confirmed for POC procurement. Hardware team to confirm.

Document version: 2.5 | May 2026
Classification: STRICTLY CONFIDENTIAL. SEISMIC DIGITAL & INNOVATIONS LIMITED
Distribution: ITIPS Programme Team only
Document owner: ITIPS Programme Director

