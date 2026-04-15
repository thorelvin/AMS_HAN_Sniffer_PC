# HAN Energy Dashboard

A lightweight HAN/AMS smart meter monitoring project built with Arduino and Python.

This repository contains the PC-side dashboard application together with the project material needed for the Arduino bridge workflow. The Arduino acts as a simple serial bridge that forwards raw smart meter frames to the PC, while the Python application handles parsing, CSV logging, live visualization, hourly load graphs, load-event detection, phase analysis, anomaly indicators, and price-aware consumption insights.

This project was developed as a practical learning and demonstration tool for understanding smart meter data, phase loading, household consumption patterns, and how HAN data can be used for analysis and troubleshooting.

---

## Features

- Live import and export monitoring
- Phase overview for **L1**, **L2**, and **L3**
- Current hour average and projected hourly average
- Monthly top-hour tracking
- Capacity charge estimation
- Detection of large load changes
- Likely phase contribution for major load steps
- CSV logging for historical analysis
- Daily hourly load graph
- Spot price support for Norwegian price areas (**NO1–NO5**)
- System status and anomaly indicators
- Full-screen dashboard for local monitoring

---

## Why this project

This project demonstrates how lightweight embedded data acquisition can be combined with PC-based analysis and visualization to build a practical HAN/AMS monitoring tool.

It was created to explore:

- smart meter communication over HAN
- serial frame forwarding and parsing
- live energy monitoring
- phase-based load analysis
- capacity-charge awareness
- practical troubleshooting and interpretation of meter data

---

## System overview

The solution is split into two parts:

### Arduino bridge
The Arduino side is intentionally lightweight and is used to:

- receive HAN frames through a serial adapter
- reconstruct complete HDLC-style frames
- forward them to the PC in a machine-readable format
- keep the embedded side simple and reliable

### Python dashboard
The Python application is responsible for:

- auto-detecting the correct serial port
- parsing incoming smart meter frames
- logging measurements to CSV
- visualizing live and historical information
- performing event detection and analysis
- estimating capacity-charge exposure
- enriching the dashboard with spot-price context

---

## Repository structure

Example structure based on this repository:

```text
.
├── HAN/
├── han_dashboard.py
└── README.md
```

- `HAN/` – project-related Arduino/HAN material for the serial bridge workflow
- `han_dashboard.py` – main Python dashboard application
- `README.md` – project documentation

---

### Live dashboard
![Live dashboard](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/Live_Dashboard.png)

### Analysis tab
![Analysis tab](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/Analysis_Dashboard.png)

### Hardware setup
![AMS Meter](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134246.jpg)
![Cat cable adapter](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134317.jpg)
![Cat pin 1 and 2](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134356.jpg)
![MBUS adapter wiring](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134414.jpg)
![Arduino wiring](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134532.jpg)
![Complete setup](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134558.jpg)

---

## Hardware requirements

Example setup:

- Smart meter with HAN port enabled
- M-Bus / HAN to TTL adapter
- Arduino Uno or Nano
- USB connection from Arduino to PC
- Windows PC running Python

---

## Software requirements

- Python 3.x
- `pyserial`

Install dependency:

```bash
pip install pyserial
```

---

## Quick start

### 1. Prepare the Arduino side
Use the Arduino bridge material included in this repository and upload the lightweight bridge sketch to the Arduino.

The Arduino bridge should:

- read HAN frames from the smart meter interface
- forward them to the PC over USB serial
- keep the microcontroller side free of heavy analysis logic

### 2. Close Arduino Serial Monitor
Make sure the Arduino IDE Serial Monitor is closed before starting the Python dashboard, otherwise the COM port may already be in use.

### 3. Run the dashboard
From the repository folder, run:

```bash
python han_dashboard.py
```

Or, if you want to force a specific serial port:

```bash
python han_dashboard.py --port COM7
```

### 4. What happens next
The dashboard will:

- scan available COM ports automatically
- connect to the first port that provides valid HAN frame data
- start logging parsed data to CSV
- launch the full-screen dashboard interface

---

## Usage

- `Esc` exits full-screen mode
- `F11` toggles full-screen mode
- Use the price area selector to choose **NO1**, **NO2**, **NO3**, **NO4**, or **NO5**
- CSV logs are stored automatically for later analysis

---

## Serial frame format

The Arduino bridge sends data to the PC in the following format:

```text
FRAME,<sequence>,<length>,<HEX_PAYLOAD>
```

Example:

```text
FRAME,42,121,A07901020110...
```

The Python application validates this format before accepting the serial source.

---

## Dashboard contents

The dashboard is designed to present both operational and analytical information.

### Live / customer view
- current import and export
- phase overview
- current hour average
- projected hourly average
- estimated capacity step
- cost view
- daily load graph

### Analysis / system view
- top monthly hours
- detected load events
- system-status and anomaly indicators
- meter / protocol context
- logging and runtime information

---

## Data logging

The Python application logs parsed values to CSV for later review.

Typical logged values include:

- timestamp
- import/export power
- reactive power
- phase currents
- phase voltages
- projected hourly average
- top-hour context
- anomaly-related indicators

This makes it possible to review consumption patterns and events outside the live dashboard.

---

## Notes on spot prices

The dashboard can display Norwegian spot-price context by price area.

Supported areas:

- NO1
- NO2
- NO3
- NO4
- NO5

Price data may be used to improve interpretation of real-time import cost and daily energy-cost exposure.

---

## Related repositories

This repository can also be viewed together with the dedicated Arduino-side repository:

- [AMS_HAN_Sniffer](https://github.com/thorelvin/AMS_HAN_Sniffer)  
  Arduino-focused repository related to the HAN bridge workflow.

- [AMS_HAN_Sniffer_PC](https://github.com/thorelvin/AMS_HAN_Sniffer_PC)  
  Python desktop application repository for live monitoring, CSV logging, visualization, event detection, phase analysis, and price-aware consumption insights.

If you want to keep the embedded and PC-side development fully separated, these repositories can be maintained independently while still documenting the same overall project concept.

---

## Important note

This project is a **personal learning and demonstration project**.

It is **not** a certified measuring instrument and should **not** be used as the sole basis for:

- electrical safety decisions
- official metering purposes
- billing verification
- formal fault diagnosis

Any suspected electrical faults, wiring issues, or safety concerns should be evaluated by a qualified electrician.

---

## Current status

This is an active practical project focused on:

- reliable HAN frame forwarding
- live visualization
- CSV logging
- phase and load analysis
- iterative dashboard improvement

---

## Future improvements

Planned or possible next steps:

- more advanced event classification
- improved anomaly detection
- longer-term historical trends
- better export / solar analytics
- richer graph views
- more hardware compatibility
- persistent settings
- improved packaging for easier deployment

---

## Author

**Thor Elvin Valø**

---

## License

**MIT License**
