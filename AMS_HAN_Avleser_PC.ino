#include <SoftwareSerial.h>

/*
  Lightweight HAN bridge for Arduino Uno/Nano
  -------------------------------------------
  Purpose:
    - Read HAN data from a TTL/M-Bus adapter on SoftwareSerial
    - Reconstruct HDLC-style frames using 0x7E delimiters
    - Unescape 0x7D byte stuffing
    - Forward each complete frame to USB serial as machine-readable text
    - Flash the built-in LED briefly every time a frame is sent to the PC

  No analysis or calculations are performed on the Arduino.
  The PC application is responsible for parsing, logging, and visualization.

  Output format on USB serial:
    FRAME,<sequence>,<length>,<HEX_PAYLOAD>

  Example:
    FRAME,42,121,A07901020110...

  Wiring:
    HAN adapter data output -> D2
    HAN adapter GND         -> GND
*/

const uint8_t HAN_RX_PIN = 2;
const uint8_t HAN_TX_PIN = 3;   // Required by SoftwareSerial, not used
const bool HAN_INVERTED = false;

const unsigned long HAN_BAUD = 2400;
const unsigned long USB_BAUD = 115200;

// LED pulse length each time a frame is forwarded to the PC
const unsigned long LED_PULSE_MS = 30;

SoftwareSerial han(HAN_RX_PIN, HAN_TX_PIN, HAN_INVERTED);

// Frame buffer
const size_t MAX_FRAME_LEN = 220;
uint8_t frameBuf[MAX_FRAME_LEN];
size_t frameLen = 0;
bool frameStarted = false;
bool escapeNext = false;
uint32_t frameSequence = 0;

// Non-blocking LED pulse timer
unsigned long ledPulseUntil = 0;

/*
  Print one byte as two uppercase hex characters.
  Example: 0xAF -> "AF"
*/
void printHexByte(uint8_t b) {
  const char HEX_CHARS[] = "0123456789ABCDEF";
  Serial.write(HEX_CHARS[(b >> 4) & 0x0F]);
  Serial.write(HEX_CHARS[b & 0x0F]);
}

/*
  Start a short LED pulse without blocking the loop.
*/
void pulseBuiltInLed() {
  digitalWrite(LED_BUILTIN, HIGH);
  ledPulseUntil = millis() + LED_PULSE_MS;
}

/*
  Turn the LED back off when the pulse time has expired.
*/
void updateBuiltInLed() {
  if (ledPulseUntil != 0 && (long)(millis() - ledPulseUntil) >= 0) {
    digitalWrite(LED_BUILTIN, LOW);
    ledPulseUntil = 0;
  }
}

/*
  Send one complete frame to the PC in machine-readable format:
    FRAME,<sequence>,<length>,<HEX_PAYLOAD>
*/
void emitFrame(const uint8_t *buf, size_t len) {
  Serial.print(F("FRAME,"));
  Serial.print(frameSequence++);
  Serial.print(',');
  Serial.print(len);
  Serial.print(',');

  for (size_t i = 0; i < len; i++) {
    printHexByte(buf[i]);
  }

  Serial.println();

  // Blink LED each time a frame is sent to the PC
  pulseBuiltInLed();
}

/*
  Feed bytes from the HAN serial stream into the HDLC frame parser.

  Rules:
    - 0x7E = frame delimiter
    - 0x7D = escape marker
    - escaped byte is XORed with 0x20
*/
void handleIncomingByte(uint8_t b) {
  // HDLC frame delimiter
  if (b == 0x7E) {
    // If we were already inside a frame and it contains data, emit it
    if (frameStarted && frameLen > 0) {
      emitFrame(frameBuf, frameLen);
    }

    // Start a fresh frame
    frameStarted = true;
    frameLen = 0;
    escapeNext = false;
    return;
  }

  // Ignore bytes until first delimiter has been seen
  if (!frameStarted) {
    return;
  }

  // Escape marker
  if (b == 0x7D) {
    escapeNext = true;
    return;
  }

  // Unescape next byte
  if (escapeNext) {
    b ^= 0x20;
    escapeNext = false;
  }

  // Store byte in frame buffer
  if (frameLen < MAX_FRAME_LEN) {
    frameBuf[frameLen++] = b;
  } else {
    // Overflow: drop frame and wait for next delimiter
    frameStarted = false;
    frameLen = 0;
    escapeNext = false;
    Serial.println(F("WARN,FRAME_OVERFLOW"));
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  Serial.begin(USB_BAUD);
  han.begin(HAN_BAUD);

  Serial.println(F("BOOT,HAN_BRIDGE_LIGHT"));
  Serial.print(F("INFO,RX_PIN,"));
  Serial.println(HAN_RX_PIN);
  Serial.print(F("INFO,HAN_BAUD,"));
  Serial.println(HAN_BAUD);
  Serial.print(F("INFO,USB_BAUD,"));
  Serial.println(USB_BAUD);
  Serial.print(F("INFO,LED_PULSE_MS,"));
  Serial.println(LED_PULSE_MS);
}

void loop() {
  while (han.available() > 0) {
    uint8_t b = (uint8_t)han.read();
    handleIncomingByte(b);
  }

  // Keep LED pulse timing non-blocking
  updateBuiltInLed();
}