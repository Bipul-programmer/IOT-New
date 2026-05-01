import serial
import serial.tools.list_ports
import requests
import time
import re

# ── Configuration ─────────────────────────────────────────────────────────────
API_URL         = "http://localhost:8000/ingest"
BAUD_RATE       = 115200
RECONNECT_DELAY = 5   # seconds between reconnect attempts
SEND_TIMEOUT    = 5   # HTTP request timeout

# Keys the ESP32 uses — used for sanity-check before parsing
REQUIRED_KEYS = ["ph", "tds", "temp"]   # lowercase substrings expected in line

# ── Port Detection ────────────────────────────────────────────────────────────
def find_esp32_port():
    """Scan for ESP32/Arduino serial ports on macOS/Linux/Windows."""
    ports = serial.tools.list_ports.comports()
    for port in ports:
        dev = port.device.lower()
        if any(kw in dev for kw in ["usbserial", "usbmodem", "cu.usbserial",
                                     "cu.usbmodem", "ttyusb", "ttyacm"]):
            print(f"🔍 Found device: {port.device} — {port.description}")
            return port.device
    return None

# ── Line Cleaner ──────────────────────────────────────────────────────────────
def clean_line(raw_bytes):
    """
    Decode bytes, strip whitespace and any leading non-ASCII emoji prefix
    (e.g. '📥 ') that some ESP32 firmware prepends.
    Returns a clean ASCII string or empty string.
    """
    try:
        line = raw_bytes.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""
    # Strip leading emoji / non-ASCII characters (e.g. '📥 ')
    line = re.sub(r'^[^\x00-\x7F]+\s*', '', line)
    return line.strip()

def looks_complete(line):
    """
    Quick sanity check: a valid line must contain all 3 expected key substrings
    AND at least 3 '=' signs so partial reads are silently discarded.
    """
    lower = line.lower()
    has_all_keys = all(k in lower for k in REQUIRED_KEYS)
    has_equals   = line.count("=") >= 3
    return has_all_keys and has_equals

# ── Parsers ───────────────────────────────────────────────────────────────────
def parse_keyvalue(line):
    """
    Parses 'key=value,...' format.
    Example: 'pH=6.25,Temp=24.50,TDS=350.00'
    """
    key_map = {
        "ph":          ["ph"],
        "temperature": ["temp", "temperature"],
        "tds":         ["tds"],
    }
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

def parse_csv(line):
    """
    Parses positional CSV.
    Expected order: sensor_id(optional), ph, temperature, tds
    """
    parts = [p.strip() for p in line.split(",")]
    numerics = []
    for p in parts:
        try:
            numerics.append(float(p))
        except ValueError:
            continue
    if len(numerics) >= 3:
        return {"ph": numerics[0], "temperature": numerics[1], "tds": numerics[2]}
    return None

def parse_line(line):
    """Try key=value first, then fall back to positional CSV."""
    if "=" in line:
        return parse_keyvalue(line)
    return parse_csv(line)

# ── Validation ────────────────────────────────────────────────────────────────
def is_valid(data):
    try:
        return (
            0   <= data["ph"]          <= 14   and
            -10 <= data["temperature"] <= 80   and
            0   <= data["tds"]         <= 2000
        )
    except (KeyError, TypeError):
        return False

# ── HTTP send ─────────────────────────────────────────────────────────────────
def send_to_backend(payload):
    try:
        resp = requests.post(API_URL, json=payload, timeout=SEND_TIMEOUT)
        if resp.status_code == 200:
            result = resp.json()
            print(
                f"  ✅ Sent → Quality: {result.get('prediction'):10s} | "
                f"Contamination: {result.get('contamination_level', 0)*100:.1f}%"
            )
        else:
            print(f"  ❌ Backend error {resp.status_code}: {resp.text[:120]}")
    except requests.exceptions.ConnectionError:
        print("  ❌ Cannot reach backend. Is 'python main.py' running?")
    except requests.exceptions.Timeout:
        print("  ⚠️  Backend timed out — data dropped.")
    except Exception as e:
        print(f"  ❌ API error: {e}")

# ── Main loop with auto-reconnect ─────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  Water Quality Serial Bridge")
    print("  Streaming ESP32 → Backend → Dashboard")
    print("=" * 55)

    while True:                             # outer loop: keep looking for port
        port = find_esp32_port()
        if not port:
            print(f"⏳ ESP32 not found. Retrying in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)
            continue

        try:
            ser = serial.Serial(port, BAUD_RATE, timeout=2)
            # Flush any stale data from a previous session so we start clean
            ser.reset_input_buffer()
            time.sleep(0.5)                 # wait half a second for ESP32 to settle
            print(f"✅ Connected to {port} at {BAUD_RATE} baud.\n")
        except serial.SerialException as e:
            print(f"❌ Could not open port {port}: {e}")
            time.sleep(RECONNECT_DELAY)
            continue

        try:
            while True:                     # inner loop: read lines
                try:
                    raw = ser.readline()    # blocks until '\n' or timeout
                    if not raw:
                        continue            # timeout with no data — keep waiting

                    line = clean_line(raw)
                    if not line:
                        continue

                    # ── Sanity check: discard partial/corrupt lines silently ──
                    if not looks_complete(line):
                        # Only log if the line is long enough to look suspicious
                        if len(line) > 5:
                            print(f"  ⏭  Skipped partial line: {line!r}")
                        continue

                    print(f"📡 RAW: {line}")

                    parsed = parse_line(line)
                    if not parsed:
                        print(f"  ⚠️  Parse failed: {line!r}")
                        continue

                    if not is_valid(parsed):
                        print(f"  ⚠️  Out-of-range filtered: {parsed}")
                        continue

                    payload = {
                        "sensor_id": "ESP32_PHYSICAL",
                        "village":   "Real-time Site",
                        "location":  "Physical Sensor",
                        **parsed
                    }
                    send_to_backend(payload)

                except serial.SerialException as e:
                    print(f"\n🔌 Serial disconnected: {e}")
                    break                   # break inner → outer will reconnect

        except KeyboardInterrupt:
            print("\n👋 Bridge stopped by user.")
            try:
                ser.close()
            except Exception:
                pass
            return

        print(f"🔄 Reconnecting in {RECONNECT_DELAY}s...")
        try:
            ser.close()
        except Exception:
            pass
        time.sleep(RECONNECT_DELAY)

if __name__ == "__main__":
    main()
