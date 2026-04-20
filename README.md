# HAN Energy Dashboard

A practical HAN/AMS smart meter monitoring and analysis project built with Arduino and Python.

Built with low-cost, widely available hardware, this project shows how affordable HAN/AMS data capture and analysis can help users better understand consumption patterns, identify costly load peaks, and improve everyday power usage.

This repository contains the PC-side application and project material used to support a lightweight Arduino-based HAN bridge.
The Arduino is intentionally kept simple and forwards raw smart meter frames to the PC, while the Python application performs parsing, CSV logging, visualization, event detection, phase analysis, anomaly indication, and price-aware consumption insight.

The project was developed as a hands-on exploration of smart metering, HAN communication, live data handling, and troubleshooting-oriented presentation of electrical consumption data.

A version 3 with wifi and MQTT smart house integration is in the works. It will also include a updated and more advanced python monitoring software for pc.

## Verified hardware setup

The hardware setup used in this project has been verified in practice.

---

### Screenshot of Live dashboard
![Live dashboard](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/Live_Dashboard.png)

### Screenshot of Analysis tab
![Analysis tab](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/Analysis_Dashboard.png)

---

## Professional relevance

This project is directly relevant to technical metering and utility-oriented troubleshooting work. It demonstrates practical interest in:

- HAN/AMS communication
- meter-data acquisition and validation
- serial communication workflows
- phase-based load analysis
- detection of significant load changes
- dashboard-based interpretation of measurement data
- structured logging for later review and troubleshooting

It is intended as a practical demonstration project and a portfolio example of applied technical work around smart metering and data interpretation.

---

## Key features

- Live import and export monitoring
- Phase overview for **L1**, **L2**, and **L3**
- Current hour average and projected hourly average
- Tracking of the highest monthly hours
- Estimated capacity-charge exposure
- Detection of large load changes
- Likely phase contribution for major load steps
- CSV logging for historical analysis
- Daily hourly load graph
- Spot-price support for Norwegian price areas (**NO1–NO5**)
- System-status and anomaly indicators
- Full-screen dashboard for local monitoring

---

## System overview

The solution is divided into two practical parts:

### Arduino bridge
The Arduino side is intentionally lightweight and is used to:

- receive HAN frames through a serial interface adapter
- reconstruct complete HDLC-style frames
- forward the frames to the PC in a machine-readable format
- keep the embedded side simple and stable

### Python dashboard
The Python application is responsible for:

- auto-detecting the correct serial port
- validating incoming frame format
- parsing smart meter data
- logging measurements to CSV
- visualizing live and historical values
- performing event detection and analysis
- estimating capacity-related exposure
- enriching the dashboard with spot-price context

---

Example setup:

- Smart meter with HAN port enabled
- M-Bus to TTL interface based on the **TI TSS721** transceiver
- Arduino Uno or Nano
- USB connection from Arduino to PC
- Windows PC running Python

---

## Hardware reference images

### AMS Meter Nuri Kaifa
![AMS Meter](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134246.jpg)

### Cat termination adapter
![Cat cable adapter](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134317.jpg)

### Pin 1 and 2 connected, polarity does not matter
![Cat pin 1 and 2](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134356.jpg)

### M-Bus adapter wiring  
The pin marked **RXD** (yellow cable) on the M-Bus adapter is the signal line used as data input to the Arduino in this setup.
![MBUS adapter wiring](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134414.jpg)

### Arduino wiring  
Arduino wiring: data frames are received on **D2**.
![Arduino wiring](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134532.jpg)

### Full wiring overview
![Complete setup](https://github.com/thorelvin/AMS_HAN_Sniffer_PC/blob/main/HAN/20260415_134558.jpg)

---

## Wiring diagram summary

The hardware is connected as follows:

- **Smart meter HAN port**
  - Use the HAN / RJ45 output from the meter
  - In this setup, the M-Bus pair is taken from **pin 1** and **pin 2**
  - Polarity does **not** matter for this connection

- **HAN / M-Bus to TTL adapter**
  - Connect the two HAN wires from the smart meter to the M-Bus input on the adapter
  - The adapter used in this setup is based on the **TI TSS721** M-Bus transceiver

- **Adapter to Arduino**
  - **Adapter RXD** -> **Arduino D2**
  - **Adapter GND** -> **Arduino GND**

- **Arduino to PC**
  - Connect the Arduino to the PC with USB
  - The Arduino forwards complete HAN frames to the Python application over USB serial

---

## Repository structure

Example structure based on this repository:
```

```text
.
├── HAN
├── AMS_HAN_Avleser_PC.ino
├── han_dashboard.py
└── README.md
```

- `HAN/` – Arduino/HAN-related images
- `AMS_HAN_Avleser_PC.ino` – lightweight Arduino firmware
- `han_dashboard.py` – main Python dashboard application
- `README.md` – project documentation

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
- builtin led on Arduino flashes every time a frams is sent, as an activity indicator

### 2. Close Arduino Serial Monitor
Make sure the Arduino IDE Serial Monitor is closed before starting the Python dashboard, otherwise the COM port may already be in use.

### 3. Run the dashboard
From the repository folder, run:

```bash
python han_dashboard.py
```

If you want to force a specific serial port:

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

The dashboard is intended to present both operational and analytical information.

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
- meter and protocol context
- runtime and logging information

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

## Spot-price context

The dashboard can display Norwegian spot-price context by price area.

Supported areas:

- NO1
- NO2
- NO3
- NO4
- NO5

Price data is used to improve interpretation of live energy cost and day-to-day operating context.

---

## Related repositories

This repository can be viewed together with the related Arduino-focused project:

- [AMS_HAN_Sniffer](https://github.com/thorelvin/AMS_HAN_Sniffer)  
  Arduino-oriented repository related to the HAN bridge workflow.

- [AMS_HAN_Sniffer_PC](https://github.com/thorelvin/AMS_HAN_Sniffer_PC)  
  Python desktop application repository for live monitoring, CSV logging, visualization, event detection, phase analysis, and price-aware consumption insight.

- [AMS_HAN_Gateway](https://github.com/thorelvin/AMS-HAN-Gateway)
  Reads AMS HAN meter data with ESP32 and provides live monitoring, diagnostics, replay tools, and Norwegian power cost analysis. 

Together, the two repositories describe a complete HAN/AMS monitoring concept with a lightweight embedded bridge and a more advanced PC-based analysis interface.

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

## Professional relevance

This project demonstrates practical work with HAN/AMS meter data, serial communication, live monitoring, phase analysis, and troubleshooting-oriented visualization. It is directly relevant to utility metering environments where reliable data handling, communication quality, and structured analysis of consumption behavior are important.

---

## Example use cases

- Real-time monitoring of household power import and export
- Detection of load increases that may affect monthly capacity charges
- Phase-oriented analysis of major load events
- CSV-based logging for documentation and later troubleshooting
- Live cost context through Norwegian spot-price areas
- Identification of imbalance and other indicators that may justify further technical inspection

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

Possible next steps include:

- more advanced event classification
- improved anomaly detection
- longer-term historical trends
- better export and solar analytics
- richer graph views
- additional hardware compatibility
- persistent settings
- improved packaging for easier deployment

---

## Author

**Thor Elvin Valø**

---

## License

**MIT License**
