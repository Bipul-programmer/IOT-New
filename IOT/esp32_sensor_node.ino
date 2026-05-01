/**
 * ESP32 Water Quality Sensor Node
 * Sends data to the Python Backend via Serial
 * Format: pH=6.25,Temp=24.50,TDS=350.00
 * (turbidity removed - using 3-feature ML model)
 */

#include <Arduino.h>

// Configuration
const char* SENSOR_ID = "ESP32_NODE_01";
const int BAUD_RATE = 115200;

// Sensor Pins (Adjust based on your wiring)
const int PH_PIN   = 34;
const int TEMP_PIN = 35;
const int TDS_PIN  = 33;

void setup() {
  Serial.begin(BAUD_RATE);
  delay(1000);
  Serial.println("ESP32 Water Quality Sensor Started");
}

float readPH() {
  int raw = analogRead(PH_PIN);
  // Calibration: 0-4095 ADC -> 0-14 pH range
  return (raw / 4095.0) * 14.0;
}

float readTemp() {
  int raw = analogRead(TEMP_PIN);
  // Mapping to typical water temperature range 15-40°C
  return 15.0 + (raw / 4095.0) * 25.0;
}

float readTDS() {
  int raw = analogRead(TDS_PIN);
  // Mapping ADC to 0-1200 ppm range
  return (raw / 4095.0) * 1200.0;
}

void loop() {
  float ph   = readPH();
  float temp = readTemp();
  float tds  = readTDS();

  // Send in key=value format the Python bridge expects
  Serial.print("pH=");
  Serial.print(ph, 2);
  Serial.print(",Temp=");
  Serial.print(temp, 2);
  Serial.print(",TDS=");
  Serial.println(tds, 2);

  delay(5000); // Send every 5 seconds
}
