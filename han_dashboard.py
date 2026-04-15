
#!/usr/bin/env python3
"""
HAN dashboard with two tabs:
  1. Live / customer view
  2. Analysis / anomaly view

Expected Arduino serial format:
    FRAME,<sequence>,<length>,<HEX_PAYLOAD>

This script:
- auto-scans COM ports and connects to the first one that emits valid FRAME lines
- keeps running if no port is found
- reconnects automatically if the serial device disappears
- parses observed Kaifa KFM_001 payloads
- logs parsed values to CSV
- fetches Norwegian spot prices for a selected NO1-NO5 area
- shows a daily hourly load graph
- estimates capacity charge, detects load steps, and highlights likely phase

Dependencies:
    pip install pyserial

Notes on price source:
- The GUI uses the open hvakosterstrommen.no API because it is simple and free.
- This is not an official Nord Pool API.
- The GUI labels the source clearly so the user can see where prices come from.
"""

import argparse
import csv
import json
import os
import queue
import threading
import time
import traceback
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import serial
from serial.tools import list_ports
import tkinter as tk
from tkinter import ttk


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_BAUD = 115200
PROBE_SECONDS = 4.0
RETRY_DELAY_SECONDS = 3.0
STEP_THRESHOLD_W = 3000
PRICE_REFRESH_SECONDS = 30 * 60  # 30 minutes

# Fixed grid energy charge from the user's Tensio table
GRID_DAY_RATE_NOK_PER_KWH = 0.4254
GRID_NIGHT_RATE_NOK_PER_KWH = 0.2642

# Manual fallback spot price if no web price is available
FALLBACK_SPOT_PRICE_NOK_PER_KWH = 1.00

# Capacity steps supplied by the user
CAPACITY_STEPS = [
    (2.0, 134, "0-2 kW"),
    (5.0, 270, "2-5 kW"),
    (10.0, 488, "5-10 kW"),
    (15.0, 739, "10-15 kW"),
    (20.0, 991, "15-20 kW"),
    (25.0, 1243, "20-25 kW"),
    (50.0, 2166, "25-50 kW"),
    (75.0, 3427, "50-75 kW"),
    (100.0, 4687, "75-100 kW"),
    (150.0, 6784, "100-150 kW"),
    (200.0, 9305, "150-200 kW"),
    (300.0, 13500, "200-300 kW"),
    (400.0, 18540, "300-400 kW"),
    (500.0, 23580, "400-500 kW"),
    (999999.0, 28615, "Over 500 kW"),
]

PRICE_AREAS = ["NO1", "NO2", "NO3", "NO4", "NO5"]


# =============================================================================
# General helpers
# =============================================================================

def debug(msg: str) -> None:
    print(f"[DEBUG] {time.strftime('%H:%M:%S')} - {msg}", flush=True)


def abs32(v: int) -> int:
    return -v if v < 0 else v


