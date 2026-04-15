# HAN Energy Dashboard

A lightweight HAN/AMS smart meter monitoring project built with Arduino and Python.

The Arduino acts as a simple serial bridge that forwards raw meter frames to a PC. The Python application handles parsing, CSV logging, live visualization, hourly load graphs, spot-price integration, load-step detection, phase analysis, and anomaly indicators.

This project was made as a practical learning and demonstration tool for understanding smart meter data, phase loading, consumption patterns, and how HAN data can be used for analysis and troubleshooting.

## Features

- Live import and export monitoring
- Phase overview for L1, L2, and L3
- Hourly load tracking and projected hourly average
- Capacity charge estimation
- Detection of large load changes and likely phase contribution
- CSV logging for later analysis
- Daily load graph
- Spot price support for Norwegian price areas
- System-status and anomaly indicators

## System overview

The project is split into two parts:

**Arduino**
- Receives HAN data through a serial adapter
- Reconstructs complete frames
- Sends machine-readable frame data to the PC
- Keeps the microcontroller side lightweight

**Python dashboard**
- Auto-detects the serial port
- Parses incoming meter frames
- Logs data to CSV
- Displays a full-screen dashboard
- Adds analysis and interpretation on top of the raw meter data

## Screenshots

Add dashboard screenshots here.

![Dashboard overview](images/dashboard_overview.png)
![Live tab](images/live_tab.png)
![Analysis tab](images/analysis_tab.png)

## Hardware setup

Example setup:
- Smart meter HAN port
- M-Bus to TTL adapter
- Arduino Uno/Nano
- PC running the Python dashboard

You can add wiring photos or diagrams here.

![Hardware setup](images/hardware_setup.jpg)

## How it works

The Arduino does not perform advanced calculations. It only forwards complete frames from the HAN interface to the PC.

The Python application performs:
- data parsing
- logging
- live calculations
- phase and load analysis
- visualization
- price integration

This makes the embedded side simple and reliable, while keeping the analysis flexible and easy to expand on the PC.

## Example use cases

- Monitor real-time household consumption
- Detect large loads that may affect capacity charges
- See which phase a load increase most likely appeared on
- Review daily load development
- Log data for troubleshooting and comparison over time

## Important note

This project is a personal learning and demonstration project. It is not a certified measuring instrument and should not be used as the sole basis for electrical safety decisions or formal metering purposes.

## Future improvements

Potential next steps:
- Better event classification
- Improved anomaly detection
- Historical trend views
- Export/import summaries over longer periods
- Additional hardware compatibility

## Author

Your Name

## License

Add your preferred license here.
