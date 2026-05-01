import requests
import time
import random
import datetime

# Configuration
API_URL = "http://localhost:8000/ingest"
SENSOR_ID = "SIM_DATA_001"
VILLAGE = "Simulation Hub"

def generate_random_reading():
    """Generates realistic but randomized water quality data with clear safe/unsafe separation."""
    is_safe = random.random() > 0.5
    
    base = {
        "sensor_id": SENSOR_ID,
        "village": VILLAGE,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    
    if is_safe:
        # Safe ranges
        base.update({
            "ph": round(random.uniform(6.8, 8.2), 2),
            "temperature": round(random.uniform(20.0, 24.0), 1),
            "turbidity": round(random.uniform(0.5, 3.5), 2),
            "tds": round(random.uniform(100.0, 400.0), 0),
            "location": "Simulated Tank A (Clean)"
        })
    else:
        # Unsafe ranges (Force failures in ph, turbidity or tds)
        base.update({
            "ph": round(random.choice([random.uniform(4.0, 6.0), random.uniform(9.0, 11.0)]), 2),
            "temperature": round(random.uniform(28.0, 38.0), 1),
            "turbidity": round(random.uniform(7.0, 15.0), 2),
            "tds": round(random.uniform(600.0, 1500.0), 0),
            "location": "Simulated Tank B (Contaminated)"
        })
    return base

def run_simulator():
    print(f"🚀 Starting Improved IoT Simulator for {SENSOR_ID}...")
    print(f"Targeting: {API_URL}")
    
    try:
        while True:
            data = generate_random_reading()
            try:
                response = requests.post(API_URL, json=data, timeout=5)
                if response.status_code == 200:
                    result = response.json()
                    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] pH={data['ph']} Turb={data['turbidity']} → Result: {result.get('prediction')}")
                else:
                    print(f"❌ API error: {response.status_code}")
            except Exception as e:
                print(f"❌ Connection failed: {e}")
                
            # Random interval between 2 and 5 seconds for realism
            time.sleep(random.uniform(2, 5))
            
    except KeyboardInterrupt:
        print("\n👋 Simulator stopped.")

if __name__ == "__main__":
    run_simulator()