def days_from_civil(y: int, m: int, d: int) -> int:
    y -= 1 if m <= 2 else 0
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def frame_to_epoch_seconds(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    days = days_from_civil(year, month, day)
    return days * 86400 + hour * 3600 + minute * 60 + second


def week_start_days(year: int, month: int, day: int) -> int:
    days = days_from_civil(year, month, day)
    weekday = (days + 3) % 7  # Monday = 0
    return days - weekday


def is_night_rate(hour: int) -> bool:
    return hour >= 22 or hour < 6


def current_grid_rate(hour: int) -> float:
    return GRID_NIGHT_RATE_NOK_PER_KWH if is_night_rate(hour) else GRID_DAY_RATE_NOK_PER_KWH


def current_grid_rate_label(hour: int) -> str:
    return "Natt (22-06)" if is_night_rate(hour) else "Dag (06-22)"


def phase_label(phase: int) -> str:
    return {1: "L1", 2: "L2", 3: "L3"}.get(phase, "UNKNOWN")


def trend_label(delta_w: int) -> str:
    if delta_w >= 3000:
        return "Kraftig opp"
    if delta_w >= 500:
        return "Opp"
    if delta_w <= -3000:
        return "Kraftig ned"
    if delta_w <= -500:
        return "Ned"
    return "Stabil"


def grid_state_label(p_import_w: int, p_export_w: int) -> str:
    if p_export_w > 50:
        return "Eksporterer"
    if p_import_w > 50:
        return "Importerer"
    return "Nær balanse"


def imbalance_percent(i1_ma: int, i2_ma: int, i3_ma: int) -> float:
    i1 = i1_ma / 1000.0
    i2 = i2_ma / 1000.0
    i3 = i3_ma / 1000.0
    avg = (i1 + i2 + i3) / 3.0
    if avg <= 0.001:
        return 0.0
    return max(abs(i1 - avg), abs(i2 - avg), abs(i3 - avg)) / avg * 100.0


def find_capacity_step_index(kw: float) -> int:
    for idx, (upper, _, _) in enumerate(CAPACITY_STEPS):
        if kw <= upper:
            return idx
    return len(CAPACITY_STEPS) - 1


# =============================================================================
# Serial auto-detect
# =============================================================================

def is_valid_frame_line(line: str) -> bool:
    if not line.startswith("FRAME,"):
        return False

    parts = line.strip().split(",", 3)
    if len(parts) != 4:
        return False

    _, seq_str, length_str, hex_payload = parts

    if not seq_str.isdigit() or not length_str.isdigit():
        return False

    if len(hex_payload) == 0 or len(hex_payload) % 2 != 0:
        return False

    try:
        raw = bytes.fromhex(hex_payload)
    except ValueError:
        return False

    expected_len = int(length_str)
    return len(raw) == expected_len


def parse_frame_line(line: str) -> bytes:
    parts = line.strip().split(",", 3)
    return bytes.fromhex(parts[3])


def probe_port(port_name: str, baudrate: int, probe_seconds: float) -> Optional[serial.Serial]:
    debug(f"Trying port {port_name} at {baudrate} baud")

    try:
        ser = serial.Serial(port_name, baudrate=baudrate, timeout=0.5)
    except Exception as e:
        debug(f"Could not open {port_name}: {e}")
        return None

    try:
        time.sleep(1.2)  # allow reset on boards that reset when the COM port opens
        ser.reset_input_buffer()

        deadline = time.time() + probe_seconds
        seen = 0

        while time.time() < deadline:
            raw = ser.readline()
            if not raw:
                continue

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            seen += 1

            if is_valid_frame_line(line):
                debug(f"Valid FRAME data detected on {port_name}")
                return ser

            if seen <= 5:
                debug(f"{port_name} non-matching line: {line[:100]}")

        debug(f"No valid FRAME data on {port_name} within {probe_seconds:.1f}s")
        ser.close()
        return None

    except Exception as e:
        debug(f"Error while probing {port_name}: {e}")
        try:
            ser.close()
        except Exception:
            pass
        return None


def auto_connect_serial(
    baudrate: int,
    preferred_port: Optional[str] = None,
    status_callback=None,
) -> serial.Serial:
    while True:
        ports = list(list_ports.comports())

        if preferred_port:
            if status_callback:
                status_callback(f"Trying preferred port {preferred_port}...")
            ser = probe_port(preferred_port, baudrate, PROBE_SECONDS)
            if ser is not None:
                return ser
            debug(f"Preferred port {preferred_port} did not provide valid FRAME data.")
            if status_callback:
                status_callback(f"No valid HAN data on {preferred_port}. Returning to auto-scan.")
            preferred_port = None

        if not ports:
            debug("No COM ports found. Retrying...")
            if status_callback:
                status_callback("No COM ports found. Connect Arduino bridge and wait...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        debug("Available serial ports:")
        if status_callback:
            status_callback(f"Found {len(ports)} serial port(s). Probing for HAN data...")
        for p in ports:
            debug(f"  {p.device} - {p.description}")

        for p in ports:
            if status_callback:
                status_callback(f"Trying {p.device} ({p.description})...")
            ser = probe_port(p.device, baudrate, PROBE_SECONDS)
            if ser is not None:
                return ser

        debug("No usable HAN serial source found. Retrying...")
        if status_callback:
            status_callback("Ports found, but no valid HAN frames yet. Retrying...")
        time.sleep(RETRY_DELAY_SECONDS)


# =============================================================================
# Kaifa frame decode
# =============================================================================

@dataclass
class MeterFrame:
    timestamp: str
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    meter_id: str
    meter_type: str
    p_import_w: int
    p_export_w: int
    q_import_var: int
    q_export_var: int
    i1_ma: int
    i2_ma: int
    i3_ma: int
    u1_dv: int
    u2_dv: int
    u3_dv: int


def read_be32(data: bytes, pos: int) -> int:
    return int.from_bytes(data[pos:pos + 4], "big", signed=False)


def find_pattern(data: bytes, pattern: bytes) -> int:
    return data.find(pattern)


def parse_kaifa_kfm001(frame_bytes: bytes) -> Optional[MeterFrame]:
    list_pattern = bytes([0x09, 0x07]) + b"KFM_001"
    list_pos = find_pattern(frame_bytes, list_pattern)
    if list_pos < 0:
        return None

    clock_pos = -1
    for i in range(0, max(0, list_pos - 1)):
        if i + 1 < len(frame_bytes) and frame_bytes[i] == 0x09 and frame_bytes[i + 1] == 0x0C:
            clock_pos = i
            break

    if clock_pos < 0 or clock_pos + 14 > len(frame_bytes):
        return None

    t = frame_bytes[clock_pos + 2: clock_pos + 14]
    year = (t[0] << 8) | t[1]
    month = t[2]
    day = t[3]
    hour = t[5]
    minute = t[6]
    second = t[7]

    p = list_pos + len(list_pattern)

    if p + 18 > len(frame_bytes) or frame_bytes[p] != 0x09 or frame_bytes[p + 1] != 0x10:
        return None
    meter_id = frame_bytes[p + 2:p + 18].decode("ascii", errors="replace")
    p += 18

    if p + 10 > len(frame_bytes) or frame_bytes[p] != 0x09 or frame_bytes[p + 1] != 0x08:
        return None
    meter_type = frame_bytes[p + 2:p + 10].decode("ascii", errors="replace")
    p += 10

    vals: List[int] = []
    for _ in range(10):
        if p + 5 > len(frame_bytes) or frame_bytes[p] != 0x06:
            return None
        vals.append(read_be32(frame_bytes, p + 1))
        p += 5

    ts = f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"

    return MeterFrame(
        timestamp=ts,
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
        meter_id=meter_id,
        meter_type=meter_type,
        p_import_w=vals[0],
        p_export_w=vals[1],
        q_import_var=vals[2],
        q_export_var=vals[3],
        i1_ma=vals[4],
        i2_ma=vals[5],
        i3_ma=vals[6],
        u1_dv=vals[7],
        u2_dv=vals[8],
        u3_dv=vals[9],
    )


# =============================================================================
# CSV logger
# =============================================================================

class CsvLogger:
    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.current_path = None

    def _path_for_frame(self, frame: MeterFrame) -> str:
        return os.path.join(self.log_dir, f"han_{frame.year:04d}-{frame.month:02d}-{frame.day:02d}.csv")

    def log(self, row: dict, frame: MeterFrame) -> None:
        path = self._path_for_frame(frame)
        file_exists = os.path.exists(path)

        with open(path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

        self.current_path = path


# =============================================================================
# Price fetching
# =============================================================================

class PriceService:
    """
    Fetches Norwegian spot prices from hvakosterstrommen.no.

    We keep this separate from the meter model because price lookup is not part
    of the live HAN data stream.
    """

    def _fetch_day(self, when: datetime, area: str) -> List[dict]:
        url = f"https://www.hvakosterstrommen.no/api/v1/prices/{when.year:04d}/{when.month:02d}-{when.day:02d}_{area}.json"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "HAN-Dashboard/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch(self, area: str) -> Dict[str, Any]:
        # Use timezone-aware local time because the API returns offset-aware timestamps.
        now = datetime.now().astimezone()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        today_rows = self._fetch_day(datetime.combine(today, datetime.min.time()).astimezone(), area)
        all_rows = list(today_rows)

        # Tomorrow may not be available before publication. That is normal.
        tomorrow_rows = []
        try:
            tomorrow_rows = self._fetch_day(datetime.combine(tomorrow, datetime.min.time()).astimezone(), area)
            all_rows.extend(tomorrow_rows)
        except Exception:
            tomorrow_rows = []

        parsed = []
        for row in all_rows:
            try:
                start = datetime.fromisoformat(row["time_start"])
                end = datetime.fromisoformat(row["time_end"])
                parsed.append({
                    "start": start,
                    "end": end,
                    "nok_per_kwh": float(row["NOK_per_kWh"]),
                    "eur_per_kwh": float(row.get("EUR_per_kWh", 0.0)),
                })
            except Exception:
                continue

        current_price = None
        next_price = None
        for idx, item in enumerate(parsed):
            if item["start"] <= now < item["end"]:
                current_price = item["nok_per_kwh"]
                if idx + 1 < len(parsed):
                    next_price = parsed[idx + 1]["nok_per_kwh"]
                break

        today_only = [item for item in parsed if item["start"].date() == today]
        avg_today = sum(item["nok_per_kwh"] for item in today_only) / len(today_only) if today_only else None
        cheapest = min(today_only, key=lambda x: x["nok_per_kwh"]) if today_only else None
        most_expensive = max(today_only, key=lambda x: x["nok_per_kwh"]) if today_only else None

        return {
            "ok": True,
            "area": area,
            "source_name": "Hva koster strømmen.no",
            "source_note": "Open API (not official Nord Pool API)",
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "current_price": current_price,
            "next_price": next_price,
            "avg_today": avg_today,
            "cheapest_label": (
                f"{cheapest['start'].strftime('%H:%M')}-{cheapest['end'].strftime('%H:%M')} · {cheapest['nok_per_kwh']:.3f} kr/kWh"
                if cheapest else "No data"
            ),
            "most_expensive_label": (
                f"{most_expensive['start'].strftime('%H:%M')}-{most_expensive['end'].strftime('%H:%M')} · {most_expensive['nok_per_kwh']:.3f} kr/kWh"
                if most_expensive else "No data"
            ),
            "today_prices": [
                {
                    "hour": item["start"].hour,
                    "price": item["nok_per_kwh"],
                }
                for item in today_only
            ],
        }


# =============================================================================
# Model
# =============================================================================

@dataclass
class StepEvent:
    timestamp: str
    delta_w: int
    before_w: int
    after_w: int
    dominant_phase: int


@dataclass
class TopHourRecord:
    year: int
    month: int
    day: int
    hour: int
    avg_import_w: int
    peak_instant_w: int
    strongest_rise_w: int
    strongest_rise_phase: int
    strongest_rise_time: str


class DashboardModel:
    """
    Holds everything derived from the HAN data stream.

    Important design choice:
    - The Arduino stays lightweight and only forwards frames.
    - All calculations and interpretation happen here on the PC.
    """

    def __init__(self, csv_logger: CsvLogger):
        self.csv = csv_logger
        self.start_monotonic = time.monotonic()

        self.last_frame: Optional[MeterFrame] = None
        self.last_epoch: Optional[int] = None

        self.current_hour_key = None
        self.current_hour_sample_count = 0
        self.current_hour_sum_import_w = 0
        self.current_hour_sum_export_w = 0
        self.current_hour_peak_instant_w = 0
        self.current_hour_strongest_event: Optional[StepEvent] = None

        self.top_hours: List[TopHourRecord] = []

        self.daily_key = None
        self.weekly_key = None

        self.daily_import_wh = 0.0
        self.daily_export_wh = 0.0
        self.daily_total_cost_grid_only_nok = 0.0

        self.weekly_import_wh = 0.0
        self.weekly_export_wh = 0.0
        self.weekly_total_cost_grid_only_nok = 0.0

        self.last_event: Optional[StepEvent] = None

        # Tracks whether a voltage channel has ever looked like a real live voltage.
        # This helps distinguish "lost voltage" from "field usually not populated".
        self.observed_voltage_capable = [False, False, False]

        self.connected_port = "Not connected"
        self.frame_count = 0
        self.last_valid_timestamp = "-"

        # Daily graph data:
        # Keep import and export separately so the graph can show
        # import in blue and export in green.
        self.daily_hour_day = None
        self.daily_hour_import_sum = [0.0] * 24
        self.daily_hour_export_sum = [0.0] * 24
        self.daily_hour_count = [0] * 24

    def set_connected_port(self, port_name: str):
        self.connected_port = port_name

    def _reset_day(self, frame: MeterFrame):
        self.daily_key = (frame.year, frame.month, frame.day)
        self.daily_import_wh = 0.0
        self.daily_export_wh = 0.0
        self.daily_total_cost_grid_only_nok = 0.0

        self.daily_hour_day = self.daily_key
        self.daily_hour_import_sum = [0.0] * 24
        self.daily_hour_export_sum = [0.0] * 24
        self.daily_hour_count = [0] * 24

    def _reset_week(self, frame: MeterFrame):
        self.weekly_key = week_start_days(frame.year, frame.month, frame.day)
        self.weekly_import_wh = 0.0
        self.weekly_export_wh = 0.0
        self.weekly_total_cost_grid_only_nok = 0.0

    def _update_daily_hour_profile(self, frame: MeterFrame):
        if self.daily_hour_day != (frame.year, frame.month, frame.day):
            self.daily_hour_day = (frame.year, frame.month, frame.day)
            self.daily_hour_import_sum = [0.0] * 24
            self.daily_hour_export_sum = [0.0] * 24
            self.daily_hour_count = [0] * 24

        h = frame.hour
        if 0 <= h <= 23:
            self.daily_hour_import_sum[h] += frame.p_import_w
            self.daily_hour_export_sum[h] += frame.p_export_w
            self.daily_hour_count[h] += 1

    def _daily_hourly_profile(self) -> List[dict]:
        """
        Return one entry per hour with:
          - kw: average magnitude for the dominant direction that hour
          - mode: 'import', 'export', or 'idle'
        This makes the graph show import bars in blue and export bars in green.
        """
        out = []
        for hour in range(24):
            if self.daily_hour_count[hour] <= 0:
                out.append({"kw": None, "mode": "idle"})
                continue

            avg_import_kw = (self.daily_hour_import_sum[hour] / self.daily_hour_count[hour]) / 1000.0
            avg_export_kw = (self.daily_hour_export_sum[hour] / self.daily_hour_count[hour]) / 1000.0

            if avg_export_kw > avg_import_kw and avg_export_kw > 0.001:
                out.append({"kw": avg_export_kw, "mode": "export"})
            elif avg_import_kw > 0.001:
                out.append({"kw": avg_import_kw, "mode": "import"})
            else:
                out.append({"kw": 0.0, "mode": "idle"})

        return out

    def _integrate_energy(self, frame: MeterFrame):
        epoch = frame_to_epoch_seconds(frame.year, frame.month, frame.day, frame.hour, frame.minute, frame.second)

        if self.last_frame is None or self.last_epoch is None:
            self._reset_day(frame)
            self._reset_week(frame)
            self.last_epoch = epoch
            return

        current_day = (frame.year, frame.month, frame.day)
        if self.daily_key != current_day:
            self._reset_day(frame)

        current_week = week_start_days(frame.year, frame.month, frame.day)
        if self.weekly_key != current_week:
            self._reset_week(frame)

        dt = epoch - self.last_epoch
        if 0 < dt <= 120:
            avg_import_w = (self.last_frame.p_import_w + frame.p_import_w) * 0.5
            avg_export_w = (self.last_frame.p_export_w + frame.p_export_w) * 0.5

            wh_import = avg_import_w * (dt / 3600.0)
            wh_export = avg_export_w * (dt / 3600.0)

            # Here we keep only the grid energy charge in the model.
            # Spot price is added later in the UI from the selected market area.
            cost_grid_only = (wh_import / 1000.0) * current_grid_rate(frame.hour)

            self.weekly_import_wh += wh_import
            self.weekly_export_wh += wh_export
            self.weekly_total_cost_grid_only_nok += cost_grid_only

            if self.daily_key == current_day:
                self.daily_import_wh += wh_import
                self.daily_export_wh += wh_export
                self.daily_total_cost_grid_only_nok += cost_grid_only

        self.last_epoch = epoch

    def _estimate_dominant_phase(self, prev: MeterFrame, curr: MeterFrame) -> int:
        d1 = abs(curr.i1_ma - prev.i1_ma)
        d2 = abs(curr.i2_ma - prev.i2_ma)
        d3 = abs(curr.i3_ma - prev.i3_ma)

        if max(d1, d2, d3) < 100:
            return 0
        if d1 >= d2 and d1 >= d3:
            return 1
        if d2 >= d1 and d2 >= d3:
            return 2
        return 3

    def _detect_event(self, frame: MeterFrame) -> Optional[StepEvent]:
        if self.last_frame is None:
            return None

        delta_w = frame.p_import_w - self.last_frame.p_import_w
        if abs32(delta_w) < STEP_THRESHOLD_W:
            return None

        phase = self._estimate_dominant_phase(self.last_frame, frame)
        return StepEvent(
            timestamp=frame.timestamp,
            delta_w=delta_w,
            before_w=self.last_frame.p_import_w,
            after_w=frame.p_import_w,
            dominant_phase=phase,
        )

    def _finalize_current_hour(self):
        if self.current_hour_key is None or self.current_hour_sample_count == 0:
            return

        year, month, day, hour = self.current_hour_key
        avg_import_w = int(self.current_hour_sum_import_w / self.current_hour_sample_count)

        strongest_rise_w = 0
        strongest_rise_phase = 0
        strongest_rise_time = "-"
        if self.current_hour_strongest_event:
            strongest_rise_w = self.current_hour_strongest_event.delta_w
            strongest_rise_phase = self.current_hour_strongest_event.dominant_phase
            strongest_rise_time = self.current_hour_strongest_event.timestamp.split(" ")[1]

        record = TopHourRecord(
            year=year,
            month=month,
            day=day,
            hour=hour,
            avg_import_w=avg_import_w,
            peak_instant_w=self.current_hour_peak_instant_w,
            strongest_rise_w=strongest_rise_w,
            strongest_rise_phase=strongest_rise_phase,
            strongest_rise_time=strongest_rise_time,
        )

        replaced = False
        for i, rec in enumerate(self.top_hours):
            if (rec.year, rec.month, rec.day) == (record.year, record.month, record.day):
                if record.avg_import_w > rec.avg_import_w:
                    self.top_hours[i] = record
                replaced = True
                break

        if not replaced:
            self.top_hours.append(record)

        self.top_hours.sort(key=lambda r: r.avg_import_w, reverse=True)
        self.top_hours = self.top_hours[:3]

        self.current_hour_key = None
        self.current_hour_sample_count = 0
        self.current_hour_sum_import_w = 0
        self.current_hour_sum_export_w = 0
        self.current_hour_peak_instant_w = 0
        self.current_hour_strongest_event = None

    def _update_hour(self, frame: MeterFrame, event: Optional[StepEvent]):
        key = (frame.year, frame.month, frame.day, frame.hour)

        if self.current_hour_key is None:
            self.current_hour_key = key

        if self.current_hour_key != key:
            self._finalize_current_hour()
            self.current_hour_key = key

        self.current_hour_sample_count += 1
        self.current_hour_sum_import_w += frame.p_import_w
        self.current_hour_sum_export_w += frame.p_export_w
        self.current_hour_peak_instant_w = max(self.current_hour_peak_instant_w, frame.p_import_w)

        if event and event.delta_w > 0:
            if self.current_hour_strongest_event is None or event.delta_w > self.current_hour_strongest_event.delta_w:
                self.current_hour_strongest_event = event

    def _projected_hour_average_kw(self, frame: MeterFrame) -> float:
        if self.current_hour_sample_count == 0:
            return frame.p_import_w / 1000.0

        elapsed = frame.minute * 60 + frame.second
        elapsed = min(max(elapsed, 1), 3599)

        avg_so_far_kw = (self.current_hour_sum_import_w / self.current_hour_sample_count) / 1000.0
        current_kw = frame.p_import_w / 1000.0
        remaining = 3600 - elapsed

        return ((avg_so_far_kw * elapsed) + (current_kw * remaining)) / 3600.0

    def _estimate_capacity(self, frame: MeterFrame, projected_kw: float):
        tmp = list(self.top_hours)

        candidate = TopHourRecord(
            year=frame.year,
            month=frame.month,
            day=frame.day,
            hour=frame.hour,
            avg_import_w=int(projected_kw * 1000.0),
            peak_instant_w=self.current_hour_peak_instant_w,
            strongest_rise_w=self.current_hour_strongest_event.delta_w if self.current_hour_strongest_event else 0,
            strongest_rise_phase=self.current_hour_strongest_event.dominant_phase if self.current_hour_strongest_event else 0,
            strongest_rise_time=self.current_hour_strongest_event.timestamp.split(" ")[1] if self.current_hour_strongest_event else "-",
        )

        merged = False
        for i, rec in enumerate(tmp):
            if (rec.year, rec.month, rec.day) == (candidate.year, candidate.month, candidate.day):
                if candidate.avg_import_w > rec.avg_import_w:
                    tmp[i] = candidate
                merged = True
                break

        if not merged:
            tmp.append(candidate)

        tmp.sort(key=lambda r: r.avg_import_w, reverse=True)
        contributing = min(3, len(tmp))
        if contributing == 0:
            return None

        basis_w = int(sum(r.avg_import_w for r in tmp[:contributing]) / contributing)
        basis_kw = basis_w / 1000.0
        idx = find_capacity_step_index(basis_kw)

        return {
            "basis_w": basis_w,
            "contributing_days": contributing,
            "step_label": CAPACITY_STEPS[idx][2],
            "monthly_price_nok": CAPACITY_STEPS[idx][1],
            "provisional": contributing < 3,
        }

    def _risk_label(self, projected_kw: float) -> str:
        if len(self.top_hours) < 3:
            return "For lite historikk"

        top1 = self.top_hours[0].avg_import_w / 1000.0
        top3 = self.top_hours[2].avg_import_w / 1000.0

        if projected_kw >= top1:
            return "Svært høy"
        if projected_kw >= top3:
            return "Høy"
        if projected_kw >= top3 * 0.9:
            return "Middels"
        return "Lav"

    def _placement_text(self, projected_kw: float) -> str:
        if len(self.top_hours) < 3:
            return "Ingen månedsreferanse ennå"

        top1 = self.top_hours[0].avg_import_w / 1000.0
        top2 = self.top_hours[1].avg_import_w / 1000.0
        top3 = self.top_hours[2].avg_import_w / 1000.0

        if projected_kw > top1:
            return "Ligger an til ny #1"
        if projected_kw > top2:
            return "Ligger an til #2"
        if projected_kw > top3:
            return "Ligger an til #3"
        return "Ikke blant topp 3 nå"

    def _need_for_top3_kw(self, projected_kw: float) -> float:
        if len(self.top_hours) < 3:
            return 0.0
        top3 = self.top_hours[2].avg_import_w / 1000.0
        return 0.0 if projected_kw >= top3 else (top3 - projected_kw)

    def _top_hours_strings(self) -> List[str]:
        rows = []
        for idx in range(3):
            if idx >= len(self.top_hours):
                rows.append(f"#{idx+1}: ingen registrert ennå")
            else:
                rec = self.top_hours[idx]
                rows.append(
                    f"#{idx+1}: {rec.avg_import_w/1000.0:.3f} kW @ "
                    f"{rec.year:04d}-{rec.month:02d}-{rec.day:02d} {rec.hour:02d}:00  |  "
                    f"Peak {rec.peak_instant_w} W  |  Hopp {rec.strongest_rise_w:+d} W på {phase_label(rec.strongest_rise_phase)}"
                )
        return rows

    def _runtime_string(self) -> str:
        sec = int(time.monotonic() - self.start_monotonic)
        days, rem = divmod(sec, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{days:02d}d {hours:02d}h {minutes:02d}m {seconds:02d}s"

    def _build_system_status(self, frame: MeterFrame, event: Optional[StepEvent]) -> dict:
        # Remember whether each voltage channel has ever shown a realistic live voltage
        live_voltage_threshold = 50.0
        voltages = [frame.u1_dv / 10.0, frame.u2_dv / 10.0, frame.u3_dv / 10.0]
        for i, v in enumerate(voltages):
            if v >= live_voltage_threshold:
                self.observed_voltage_capable[i] = True

        def phase_voltage_status(name: str, voltage_v: float, observed_before: bool) -> str:
            if observed_before and voltage_v < live_voltage_threshold:
                return f"{name}: Mistet spenning"
            if not observed_before and voltage_v < live_voltage_threshold:
                return f"{name}: Ikke tilgjengelig i målerprofil"
            if voltage_v < 200.0:
                return f"{name}: Lav spenning"
            if voltage_v > 260.0:
                return f"{name}: Høy spenning"
            return f"{name}: OK"

        l1_status = phase_voltage_status("L1", voltages[0], self.observed_voltage_capable[0])
        l2_status = phase_voltage_status("L2", voltages[1], self.observed_voltage_capable[1])
        l3_status = phase_voltage_status("L3", voltages[2], self.observed_voltage_capable[2])

        lost_voltage = []
        unavailable_voltage = []
        for idx, status in enumerate([l1_status, l2_status, l3_status], start=1):
            if "Mistet spenning" in status:
                lost_voltage.append(f"L{idx}")
            elif "Ikke tilgjengelig" in status:
                unavailable_voltage.append(f"L{idx}")

        if lost_voltage:
            voltage_summary = "Mistet spenning på: " + ", ".join(lost_voltage)
        elif unavailable_voltage:
            voltage_summary = "Begrenset spenningsinfo på: " + ", ".join(unavailable_voltage)
        else:
            voltage_summary = "Ingen tydelige spenningsavvik"

        imb = imbalance_percent(frame.i1_ma, frame.i2_ma, frame.i3_ma)
        if imb < 20.0:
            balance_status = "Normal fasebalanse"
        elif imb < 40.0:
            balance_status = "Noe faseubalanse"
        elif imb < 60.0:
            balance_status = "Høy faseubalanse"
        else:
            balance_status = "Svært høy faseubalanse"

        consistency_status = "Måledata virker konsistente"
        consistency_flag = False

        total_current_a = (frame.i1_ma + frame.i2_ma + frame.i3_ma) / 1000.0
        if frame.p_import_w > 500 and total_current_a < 0.3:
            consistency_status = "Effekt er høy, men registrert strøm er svært lav"
            consistency_flag = True
        elif frame.p_import_w > 100 and frame.p_export_w > 100:
            consistency_status = "Import og eksport er samtidig uvanlig høye"
            consistency_flag = True
        elif event is not None and event.dominant_phase == 0:
            consistency_status = "Stort effekthopp uten tydelig fasebidrag"
            consistency_flag = True

        if lost_voltage:
            safety_status = "Mulig fasefeil – HAN-data kan ikke bekrefte jordfeil"
        elif imb >= 60.0:
            safety_status = "Uvanlig høy ubalanse – bør følges opp"
        elif consistency_flag:
            safety_status = "Mulig måle- eller installasjonsavvik"
        else:
            safety_status = "Ingen tydelige sikkerhetsavvik i HAN-data"

        if lost_voltage:
            total_status = "BØR UNDERSØKES"
            recommendation = "Kontroller fase / kurs. Kontakt elektriker ved symptomer."
        elif imb >= 60.0 or consistency_flag:
            total_status = "MULIG AVVIK"
            recommendation = "Følg med og sammenlign med kjente laster."
        else:
            total_status = "INGEN TYDELIGE AVVIK"
            recommendation = "Ingen tiltak nødvendig nå."

        return {
            "system_total": total_status,
            "system_voltage": voltage_summary,
            "system_l1": l1_status,
            "system_l2": l2_status,
            "system_l3": l3_status,
            "system_balance": balance_status,
            "system_consistency": consistency_status,
            "system_safety": safety_status,
            "system_recommendation": recommendation,
        }

    def process_frame(self, frame: MeterFrame) -> dict:
        self.frame_count += 1
        self.last_valid_timestamp = frame.timestamp

        self._integrate_energy(frame)
        event = self._detect_event(frame)
        self._update_hour(frame, event)
        self._update_daily_hour_profile(frame)

        if event:
            self.last_event = event

        hour_avg_kw = (self.current_hour_sum_import_w / max(1, self.current_hour_sample_count)) / 1000.0
        projected_kw = self._projected_hour_average_kw(frame)
        capacity = self._estimate_capacity(frame, projected_kw)
        system_status = self._build_system_status(frame, event)

        snapshot = {
            "timestamp": frame.timestamp,
            "meter": f"{frame.meter_id} ({frame.meter_type})",
            "meter_brand": "Kaifa" if frame.meter_type.upper().startswith("MA") else "Unknown",
            "meter_profile": "KFM_001",
            "meter_protocol": "DLMS/COSEM + M-Bus",
            "meter_id_masked": f"{frame.meter_id[:8]}••••{frame.meter_id[-4:]}" if len(frame.meter_id) >= 12 else frame.meter_id,
            "import_now_kw": frame.p_import_w / 1000.0,
            "export_now_w": frame.p_export_w,
            "import_now": f"{frame.p_import_w / 1000.0:.3f} kW",
            "export_now": f"{frame.p_export_w} W",
            "direction": grid_state_label(frame.p_import_w, frame.p_export_w),
            "trend": trend_label(0 if self.last_frame is None else frame.p_import_w - self.last_frame.p_import_w),
            "phases": (
                f"L1 {frame.i1_ma/1000.0:.3f} A / {frame.u1_dv/10.0:.1f} V    "
                f"L2 {frame.i2_ma/1000.0:.3f} A / {frame.u2_dv/10.0:.1f} V    "
                f"L3 {frame.i3_ma/1000.0:.3f} A / {frame.u3_dv/10.0:.1f} V"
            ),
            "hour_avg_kw_raw": hour_avg_kw,
            "projected_hour_kw_raw": projected_kw,
            "hour_avg": f"{hour_avg_kw:.3f} kW",
            "projected_hour": f"{projected_kw:.3f} kW",
            "peak_hour": f"{self.current_hour_peak_instant_w} W",
            "risk": self._risk_label(projected_kw),
            "placement": self._placement_text(projected_kw),
            "need_top3": (
                "Venter på tre toppdager"
                if len(self.top_hours) < 3
                else ("Allerede over #3" if self._need_for_top3_kw(projected_kw) <= 0.0001
                      else f"{self._need_for_top3_kw(projected_kw):.3f} kW")
            ),
            "largest_rise": (
                "Ingen store hopp registrert"
                if self.current_hour_strongest_event is None
                else f"+{self.current_hour_strongest_event.delta_w} W på {phase_label(self.current_hour_strongest_event.dominant_phase)} "
                     f"kl. {self.current_hour_strongest_event.timestamp.split(' ')[1]}"
            ),
            "capacity_basis": "-" if not capacity else f"{capacity['basis_w']/1000.0:.3f} kW",
            "capacity_days": "-" if not capacity else f"{capacity['contributing_days']} av 3",
            "capacity_step": "-" if not capacity else capacity["step_label"],
            "capacity_price": "-" if not capacity else f"{capacity['monthly_price_nok']} kr/mnd",
            "capacity_note": (
                "Ikke nok data ennå"
                if not capacity else
                ("Foreløpig estimat" if capacity["provisional"] else "Basert på toppdager hittil denne måneden")
            ),
            "export_today_kwh_raw": self.daily_export_wh / 1000.0,
            "export_week_kwh_raw": self.weekly_export_wh / 1000.0,
            "daily_import_kwh_raw": self.daily_import_wh / 1000.0,
            "weekly_import_kwh_raw": self.weekly_import_wh / 1000.0,
            "grid_cost_today_raw": self.daily_total_cost_grid_only_nok,
            "grid_cost_week_raw": self.weekly_total_cost_grid_only_nok,
            "export_today": f"{self.daily_export_wh/1000.0:.3f} kWh",
            "export_week": f"{self.weekly_export_wh/1000.0:.3f} kWh",
            "imbalance": f"{imbalance_percent(frame.i1_ma, frame.i2_ma, frame.i3_ma):.1f} %",
            "last_event": (
                "Ingen store effekthopp registrert"
                if self.last_event is None
                else f"{self.last_event.timestamp} | {self.last_event.delta_w:+d} W | {phase_label(self.last_event.dominant_phase)}"
            ),
            "system_total": system_status["system_total"],
            "system_voltage": system_status["system_voltage"],
            "system_l1": system_status["system_l1"],
            "system_l2": system_status["system_l2"],
            "system_l3": system_status["system_l3"],
            "system_balance": system_status["system_balance"],
            "system_consistency": system_status["system_consistency"],
            "system_safety": system_status["system_safety"],
            "system_recommendation": system_status["system_recommendation"],
            "port": self.connected_port,
            "frames": str(self.frame_count),
            "csv_path": self.csv.current_path or "-",
            "runtime": self._runtime_string(),
            "top_hours": self._top_hours_strings(),
            "daily_hourly_profile": self._daily_hourly_profile(),
        }

        csv_row = {
            "timestamp": frame.timestamp,
            "meter_id": frame.meter_id,
            "meter_type": frame.meter_type,
            "p_import_w": frame.p_import_w,
            "p_export_w": frame.p_export_w,
            "q_import_var": frame.q_import_var,
            "q_export_var": frame.q_export_var,
            "i1_a": round(frame.i1_ma / 1000.0, 3),
            "i2_a": round(frame.i2_ma / 1000.0, 3),
            "i3_a": round(frame.i3_ma / 1000.0, 3),
            "u1_v": round(frame.u1_dv / 10.0, 1),
            "u2_v": round(frame.u2_dv / 10.0, 1),
            "u3_v": round(frame.u3_dv / 10.0, 1),
            "hour_avg_kw": round(hour_avg_kw, 3),
            "projected_hour_kw": round(projected_kw, 3),
            "capacity_step": snapshot["capacity_step"],
            "capacity_price_nok_month": snapshot["capacity_price"],
            "export_today_kwh": snapshot["export_today"],
            "export_week_kwh": snapshot["export_week"],
            "imbalance_percent": snapshot["imbalance"],
            "system_total": snapshot["system_total"],
            "system_voltage": snapshot["system_voltage"],
            "system_balance": snapshot["system_balance"],
            "system_consistency": snapshot["system_consistency"],
            "system_safety": snapshot["system_safety"],
        }
        self.csv.log(csv_row, frame)

        self.last_frame = frame
        return snapshot


# =============================================================================
# UI
# =============================================================================

class DashboardUI:
    def __init__(self, root: tk.Tk, app_queue: queue.Queue, price_request_queue: queue.Queue):
        self.root = root
        self.queue = app_queue
        self.price_request_queue = price_request_queue
        self.latest_snapshot: Optional[dict] = None
        self.latest_prices: Optional[dict] = None

        self.root.title("HAN Dashboard")
        self.root.configure(bg="#0b1020")
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda e: self.root.destroy())
        self.root.bind("<F11>", self.toggle_fullscreen)

        self._setup_style()

        self.selected_area = tk.StringVar(value="NO3")

        self.vars = {
            "timestamp": tk.StringVar(value="Waiting for live data"),
            "meter": tk.StringVar(value="No meter connected yet"),
            "meter_brand": tk.StringVar(value="Waiting for meter"),
            "meter_profile": tk.StringVar(value="-"),
            "meter_protocol": tk.StringVar(value="-"),
            "meter_id_masked": tk.StringVar(value="-"),
            "import_now": tk.StringVar(value="Waiting..."),
            "export_now": tk.StringVar(value="No data yet"),
            "direction": tk.StringVar(value="Unknown"),
            "trend": tk.StringVar(value="Waiting for first frame"),
            "phases": tk.StringVar(value="Connect Arduino bridge to populate L1 / L2 / L3"),
            "hour_avg": tk.StringVar(value="No data yet"),
            "projected_hour": tk.StringVar(value="No data yet"),
            "peak_hour": tk.StringVar(value="No data yet"),
            "risk": tk.StringVar(value="Waiting for month history"),
            "placement": tk.StringVar(value="Waiting for live measurements"),
            "need_top3": tk.StringVar(value="Need live data first"),
            "largest_rise": tk.StringVar(value="No events yet"),
            "capacity_basis": tk.StringVar(value="No estimate yet"),
            "capacity_days": tk.StringVar(value="0 av 3"),
            "capacity_step": tk.StringVar(value="Unknown"),
            "capacity_price": tk.StringVar(value="Waiting for data"),
            "capacity_note": tk.StringVar(value="Connect meter to calculate estimate"),
            "export_today": tk.StringVar(value="Waiting for data"),
            "export_week": tk.StringVar(value="Waiting for data"),
            "imbalance": tk.StringVar(value="Cannot evaluate yet"),
            "last_event": tk.StringVar(value="No large events detected yet"),
            "system_total": tk.StringVar(value="No live data yet"),
            "system_voltage": tk.StringVar(value="Waiting for meter frames"),
            "system_l1": tk.StringVar(value="Unknown"),
            "system_l2": tk.StringVar(value="Unknown"),
            "system_l3": tk.StringVar(value="Unknown"),
            "system_balance": tk.StringVar(value="Waiting for current measurements"),
            "system_consistency": tk.StringVar(value="Cannot evaluate without live data"),
            "system_safety": tk.StringVar(value="No conclusion before live data"),
            "system_recommendation": tk.StringVar(value="Connect Arduino bridge and wait for valid HAN frames"),
            "port": tk.StringVar(value="Auto-scan enabled"),
            "frames": tk.StringVar(value="0"),
            "csv_path": tk.StringVar(value="Will be created after first valid frame"),
            "runtime": tk.StringVar(value="00d 00h 00m 00s"),
            "status": tk.StringVar(value="Searching for Arduino bridge..."),

            # Price-related variables
            "price_source": tk.StringVar(value="Waiting for price source"),
            "price_updated": tk.StringVar(value="No price fetch yet"),
            "spot_now": tk.StringVar(value=f"{FALLBACK_SPOT_PRICE_NOK_PER_KWH:.3f} kr/kWh (fallback)"),
            "spot_next": tk.StringVar(value="Waiting for data"),
            "spot_avg_today": tk.StringVar(value="Waiting for data"),
            "spot_cheapest": tk.StringVar(value="Waiting for data"),
            "spot_expensive": tk.StringVar(value="Waiting for data"),
            "total_cost_now": tk.StringVar(value="Waiting for data"),
            "total_cost_hour": tk.StringVar(value="Waiting for data"),
            "total_cost_today": tk.StringVar(value="Waiting for data"),
            "total_cost_week": tk.StringVar(value="Waiting for data"),
            "price_grid": tk.StringVar(value="Waiting to determine day/night tariff"),
        }

        self.top_hour_vars = [tk.StringVar(value=f"#{i+1}: waiting for a closed hour") for i in range(3)]

        self._build()

        # Trigger initial price fetch
        self.request_price_refresh()
        self.root.after(int(PRICE_REFRESH_SECONDS * 1000), self.periodic_price_refresh)
        self.root.after(150, self.poll_queue)

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Dark.TNotebook", background="#0b1020", borderwidth=0)
        style.configure(
            "Dark.TNotebook.Tab",
            background="#11182b",
            foreground="#cbd5e1",
            padding=(18, 10),
            borderwidth=0,
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", "#1c2842")],
            foreground=[("selected", "#f8fafc")],
        )
        style.configure("Dark.TCombobox", fieldbackground="#11182b", background="#11182b", foreground="#f8fafc")

    def toggle_fullscreen(self, event=None):
        current = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not current)

    def request_price_refresh(self):
        area = self.selected_area.get().strip().upper()
        if area not in PRICE_AREAS:
            area = "NO3"
            self.selected_area.set(area)
        self.price_request_queue.put(("fetch", area))

    def periodic_price_refresh(self):
        self.request_price_refresh()
        self.root.after(int(PRICE_REFRESH_SECONDS * 1000), self.periodic_price_refresh)

    def _card(self, parent, title, value_var, sub_var=None, row=0, col=0, colspan=1):
        frame = tk.Frame(parent, bg="#11182b", highlightbackground="#23304f", highlightthickness=1)
        frame.grid(row=row, column=col, columnspan=colspan, sticky="nsew", padx=8, pady=8)
        tk.Label(frame, text=title, font=("Segoe UI", 11), fg="#94a3b8", bg="#11182b").pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(frame, textvariable=value_var, font=("Segoe UI", 22, "bold"), fg="#f8fafc", bg="#11182b").pack(anchor="w", padx=16, pady=(6, 0))
        if sub_var is not None:
            tk.Label(frame, textvariable=sub_var, font=("Segoe UI", 10), fg="#cbd5e1", bg="#11182b").pack(anchor="w", padx=16, pady=(6, 14))
        else:
            tk.Label(frame, text="", bg="#11182b").pack(anchor="w", padx=16, pady=(6, 14))
        return frame

    def _panel(self, parent, title, row, col, colspan=1, rowspan=1):
        frame = tk.Frame(parent, bg="#11182b", highlightbackground="#23304f", highlightthickness=1)
        frame.grid(row=row, column=col, columnspan=colspan, rowspan=rowspan, sticky="nsew", padx=8, pady=8)
        tk.Label(frame, text=title, font=("Segoe UI", 15, "bold"), fg="#f8fafc", bg="#11182b").pack(anchor="w", padx=16, pady=(14, 10))
        return frame

    def _info_row(self, parent, label, var):
        row = tk.Frame(parent, bg="#11182b")
        row.pack(fill="x", padx=16, pady=4)
        tk.Label(row, text=label, font=("Segoe UI", 11), fg="#94a3b8", bg="#11182b").pack(side="left")
        tk.Label(row, textvariable=var, font=("Segoe UI", 11, "bold"), fg="#f8fafc", bg="#11182b", wraplength=500, justify="right").pack(side="right")

    def _build(self):
        outer = tk.Frame(self.root, bg="#0b1020")
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        header = tk.Frame(outer, bg="#11182b", highlightbackground="#23304f", highlightthickness=1)
        header.pack(fill="x", pady=(0, 10))

        left = tk.Frame(header, bg="#11182b")
        left.pack(side="left", padx=16, pady=12)
        tk.Label(left, text="HAN Energy Dashboard", font=("Segoe UI", 24, "bold"),
                 fg="#f8fafc", bg="#11182b").pack(anchor="w")
        tk.Label(left, text="Live monitoring, cost context, top-hour analysis and anomaly indicators",
                 font=("Segoe UI", 10), fg="#94a3b8", bg="#11182b").pack(anchor="w", pady=(4, 0))

        center = tk.Frame(header, bg="#11182b")
        center.pack(side="left", padx=24, pady=12)
        tk.Label(center, text="Price area", font=("Segoe UI", 10), fg="#94a3b8", bg="#11182b").pack(anchor="w")
        combo = ttk.Combobox(center, textvariable=self.selected_area, values=PRICE_AREAS, width=8, state="readonly", style="Dark.TCombobox")
        combo.pack(anchor="w", pady=(4, 0))
        combo.bind("<<ComboboxSelected>>", lambda e: self.request_price_refresh())

        right = tk.Frame(header, bg="#11182b")
        right.pack(side="right", padx=16, pady=12)
        tk.Label(right, textvariable=self.vars["timestamp"], font=("Segoe UI", 16), fg="#f8fafc", bg="#11182b").pack(anchor="e")
        tk.Label(right, textvariable=self.vars["status"], font=("Segoe UI", 10), fg="#34d399", bg="#11182b").pack(anchor="e")
        tk.Label(right, textvariable=self.vars["price_source"], font=("Segoe UI", 10), fg="#94a3b8", bg="#11182b").pack(anchor="e")
        tk.Label(right, textvariable=self.vars["price_updated"], font=("Segoe UI", 9), fg="#64748b", bg="#11182b").pack(anchor="e")

        notebook = ttk.Notebook(outer, style="Dark.TNotebook")
        notebook.pack(fill="both", expand=True)

        live_tab = tk.Frame(notebook, bg="#0b1020")
        analysis_tab = tk.Frame(notebook, bg="#0b1020")
        notebook.add(live_tab, text="Live / customer")
        notebook.add(analysis_tab, text="Analysis / system")

        self._build_live_tab(live_tab)
        self._build_analysis_tab(analysis_tab)

    def _build_live_tab(self, parent):
        grid = tk.Frame(parent, bg="#0b1020")
        grid.pack(fill="both", expand=True)
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1)
        for i in range(4):
            grid.grid_rowconfigure(i, weight=1)

        self._card(grid, "Import now", self.vars["import_now"], self.vars["total_cost_now"], row=0, col=0)
        self._card(grid, "Projected hour", self.vars["projected_hour"], self.vars["risk"], row=0, col=1)
        self._card(grid, "Capacity step", self.vars["capacity_step"], self.vars["capacity_price"], row=0, col=2)
        self._card(grid, "Solar export", self.vars["export_now"], self.vars["export_today"], row=0, col=3)

        phase_panel = self._panel(grid, "Phase overview", row=1, col=0, colspan=2)
        self._info_row(phase_panel, "Meter", self.vars["meter"])
        self._info_row(phase_panel, "Direction", self.vars["direction"])
        self._info_row(phase_panel, "Trend", self.vars["trend"])
        self._info_row(phase_panel, "L1 / L2 / L3", self.vars["phases"])
        self._info_row(phase_panel, "Imbalance", self.vars["imbalance"])

        hour_panel = self._panel(grid, "Hour status", row=1, col=2, colspan=2)
        self._info_row(hour_panel, "Hour average so far", self.vars["hour_avg"])
        self._info_row(hour_panel, "Projected hour average", self.vars["projected_hour"])
        self._info_row(hour_panel, "Highest instant this hour", self.vars["peak_hour"])
        self._info_row(hour_panel, "Compared with top 3", self.vars["placement"])
        self._info_row(hour_panel, "Need to enter top 3", self.vars["need_top3"])
        self._info_row(hour_panel, "Largest rise this hour", self.vars["largest_rise"])

        cost_panel = self._panel(grid, "Customer cost view", row=2, col=0, colspan=2)
        self._info_row(cost_panel, "Spot price now", self.vars["spot_now"])
        self._info_row(cost_panel, "Next hour spot price", self.vars["spot_next"])
        self._info_row(cost_panel, "Average spot price today", self.vars["spot_avg_today"])
        self._info_row(cost_panel, "Grid energy charge", self.vars["price_grid"])
        self._info_row(cost_panel, "Cost right now", self.vars["total_cost_now"])
        self._info_row(cost_panel, "Estimated cost this hour", self.vars["total_cost_hour"])
        self._info_row(cost_panel, "Cost today", self.vars["total_cost_today"])
        self._info_row(cost_panel, "Cost this week", self.vars["total_cost_week"])

        capacity_panel = self._panel(grid, "Capacity charge estimate", row=2, col=2, colspan=2)
        self._info_row(capacity_panel, "Capacity basis", self.vars["capacity_basis"])
        self._info_row(capacity_panel, "Contributing days", self.vars["capacity_days"])
        self._info_row(capacity_panel, "Estimated step", self.vars["capacity_step"])
        self._info_row(capacity_panel, "Estimated monthly price", self.vars["capacity_price"])
        self._info_row(capacity_panel, "Note", self.vars["capacity_note"])

        graph_panel = self._panel(grid, "Daily load graph", row=3, col=0, colspan=4)
        tk.Label(graph_panel, text="Daily hourly average import load", font=("Segoe UI", 10), fg="#94a3b8", bg="#11182b").pack(anchor="w", padx=16)
        self.load_canvas = tk.Canvas(graph_panel, bg="#0b1328", highlightbackground="#23304f", highlightthickness=1, height=220)
        self.load_canvas.pack(fill="both", expand=True, padx=16, pady=16)
        self.load_canvas.bind("<Configure>", lambda e: self.redraw_load_graph())

    def _build_analysis_tab(self, parent):
        grid = tk.Frame(parent, bg="#0b1020")
        grid.pack(fill="both", expand=True)
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1)
        for i in range(3):
            grid.grid_rowconfigure(i, weight=1)

        system_panel = self._panel(grid, "System status / anomaly indicators", row=0, col=0, colspan=2)
        self._info_row(system_panel, "Overall assessment", self.vars["system_total"])
        self._info_row(system_panel, "Voltage status", self.vars["system_voltage"])
        self._info_row(system_panel, "L1", self.vars["system_l1"])
        self._info_row(system_panel, "L2", self.vars["system_l2"])
        self._info_row(system_panel, "L3", self.vars["system_l3"])
        self._info_row(system_panel, "Phase balance", self.vars["system_balance"])
        self._info_row(system_panel, "Data consistency", self.vars["system_consistency"])
        self._info_row(system_panel, "Safety indication", self.vars["system_safety"])
        self._info_row(system_panel, "Recommendation", self.vars["system_recommendation"])

        meter_panel = self._panel(grid, "About this meter / price context", row=0, col=2, colspan=2)
        self._info_row(meter_panel, "Brand", self.vars["meter_brand"])
        self._info_row(meter_panel, "Model", self.vars["meter"])
        self._info_row(meter_panel, "Meter ID", self.vars["meter_id_masked"])
        self._info_row(meter_panel, "Profile", self.vars["meter_profile"])
        self._info_row(meter_panel, "Protocol", self.vars["meter_protocol"])
        self._info_row(meter_panel, "Price area", self.selected_area)
        self._info_row(meter_panel, "Source", self.vars["price_source"])
        self._info_row(meter_panel, "Cheapest hour today", self.vars["spot_cheapest"])
        self._info_row(meter_panel, "Most expensive hour today", self.vars["spot_expensive"])
        self._info_row(meter_panel, "Author", tk.StringVar(value="Thor Elvin Valø"))

        events_panel = self._panel(grid, "Detected load events / top hours", row=1, col=0, colspan=3, rowspan=2)
        self._info_row(events_panel, "Last large event", self.vars["last_event"])
        for i, var in enumerate(self.top_hour_vars):
            self._info_row(events_panel, f"Top hour #{i+1}", var)

        system_info_panel = self._panel(grid, "System / logging", row=1, col=3, rowspan=2)
        self._info_row(system_info_panel, "Connected port", self.vars["port"])
        self._info_row(system_info_panel, "Frames processed", self.vars["frames"])
        self._info_row(system_info_panel, "CSV log", self.vars["csv_path"])
        self._info_row(system_info_panel, "Runtime", self.vars["runtime"])
        self._info_row(system_info_panel, "Weekly export", self.vars["export_week"])
        self._info_row(system_info_panel, "Price refresh", self.vars["price_updated"])

    def redraw_load_graph(self):
        canvas = self.load_canvas
        if canvas is None:
            return

        canvas.delete("all")
        w = max(canvas.winfo_width(), 300)
        h = max(canvas.winfo_height(), 180)

        margin_left = 45
        margin_right = 20
        margin_top = 20
        margin_bottom = 28

        canvas.create_rectangle(margin_left, margin_top, w - margin_right, h - margin_bottom, outline="#23304f")

        if not self.latest_snapshot or not self.latest_snapshot.get("daily_hourly_profile"):
            canvas.create_text(
                w / 2,
                h / 2 - 10,
                text="Waiting for live data to build today's graph",
                fill="#cbd5e1",
                font=("Segoe UI", 14, "bold"),
            )
            canvas.create_text(
                w / 2,
                h / 2 + 15,
                text="Hourly import/export will appear here automatically",
                fill="#94a3b8",
                font=("Segoe UI", 10),
            )
            return

        profile = self.latest_snapshot["daily_hourly_profile"]
        present = [item["kw"] for item in profile if item.get("kw") is not None]
        if not present:
            canvas.create_text(
                w / 2,
                h / 2,
                text="No completed measurements for today yet",
                fill="#cbd5e1",
                font=("Segoe UI", 12, "bold"),
            )
            return

        max_v = max(present)
        max_v = max(max_v, 1.0)

        for frac in [0.25, 0.5, 0.75, 1.0]:
            y = h - margin_bottom - frac * (h - margin_top - margin_bottom)
            canvas.create_line(margin_left, y, w - margin_right, y, fill="#1d2740")
            canvas.create_text(
                margin_left - 8,
                y,
                text=f"{max_v * frac:.1f}",
                fill="#64748b",
                font=("Segoe UI", 9),
                anchor="e",
            )

        slot_w = (w - margin_left - margin_right) / 24.0
        prev_xy = None

        peak_idx = max(
            range(24),
            key=lambda i: -1 if profile[i].get("kw") is None else profile[i]["kw"]
        )

        for hour in range(24):
            x0 = margin_left + hour * slot_w + 2
            x1 = margin_left + (hour + 1) * slot_w - 2
            y_base = h - margin_bottom

            if hour % 2 == 0:
                canvas.create_rectangle(x0, margin_top, x1, y_base, fill="#0d1630", outline="")

            canvas.create_text(
                (x0 + x1) / 2,
                h - margin_bottom + 12,
                text=f"{hour:02d}",
                fill="#64748b",
                font=("Segoe UI", 8),
            )

            item = profile[hour]
            value_kw = item.get("kw")
            mode = item.get("mode", "idle")

            if value_kw is None:
                continue

            bar_h = (value_kw / max_v) * (h - margin_top - margin_bottom)
            y = y_base - bar_h

            if mode == "export":
                fill = "#16a34a"
                line_color = "#86efac"
            elif mode == "import":
                fill = "#1f6feb"
                line_color = "#93c5fd"
            else:
                fill = "#334155"
                line_color = "#94a3b8"

            outline = "#f59e0b" if hour == peak_idx else ""
            canvas.create_rectangle(x0, y, x1, y_base, fill=fill, outline=outline)

            cx = (x0 + x1) / 2
            cy = y
            if prev_xy is not None:
                canvas.create_line(prev_xy[0], prev_xy[1], cx, cy, fill=line_color, width=2)
            prev_xy = (cx, cy)

            if hour == peak_idx:
                label = "Peak export" if mode == "export" else "Peak import"
                canvas.create_text(
                    cx,
                    max(y - 12, margin_top + 10),
                    text=f"{label} {value_kw:.2f} kW",
                    fill="#fcd34d",
                    font=("Segoe UI", 9, "bold"),
                )

        legend_y = margin_top + 10
        canvas.create_rectangle(w - 190, legend_y, w - 178, legend_y + 12, fill="#1f6feb", outline="")
        canvas.create_text(w - 170, legend_y + 6, text="Import", fill="#cbd5e1", font=("Segoe UI", 9), anchor="w")
        canvas.create_rectangle(w - 110, legend_y, w - 98, legend_y + 12, fill="#16a34a", outline="")
        canvas.create_text(w - 90, legend_y + 6, text="Export", fill="#cbd5e1", font=("Segoe UI", 9), anchor="w")

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "snapshot":
                    self.apply_snapshot(payload)
                elif kind == "status":
                    self.vars["status"].set(payload)
                elif kind == "port":
                    self.vars["port"].set(payload)
                elif kind == "price":
                    self.apply_price(payload)
        except queue.Empty:
            pass

        self.root.after(150, self.poll_queue)

    def apply_snapshot(self, snap: dict):
        self.latest_snapshot = snap
        for key, var in self.vars.items():
            if key in snap:
                var.set(str(snap[key]))

        top_hours = snap.get("top_hours", [])
        for i in range(3):
            self.top_hour_vars[i].set(top_hours[i] if i < len(top_hours) else f"#{i+1}: waiting for a closed hour")

        self.refresh_cost_view()
        self.redraw_load_graph()

    def apply_price(self, payload: dict):
        self.latest_prices = payload

        if not payload.get("ok"):
            self.vars["price_source"].set("Price source: unavailable")
            self.vars["price_updated"].set(payload.get("status", "Price fetch failed"))
            self.refresh_cost_view()
            return

        self.vars["price_source"].set(
            f"Price source: {payload['source_name']} · {payload['area']} · {payload['source_note']}"
        )
        self.vars["price_updated"].set(f"Prices updated: {payload['fetched_at']}")

        self.vars["spot_now"].set(
            f"{payload['current_price']:.3f} kr/kWh" if payload.get("current_price") is not None else "Current hour not found"
        )
        self.vars["spot_next"].set(
            f"{payload['next_price']:.3f} kr/kWh" if payload.get("next_price") is not None else "Next hour not available yet"
        )
        self.vars["spot_avg_today"].set(
            f"{payload['avg_today']:.3f} kr/kWh" if payload.get("avg_today") is not None else "No data"
        )
        self.vars["spot_cheapest"].set(payload.get("cheapest_label", "No data"))
        self.vars["spot_expensive"].set(payload.get("most_expensive_label", "No data"))

        self.refresh_cost_view()

    def refresh_cost_view(self):
        snap = self.latest_snapshot
        prices = self.latest_prices

        if snap is None:
            return

        # Spot price selection:
        # - use web data if available
        # - otherwise fall back to a manual fixed price
        spot_now = FALLBACK_SPOT_PRICE_NOK_PER_KWH
        if prices and prices.get("ok") and prices.get("current_price") is not None:
            spot_now = float(prices["current_price"])

        price_area = self.selected_area.get()
        self.vars["price_grid"].set(
            f"{current_grid_rate_label(datetime.now().hour)} = {current_grid_rate(datetime.now().hour):.4f} kr/kWh · {price_area}"
        )

        import_now_kw = float(snap.get("import_now_kw", 0.0))
        projected_kw = float(snap.get("projected_hour_kw_raw", 0.0))
        total_rate = spot_now + current_grid_rate(datetime.now().hour)

        self.vars["total_cost_now"].set(f"{import_now_kw * total_rate:.2f} kr/time")
        self.vars["total_cost_hour"].set(f"{projected_kw * total_rate:.2f} kr")

        daily_import_kwh = float(snap.get("daily_import_kwh_raw", 0.0))
        weekly_import_kwh = float(snap.get("weekly_import_kwh_raw", 0.0))
        daily_grid_only = float(snap.get("grid_cost_today_raw", 0.0))
        weekly_grid_only = float(snap.get("grid_cost_week_raw", 0.0))

        # Approximate daily/weekly totals using current spot as a practical running estimate
        self.vars["total_cost_today"].set(f"{daily_grid_only + daily_import_kwh * spot_now:.2f} kr")
        self.vars["total_cost_week"].set(f"{weekly_grid_only + weekly_import_kwh * spot_now:.2f} kr")


