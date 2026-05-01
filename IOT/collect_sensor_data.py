"""
collect_sensor_data.py — Collects raw readings from the ESP32 and labels them
using WHO water quality standards (rule-based, not ML-based).

Target: 10,000+ diverse readings saved to sensor_dataset.csv
Usage:  python collect_sensor_data.py
        python collect_sensor_data.py --target 15000
"""

import serial
import serial.tools.list_ports
import csv
import os
import re
import time
import argparse
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────
OUTPUT_CSV  = "sensor_dataset.csv"
BAUD_RATE   = 115200
RECONNECT   = 5    # seconds between reconnect attempts

# ── WHO Water Quality Thresholds ──────────────────────────────────────────────
# Source: WHO Guidelines for Drinking-water Quality (4th edition)
PH_MIN, PH_MAX   = 6.5, 8.5   # acceptable pH range
TDS_MAX           = 500         # mg/L (ppm) — WHO guideline
TEMP_MAX          = 30          # °C — above this increases microbial risk

def rule_based_label(ph, tds, temperature):
    """
    Assign potability based on WHO thresholds.
    Returns 1 (Safe) or 0 (Unsafe).
    """
    if not (PH_MIN <= ph <= PH_MAX):
        return 0
    if tds > TDS_MAX:
        return 0
    if temperature > TEMP_MAX:
        return 0
    return 1

# ── Port Detection ─────────────────────────────────────────────────────────────
def find_esp32_port():
    for port in serial.tools.list_ports.comports():
        dev = port.device.lower()
        if any(kw in dev for kw in ["usbserial", "usbmodem", "cu.usbserial",
                                     "cu.usbmodem", "ttyusb", "ttyacm"]):
            return port.device
    return None

# ── Line Utilities ─────────────────────────────────────────────────────────────
def clean_line(raw_bytes):
    try:
        line = raw_bytes.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    return re.sub(r'^[^\x00-\x7F]+\s*', '', line).strip()

REQUIRED_KEYS = ["ph", "tds", "temp"]

def looks_complete(line):
    lower = line.lower()
    return all(k in lower for k in REQUIRED_KEYS) and line.count("=") >= 3

def parse_keyvalue(line):
    key_map = {"ph": ["ph"], "temperature": ["temp", "temperature"], "tds": ["tds"]}
    raw = {}
    for part in line.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            raw[k.strip().lower()] = v.strip()
    parsed = {}
    for std_key, aliases in key_map.items():
        for alias in aliases:
            if alias in raw:
                try:
                    parsed[std_key] = float(raw[alias])
                except ValueError:
                    return None
                break
    return parsed if len(parsed) == 3 else None

def is_valid(d):
    try:
        return 0 <= d["ph"] <= 14 and -10 <= d["temperature"] <= 80 and 0 <= d["tds"] <= 2000
    except (KeyError, TypeError):
        return False

# ── Main Collector ─────────────────────────────────────────────────────────────
def collect(target_count=10000):
    print("=" * 60)
    print(f"  Sensor Data Collector  →  {OUTPUT_CSV}")
    print(f"  Target: {target_count:,} readings")
    print(f"  Labels: Rule-based (WHO standards)")
    print("=" * 60)

    # Start fresh — always write a new file
    with open(OUTPUT_CSV, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=["ph", "temperature", "tds", "Potability", "timestamp"]).writeheader()

    count = 0
    safe_count = 0
    unsafe_count = 0

    while True:
        port = find_esp32_port()
        if not port:
            print(f"⏳ ESP32 not found. Retrying in {RECONNECT}s...")
            time.sleep(RECONNECT)
            continue

        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=2)
            ser.reset_input_buffer()
            time.sleep(0.5)
            print(f"✅ Connected: {port}\n")
        except serial.SerialException as e:
            print(f"❌ Port error: {e}")
            time.sleep(RECONNECT)
            continue

        try:
            while count < target_count:
                try:
                    raw = ser.readline()
                    if not raw:
                        continue

                    line = clean_line(raw)
                    if not line or not looks_complete(line):
                        continue

                    parsed = parse_keyvalue(line)
                    if not parsed or not is_valid(parsed):
                        continue

                    ph    = parsed["ph"]
                    tds   = parsed["tds"]
                    temp  = parsed["temperature"]
                    label = rule_based_label(ph, tds, temp)
                    ts    = datetime.now(timezone.utc).isoformat()

                    with open(OUTPUT_CSV, "a", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=["ph", "temperature", "tds", "Potability", "timestamp"])
                        writer.writerow({"ph": ph, "temperature": temp, "tds": tds, "Potability": label, "timestamp": ts})

                    count += 1
                    if label == 1:
                        safe_count += 1
                    else:
                        unsafe_count += 1

                    pct = count / target_count * 100
                    label_str = "✅ Safe  " if label == 1 else "❌ Unsafe"
                    print(f"  [{count:>6}/{target_count}  {pct:5.1f}%]  "
                          f"pH={ph:.2f}  Temp={temp:.1f}°C  TDS={tds:.0f}ppm  →  {label_str}")

                    if count % 500 == 0:
                        print(f"\n  ── Checkpoint {count} ──  "
                              f"Safe: {safe_count} ({safe_count/count*100:.0f}%)  "
                              f"Unsafe: {unsafe_count} ({unsafe_count/count*100:.0f}%)\n")

                except serial.SerialException as e:
                    print(f"\n🔌 Disconnected: {e}")
                    break

        except KeyboardInterrupt:
            print(f"\n\n👋 Stopped early at {count:,} readings.")
            break

        if count >= target_count:
            break

        print(f"🔄 Reconnecting in {RECONNECT}s...")
        try:
            ser.close()
        except Exception:
            pass
        time.sleep(RECONNECT)

    try:
        ser.close()
    except Exception:
        pass

    print("\n" + "=" * 60)
    print(f"  Collection complete!")
    print(f"  Total readings : {count:,}")
    print(f"  Safe           : {safe_count:,} ({safe_count/max(count,1)*100:.1f}%)")
    print(f"  Unsafe         : {unsafe_count:,} ({unsafe_count/max(count,1)*100:.1f}%)")
    print(f"  Saved to       : {OUTPUT_CSV}")
    print("=" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ESP32 water quality data collector")
    parser.add_argument("--target", type=int, default=10000, help="Number of readings to collect")
    args = parser.parse_args()
    collect(args.target)