# =============================================================================
# Workers
# =============================================================================

def serial_worker(app_queue: queue.Queue, model: DashboardModel, baudrate: int, preferred_port: Optional[str]):
    while True:
        try:
            app_queue.put(("status", "Searching serial ports..."))
            ser = auto_connect_serial(
                baudrate,
                preferred_port=preferred_port,
                status_callback=lambda msg: app_queue.put(("status", msg)),
            )
            preferred_port = None

            port_name = ser.port
            model.set_connected_port(port_name)
            app_queue.put(("port", port_name))
            app_queue.put(("status", f"Connected to {port_name}"))

            try:
                while True:
                    raw = ser.readline()
                    if not raw:
                        continue

                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    if not is_valid_frame_line(line):
                        continue

                    frame_bytes = parse_frame_line(line)
                    parsed = parse_kaifa_kfm001(frame_bytes)
                    if parsed is None:
                        continue

                    snapshot = model.process_frame(parsed)
                    app_queue.put(("snapshot", snapshot))

            except Exception as e:
                debug(f"Serial connection lost: {e}")
                app_queue.put(("status", f"Serial connection lost: {e}. Returning to auto-scan..."))
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1.0)

        except Exception as e:
            debug(f"Worker error: {e}")
            traceback.print_exc()
            app_queue.put(("status", f"Worker error: {e}"))
            time.sleep(RETRY_DELAY_SECONDS)


def price_worker(app_queue: queue.Queue, request_queue: queue.Queue):
    service = PriceService()
    last_area = None

    while True:
        try:
            cmd, area = request_queue.get()
            if cmd != "fetch":
                continue

            area = area.strip().upper()
            if area not in PRICE_AREAS:
                area = "NO3"

            last_area = area
            debug(f"Fetching prices for {area}")
            try:
                payload = service.fetch(area)
                app_queue.put(("price", payload))
            except urllib.error.URLError as e:
                app_queue.put(("price", {
                    "ok": False,
                    "status": f"Price fetch failed: {e}. Falling back to manual spot price.",
                    "area": area,
                }))
            except Exception as e:
                app_queue.put(("price", {
                    "ok": False,
                    "status": f"Price fetch failed: {e}. Falling back to manual spot price.",
                    "area": area,
                }))

        except Exception as e:
            debug(f"Price worker error: {e}")
            time.sleep(1.0)


# =============================================================================
# Main
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(description="HAN dashboard with tabs and price-area support")
    parser.add_argument("--port", help="Preferred serial port, e.g. COM7")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Serial baud rate (default {DEFAULT_BAUD})")
    return parser.parse_args()


def main():
    args = parse_args()

    app_queue: queue.Queue = queue.Queue()
    price_request_queue: queue.Queue = queue.Queue()

    logger = CsvLogger()
    model = DashboardModel(logger)

    root = tk.Tk()
    ui = DashboardUI(root, app_queue, price_request_queue)

    t_serial = threading.Thread(
        target=serial_worker,
        args=(app_queue, model, args.baud, args.port),
        daemon=True,
    )
    t_serial.start()

    t_price = threading.Thread(
        target=price_worker,
        args=(app_queue, price_request_queue),
        daemon=True,
    )
    t_price.start()

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        debug(f"Unhandled exception: {e}")
        traceback.print_exc()
        input("Press Enter to close...")
