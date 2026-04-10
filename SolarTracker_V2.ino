#include <Servo.h>
#include <avr/interrupt.h>
#include <avr/sleep.h>
#include <avr/wdt.h>

/* ==============================================================
 *  ADVANCED SOLAR TRACKER v2.0
 *  WE ARE X-2 ANGKATAN 39
 * ============================================================== */

// ======================== CONFIGURASI ========================

const bool DEBUG_MODE = true;

// --- Servo ---
const bool  IS_FLIPPED  = true;
const int   BATAS_MIN   = 82;
const int   BATAS_MAX   = 170;
const int   PIN_SERVO   = 9;
const int   PIN_OUTPUT_11 = 11;

// --- Sensor LDR ---
const int   PIN_LDR_L   = A0;
const int   PIN_LDR_R   = A1;

// --- Thresholds ---
const int   TOLERANSI         = 20;   // Selisih LDR minimum
const int   TOLERANSI_STOP    = 20;   // stop?
const int   BATAS_BANGUN      = 650;  // Kapan aktif?
const int   BATAS_TIDUR       = 850;  // Kapan tidur?

// --- Oversampling ---
const int   JUMLAH_SAMPLE = 4;   // cek data sensor 4 kali sebelum eksekusi perintah

/* --- FSM Timing REAL ONE --- (deprecated)
const unsigned long SLEEP_INTERVAL_PROD   = 60000;
const unsigned long IDLE_INTERVAL_PROD    = 10000;
const unsigned long VERIFY_INTERVAL_PROD  = 1000;
const unsigned long HUNTING_INTERVAL_PROD = 50;
*/

// --- FSM Timing Untuk DEMO ---
const unsigned long SLEEP_INTERVAL_PROD   = 1000;
const unsigned long IDLE_INTERVAL_PROD    = 500;
const unsigned long VERIFY_INTERVAL_PROD  = 250;
const unsigned long HUNTING_INTERVAL_PROD = 50;

// --- FSM Timing GUI ---
const unsigned long SLEEP_INTERVAL_GUI   = 5000;
const unsigned long IDLE_INTERVAL_GUI    = 2000;
const unsigned long VERIFY_INTERVAL_GUI  = 500;
const unsigned long HUNTING_INTERVAL_GUI = 50;

// --- FSM Timing PRESENTASI ---
const unsigned long SLEEP_INTERVAL_PRES   = 1000;
const unsigned long IDLE_INTERVAL_PRES    = 500;
const unsigned long VERIFY_INTERVAL_PRES  = 250;   
const unsigned long HUNTING_INTERVAL_PRES = 50;     

// --- Are YOU safe? ---
const int           VERIFY_COUNT_TARGET = 3;
const int           WAKE_CONFIRM_TARGET = 2;
const int           DARK_CONFIRM_TARGET = 3;
const int           HUNT_STABLE_TARGET  = 2;
const int           HUNT_LIMIT_TARGET   = 2;
const unsigned long HUNTING_TIMEOUT     = 30000;

// --- Host/runtime ---
const unsigned long HOST_HEARTBEAT_TIMEOUT = 4000;
const unsigned long DATA_SEND_INTERVAL     = 100;  // GUI/PRESENTATION only
const unsigned long SENSOR_CACHE_MAX_AGE   = 120;


// ======================== DO NOT TOUCH THIS CODE BELOW!!! ( except you a programmer :} ) ========================
// ======================== ENUMS ========================

enum State {
  STATE_SLEEP,
  STATE_IDLE,
  STATE_VERIFY,
  STATE_HUNTING
};

enum Pin11Mode {
  PIN11_AUTO,
  PIN11_FORCE_OFF,
  PIN11_FORCE_ON
};

enum RunMode {
  RUN_STANDALONE,
  RUN_GUI,
  RUN_PRESENTATION
};

// ======================== GLOBALS ========================

Servo tracker;
int   pos            = 126;
bool  servoAttached  = false;

State currentState   = STATE_IDLE;
State prevState      = STATE_IDLE;
RunMode runMode      = RUN_STANDALONE;

unsigned long lastCheckTime    = 0;
unsigned long lastDataSendTime = 0;
unsigned long stateEntryTime   = 0;
unsigned long huntingStartTime = 0;
unsigned long lastHostPingTime = 0;
unsigned long lastSensorReadTime = 0;
unsigned long sleepCompensationMs = 0;

int verifyCount = 0;
int brightConfirmCount = 0;
int darkConfirmCount   = 0;
int huntStableCount    = 0;
int huntLimitCount     = 0;

// Cached sensor values
int valL     = 0;
int valR     = 0;
int selisih  = 0;
int rataRata = 0;

// --- Debug/Sim variables (FOR TESTING) ---
bool simMode  = false;   // LDR simulasi aktif?
bool presMode = false;   // Presentation speed aktif?
int  simValL  = 512;     // Nilai simulasi LDR kiri
int  simValR  = 512;     // Nilai simulasi LDR kanan
Pin11Mode pin11Mode = PIN11_AUTO;

// Serial command buffer
char cmdBuffer[32];
int  cmdIndex = 0;

volatile bool watchdogFired = false;

ISR(WDT_vect) {
  watchdogFired = true;
}

// ======================== HELPER: Get interval ========================

unsigned long trackedMillis() {
  return millis() + sleepCompensationMs;
}

unsigned long getInterval(unsigned long prod, unsigned long gui, unsigned long pres) {
  if (runMode == RUN_PRESENTATION) return pres;
  if (runMode == RUN_GUI) return gui;
  return prod;
}

unsigned long getCurrentStateInterval() {
  switch (currentState) {
    case STATE_SLEEP:
      return getInterval(SLEEP_INTERVAL_PROD, SLEEP_INTERVAL_GUI, SLEEP_INTERVAL_PRES);
    case STATE_IDLE:
      return getInterval(IDLE_INTERVAL_PROD, IDLE_INTERVAL_GUI, IDLE_INTERVAL_PRES);
    case STATE_VERIFY:
      return getInterval(VERIFY_INTERVAL_PROD, VERIFY_INTERVAL_GUI, VERIFY_INTERVAL_PRES);
    case STATE_HUNTING:
      return getInterval(HUNTING_INTERVAL_PROD, HUNTING_INTERVAL_GUI, HUNTING_INTERVAL_PRES);
  }
  return IDLE_INTERVAL_PROD;
}

bool telemetryEnabled() {
  return runMode != RUN_STANDALONE;
}

bool useTrueSleep() {
  return (runMode == RUN_STANDALONE && currentState == STATE_SLEEP);
}

unsigned long getSensorAgeMs(unsigned long now) {
  if (lastSensorReadTime == 0) return 0;
  return now - lastSensorReadTime;
}

uint8_t getSleepKindFlag() {
  return useTrueSleep() ? 1 : 0;
}

void markHostActive(bool presentationRequested = false) {
  unsigned long now = trackedMillis();
  lastHostPingTime = now;
  if (presentationRequested) {
    runMode = RUN_PRESENTATION;
    presMode = true;
  } else {
    if (runMode == RUN_STANDALONE) runMode = RUN_GUI;
    if (runMode != RUN_PRESENTATION) presMode = false;
  }
}

void setGuiMode() {
  lastHostPingTime = trackedMillis();
  runMode = RUN_GUI;
  presMode = false;
}

void updateRuntimeMode(unsigned long now) {
  if (runMode != RUN_STANDALONE && (now - lastHostPingTime > HOST_HEARTBEAT_TIMEOUT)) {
    runMode = RUN_STANDALONE;
    presMode = false;
    Serial.println(F("LOG:RUNTIME -> STANDALONE"));
    if (currentState != STATE_HUNTING) servoOff();
  }
}

const __FlashStringHelper* getRunModeName(RunMode mode) {
  switch (mode) {
    case RUN_STANDALONE:   return F("STANDALONE");
    case RUN_GUI:          return F("GUI");
    case RUN_PRESENTATION: return F("PRESENTATION");
  }
  return F("UNKNOWN");
}

bool shouldEmitSleepLog() {
  return !useTrueSleep();
}

bool shouldEmitVerboseLoopLogs() {
  return runMode == RUN_STANDALONE;
}

uint8_t getWdtPeriodCode(unsigned long ms) {
  if (ms <= 16)   return WDTO_15MS;
  if (ms <= 32)   return WDTO_30MS;
  if (ms <= 64)   return WDTO_60MS;
  if (ms <= 125)  return WDTO_120MS;
  if (ms <= 250)  return WDTO_250MS;
  if (ms <= 500)  return WDTO_500MS;
  if (ms <= 1000) return WDTO_1S;
  if (ms <= 2000) return WDTO_2S;
  if (ms <= 4000) return WDTO_4S;
  return WDTO_8S;
}

unsigned long getWdtPeriodMs(uint8_t code) {
  switch (code) {
    case WDTO_15MS:  return 16;
    case WDTO_30MS:  return 32;
    case WDTO_60MS:  return 64;
    case WDTO_120MS: return 125;
    case WDTO_250MS: return 250;
    case WDTO_500MS: return 500;
    case WDTO_1S:    return 1000;
    case WDTO_2S:    return 2000;
    case WDTO_4S:    return 4000;
    default:         return 8000;
  }
}

void sleepWatchdogOnce(unsigned long targetMs) {
  uint8_t code = getWdtPeriodCode(targetMs);
  unsigned long periodMs = getWdtPeriodMs(code);

  watchdogFired = false;
  MCUSR &= ~_BV(WDRF);
  WDTCSR = _BV(WDCE) | _BV(WDE);
  WDTCSR = _BV(WDIE) | code;

  set_sleep_mode(SLEEP_MODE_PWR_DOWN);
  noInterrupts();
  sleep_enable();
#if defined(BODS) && defined(BODSE)
  sleep_bod_disable();
#endif
  interrupts();
  sleep_cpu();
  sleep_disable();

  wdt_disable();
  sleepCompensationMs += periodMs;
}

// ======================== FUNCTIONS ========================

/** Baca sensor dengan oversampling (4x rata-rata + buang pertama) */
int bacaSensorPin(int pin) {
  analogRead(pin);
  long total = 0;
  for (int i = 0; i < JUMLAH_SAMPLE; i++) {
    total += analogRead(pin);
    delayMicroseconds(200);
  }
  return (int)(total / JUMLAH_SAMPLE);
}

/** Baca kedua LDR — real atau simulasi */
void bacaSemuaSensor(unsigned long now) {
  if (simMode && DEBUG_MODE) {
    valL = simValL;
    valR = simValR;
  } else {
    valL = bacaSensorPin(PIN_LDR_L);
    valR = bacaSensorPin(PIN_LDR_R);
  }
  selisih  = abs(valL - valR);
  rataRata = (valL + valR) / 2;
  lastSensorReadTime = now;
}

void ensureFreshSensorData(unsigned long now, unsigned long maxAge = SENSOR_CACHE_MAX_AGE) {
  if (lastSensorReadTime == 0 || (now - lastSensorReadTime) >= maxAge) {
    bacaSemuaSensor(now);
  }
}

/** Attach servo jika belum */
void servoOn() {
  if (!servoAttached) {
    tracker.attach(PIN_SERVO);
    servoAttached = true;
    delay(20);
    tracker.write(pos);
  }
}

/** Detach servo dengan safety delay */
void servoOff() {
  if (servoAttached) {
    tracker.write(pos);
    delay(25);
    tracker.detach();
    servoAttached = false;
  }
}

/** Ambil status output pin 11 berdasarkan mode aktif */
bool getPin11OutputLevel(State s) {
  if (pin11Mode == PIN11_FORCE_OFF) return false;
  if (pin11Mode == PIN11_FORCE_ON) return true;
  return (s != STATE_SLEEP);
}

/** Reset counter terang/gelap saat transisi state */
void resetSleepCounters() {
  brightConfirmCount = 0;
  darkConfirmCount   = 0;
}

/** Update counter untuk bangun dari state tidur */
bool updateWakeCounter() {
  if (rataRata < BATAS_BANGUN) {
    if (brightConfirmCount < WAKE_CONFIRM_TARGET) brightConfirmCount++;
  } else {
    brightConfirmCount = 0;
  }
  return brightConfirmCount >= WAKE_CONFIRM_TARGET;
}

/** Update counter untuk masuk ke state tidur */
bool updateDarkCounter() {
  if (rataRata > BATAS_TIDUR) {
    if (darkConfirmCount < DARK_CONFIRM_TARGET) darkConfirmCount++;
  } else {
    darkConfirmCount = 0;
  }
  return darkConfirmCount >= DARK_CONFIRM_TARGET;
}

/** Terapkan status pin 11 */
void updateOutputPinForState(State s) {
  digitalWrite(PIN_OUTPUT_11, getPin11OutputLevel(s) ? HIGH : LOW);
}

/** Ganti state dan catat waktu masuk */
void gantiState(State newState) {
  bool crossingSleepBoundary = (currentState == STATE_SLEEP || newState == STATE_SLEEP);
  prevState      = currentState;
  currentState   = newState;
  stateEntryTime = trackedMillis();
  lastCheckTime  = stateEntryTime;
  if (crossingSleepBoundary) {
    resetSleepCounters();
  }
  huntStableCount = 0;
  huntLimitCount  = 0;
  updateOutputPinForState(currentState);

  // Print transisi (human readable)
  Serial.print(F("LOG:["));
  printStateName(prevState);
  Serial.print(F("->"));
  printStateName(newState);
  Serial.println(F("]"));
}

/** Print nama state ke serial */
void printStateName(State s) {
  switch (s) {
    case STATE_SLEEP:   Serial.print(F("SLEEP"));   break;
    case STATE_IDLE:    Serial.print(F("IDLE"));     break;
    case STATE_VERIFY:  Serial.print(F("VERIFY"));   break;
    case STATE_HUNTING: Serial.print(F("HUNTING"));  break;
  }
}

/** Print sensor data ke serial (human readable) */
void printSensorData() {
  Serial.print(F("L="));  Serial.print(valL);
  Serial.print(F(" R=")); Serial.print(valR);
  Serial.print(F(" Sel=")); Serial.print(selisih);
  Serial.print(F(" Avg=")); Serial.print(rataRata);
  Serial.print(F(" Pos=")); Serial.print(pos);
  Serial.print(F(" Dark=")); Serial.print(darkConfirmCount);
  Serial.print(F("/")); Serial.print(DARK_CONFIRM_TARGET);
  Serial.print(F(" Bright=")); Serial.print(brightConfirmCount);
  Serial.print(F("/")); Serial.print(WAKE_CONFIRM_TARGET);
}

// ======================== SERIAL PROTOCOL ========================

/** Kirim data terstruktur ke Python GUI */
void sendData() {
  unsigned long now = trackedMillis();
  // Format: DATA:<state>,<valL>,<valR>,<selisih>,<rataRata>,<pos>,<attached>,<verifyCount>,<simMode>,<pin11State>,<pin11Mode>,<darkConfirmCount>,<brightConfirmCount>,<millis>,<runMode>,<sensorAgeMs>,<sleepKind>
  Serial.print(F("DATA:"));
  Serial.print(currentState);
  Serial.print(','); Serial.print(valL);
  Serial.print(','); Serial.print(valR);
  Serial.print(','); Serial.print(selisih);
  Serial.print(','); Serial.print(rataRata);
  Serial.print(','); Serial.print(pos);
  Serial.print(','); Serial.print(servoAttached ? 1 : 0);
  Serial.print(','); Serial.print(verifyCount);
  Serial.print(','); Serial.print(simMode ? 1 : 0);
  Serial.print(','); Serial.print(getPin11OutputLevel(currentState) ? 1 : 0);
  Serial.print(','); Serial.print(pin11Mode);
  Serial.print(','); Serial.print(darkConfirmCount);
  Serial.print(','); Serial.print(brightConfirmCount);
  Serial.print(','); Serial.print(now);
  Serial.print(','); Serial.print(runMode);
  Serial.print(','); Serial.print(getSensorAgeMs(now));
  Serial.print(','); Serial.println(getSleepKindFlag());
}

/** Parse dan eksekusi command dari Python */
void processCommand(const char* cmd) {
  if (strlen(cmd) == 0) return;

  // CMD:SIM_ON
  if (strcmp(cmd, "CMD:SIM_ON") == 0) {
    markHostActive(false);
    simMode = true;
    Serial.println(F("LOG:SIM MODE ON"));
  }
  // CMD:SIM_OFF
  else if (strcmp(cmd, "CMD:SIM_OFF") == 0) {
    markHostActive(false);
    simMode = false;
    Serial.println(F("LOG:SIM MODE OFF"));
  }
  // CMD:SIM_L:<value>
  else if (strncmp(cmd, "CMD:SIM_L:", 10) == 0) {
    markHostActive(false);
    simValL = constrain(atoi(cmd + 10), 0, 1023);
  }
  // CMD:SIM_R:<value>
  else if (strncmp(cmd, "CMD:SIM_R:", 10) == 0) {
    markHostActive(false);
    simValR = constrain(atoi(cmd + 10), 0, 1023);
  }
  // CMD:STATE:<n>
  else if (strncmp(cmd, "CMD:STATE:", 10) == 0) {
    markHostActive(false);
    int s = atoi(cmd + 10);
    if (s >= 0 && s <= 3) {
      if (s == STATE_HUNTING) {
        servoOn();
      } else {
        servoOff();
      }
      gantiState((State)s);
      Serial.print(F("LOG:FORCED STATE "));
      Serial.println(s);
    }
  }
  // CMD:SERVO:<angle>
  else if (strncmp(cmd, "CMD:SERVO:", 10) == 0) {
    markHostActive(false);
    int angle = constrain(atoi(cmd + 10), BATAS_MIN, BATAS_MAX);
    pos = angle;
    servoOn();
    tracker.write(pos);
    Serial.print(F("LOG:SERVO SET "));
    Serial.println(pos);
  }
  // CMD:ATTACH
  else if (strcmp(cmd, "CMD:ATTACH") == 0) {
    markHostActive(false);
    servoOn();
    Serial.println(F("LOG:SERVO ATTACHED"));
  }
  // CMD:DETACH
  else if (strcmp(cmd, "CMD:DETACH") == 0) {
    markHostActive(false);
    if (servoAttached) {
      servoOff();
      Serial.println(F("LOG:SERVO DETACHED"));
    }
  }
  // CMD:PING
  else if (strcmp(cmd, "CMD:PING") == 0) {
    markHostActive(false);
    Serial.println(F("PONG"));
  }
  // CMD:PRES_ON
  else if (strcmp(cmd, "CMD:PRES_ON") == 0) {
    markHostActive(true);
    lastCheckTime = 0;  // Force immediate check
    Serial.println(F("LOG:PRESENTATION SPEED ON"));
  }
  // CMD:PRES_OFF
  else if (strcmp(cmd, "CMD:PRES_OFF") == 0) {
    setGuiMode();
    Serial.println(F("LOG:PRESENTATION SPEED OFF"));
  }
  // CMD:PIN11:AUTO
  else if (strcmp(cmd, "CMD:PIN11:AUTO") == 0) {
    markHostActive(false);
    pin11Mode = PIN11_AUTO;
    updateOutputPinForState(currentState);
    Serial.println(F("LOG:PIN 11 AUTO"));
  }
  // CMD:PIN11:ON
  else if (strcmp(cmd, "CMD:PIN11:ON") == 0) {
    markHostActive(false);
    pin11Mode = PIN11_FORCE_ON;
    updateOutputPinForState(currentState);
    Serial.println(F("LOG:PIN 11 FORCED ON"));
  }
  // CMD:PIN11:OFF
  else if (strcmp(cmd, "CMD:PIN11:OFF") == 0) {
    markHostActive(false);
    pin11Mode = PIN11_FORCE_OFF;
    updateOutputPinForState(currentState);
    Serial.println(F("LOG:PIN 11 FORCED OFF"));
  }
}

/** Baca serial input non-blocking */
void readSerialCommands() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdIndex > 0) {
        cmdBuffer[cmdIndex] = '\0';
        processCommand(cmdBuffer);
        cmdIndex = 0;
      }
    } else {
      if (cmdIndex < 30) {
        cmdBuffer[cmdIndex++] = c;
      }
    }
  }
}

// ======================== STATE HANDLERS ========================

void handleSleep(unsigned long now) {
  unsigned long interval = getInterval(SLEEP_INTERVAL_PROD, SLEEP_INTERVAL_GUI, SLEEP_INTERVAL_PRES);
  if (now - lastCheckTime >= interval) {
    bacaSemuaSensor(now);
    bool shouldWake = updateWakeCounter();

    if (shouldEmitSleepLog()) {
      Serial.print(F("LOG:[SLEEP] "));
      printSensorData();
      Serial.println(F(" | Zzz..."));
    }

    if (shouldWake) {
      Serial.println(F("LOG:  >> Cahaya stabil terdeteksi!"));
      gantiState(STATE_IDLE);
    }
    lastCheckTime = now;
  }
}

void handleIdle(unsigned long now) {
  unsigned long interval = getInterval(IDLE_INTERVAL_PROD, IDLE_INTERVAL_GUI, IDLE_INTERVAL_PRES);
  if (now - lastCheckTime >= interval) {
    bacaSemuaSensor(now);
    bool shouldSleep = updateDarkCounter();

    if (shouldEmitVerboseLoopLogs()) {
      Serial.print(F("LOG:[IDLE] "));
      printSensorData();
      Serial.println(F(" | OK"));
    }

    if (shouldSleep) {
      Serial.println(F("LOG:  >> Gelap stabil, masuk tidur..."));
      servoOff();
      gantiState(STATE_SLEEP);
    }
    else if (selisih > TOLERANSI) {
      if (shouldEmitVerboseLoopLogs()) {
        Serial.print(F("LOG:  >> Selisih besar! "));
        printSensorData();
        Serial.println();
      }
      verifyCount = 0;
      gantiState(STATE_VERIFY);
      servoOn();
    }
    else {
      servoOff();
    }
    lastCheckTime = now;
  }
}

void handleVerify(unsigned long now) {
  unsigned long interval = getInterval(VERIFY_INTERVAL_PROD, VERIFY_INTERVAL_GUI, VERIFY_INTERVAL_PRES);
  if (now - lastCheckTime >= interval) {
    bacaSemuaSensor(now);

    if (updateDarkCounter()) {
      Serial.println(F("LOG:[VERIFY] Gelap stabil -> SLEEP"));
      servoOff();
      gantiState(STATE_SLEEP);
    }
    else if (selisih > TOLERANSI) {
      verifyCount++;
      if (shouldEmitVerboseLoopLogs()) {
        Serial.print(F("LOG:[VERIFY "));
        Serial.print(verifyCount);
        Serial.print(F("/"));
        Serial.print(VERIFY_COUNT_TARGET);
        Serial.print(F("] "));
        printSensorData();
        Serial.println(F(" check"));
      }

      if (verifyCount >= VERIFY_COUNT_TARGET) {
        Serial.println(F("LOG:  >> CONFIRMED! Servo ON -> HUNTING"));
        servoOn();
        huntingStartTime = now;
        gantiState(STATE_HUNTING);
      }
    }
    else {
      if (shouldEmitVerboseLoopLogs()) {
        Serial.print(F("LOG:[VERIFY] False alarm. Sel="));
        Serial.print(selisih);
        Serial.println(F(" < tol, back to IDLE"));
      }
      servoOff();
      gantiState(STATE_IDLE);
    }
    lastCheckTime = now;
  }
}

void handleHunting(unsigned long now) {
  unsigned long interval = getInterval(HUNTING_INTERVAL_PROD, HUNTING_INTERVAL_GUI, HUNTING_INTERVAL_PRES);
  if (now - lastCheckTime >= interval) {
    bacaSemuaSensor(now);

    // --- Safety: Timeout ---
    if (now - huntingStartTime >= HUNTING_TIMEOUT) {
      Serial.println(F("LOG:[HUNTING] TIMEOUT 30s!"));
      servoOff();
      gantiState(STATE_IDLE);
      lastCheckTime = now;
      return;
    }

    // --- Safety: Mendadak gelap ---
    if (updateDarkCounter()) {
      Serial.println(F("LOG:[HUNTING] Gelap stabil! -> SLEEP"));
      servoOff();
      gantiState(STATE_SLEEP);
      lastCheckTime = now;
      return;
    }

    // --- Gerakin servo ---
    if (selisih > TOLERANSI) {
      huntStableCount = 0;
      int prevPos = pos;
      int nextPos = pos;

      if (valL > valR) {
        nextPos = IS_FLIPPED ? (pos + 1) : (pos - 1);
      } else {
        nextPos = IS_FLIPPED ? (pos - 1) : (pos + 1);
      }

      nextPos = constrain(nextPos, BATAS_MIN, BATAS_MAX);

      if (nextPos == prevPos) {
        if (huntLimitCount < HUNT_LIMIT_TARGET) huntLimitCount++;
        if (shouldEmitVerboseLoopLogs()) {
          Serial.print(F("LOG:[HUNTING] Servo mentok di "));
          Serial.print(prevPos);
          Serial.print(F(" | limit hold "));
          Serial.print(huntLimitCount);
          Serial.print(F("/"));
          Serial.println(HUNT_LIMIT_TARGET);
        }

        if (huntLimitCount >= HUNT_LIMIT_TARGET) {
          Serial.println(F("LOG:[HUNTING] Limit reached -> IDLE"));
          servoOff();
          gantiState(STATE_IDLE);
          lastCheckTime = now;
          return;
        }
      } else {
        huntLimitCount = 0;
        pos = nextPos;
        tracker.write(pos);
      }
    }
    else if (selisih <= TOLERANSI_STOP) {
      huntLimitCount = 0;
      if (huntStableCount < HUNT_STABLE_TARGET) huntStableCount++;
      if (huntStableCount >= HUNT_STABLE_TARGET) {
        Serial.print(F("LOG:[HUNTING] Target acquired! "));
        printSensorData();
        Serial.println(F(" -> IDLE"));
        servoOff();
        gantiState(STATE_IDLE);
      } else if (shouldEmitVerboseLoopLogs()) {
        Serial.print(F("LOG:[HUNTING] Menunggu stabil "));
        Serial.print(huntStableCount);
        Serial.print(F("/"));
        Serial.println(HUNT_STABLE_TARGET);
      }
    } else {
      huntStableCount = 0;
      huntLimitCount = 0;
    }
    lastCheckTime = now;
  }
}

// ======================== SETUP & LOOP ========================

void setup() {
  Serial.begin(DEBUG_MODE ? 115200 : 9600);
  pinMode(PIN_OUTPUT_11, OUTPUT);
  updateOutputPinForState(STATE_IDLE);

  // Set servo ke posisi awal
  tracker.attach(PIN_SERVO);
  tracker.write(pos);
  delay(500);
  servoAttached = true;
  servoOff();

  // AHAHAHAHAHAHAHA
  Serial.println(F("\n================================================================"));
  Serial.println(F("  Credits: Rehan Christian and Melcior Zonggonau"));
  Serial.println(F("  SOLAR TRACKER v2.0 — FSM EDITION | WACANA"));
  Serial.println(F("================================================================"));
  Serial.print(F(">>> Runtime default: "));
  Serial.print(getRunModeName(runMode));
  Serial.println(F(" | Host heartbeat controls GUI mode <<<"));
  Serial.print(F("> CFG: Flip="));
  Serial.print(IS_FLIPPED ? F("ON") : F("OFF"));
  Serial.print(F(" | Range="));
  Serial.print(BATAS_MIN);
  Serial.print(F("-"));
  Serial.print(BATAS_MAX);
  Serial.print(F(" | Tol="));
  Serial.print(TOLERANSI);
  Serial.print(F(" | StopTol="));
  Serial.print(TOLERANSI_STOP);
  Serial.print(F(" | Wake<"));
  Serial.print(BATAS_BANGUN);
  Serial.print(F(" | Sleep>"));
  Serial.println(BATAS_TIDUR);
  Serial.print(F("> Power: Servo detach when idle | Sleep mode="));
  Serial.println(F("TRUE AVR WDT"));
  Serial.println(F("================================================================\n"));

  unsigned long now = trackedMillis();
  bacaSemuaSensor(now);
  currentState   = STATE_IDLE;
  prevState      = STATE_IDLE;
  runMode        = RUN_STANDALONE;
  stateEntryTime = now;
  lastCheckTime  = (now >= IDLE_INTERVAL_PROD) ? (now - IDLE_INTERVAL_PROD) : 0;
  lastDataSendTime = now;
}

void loop() {
  unsigned long now = trackedMillis();

  // --- Baca command dari Python (non-blocking) ---
  if (DEBUG_MODE) {
    readSerialCommands();
  }
  updateRuntimeMode(now);

  if (useTrueSleep()) {
    unsigned long interval = getCurrentStateInterval();
    unsigned long elapsed = now - lastCheckTime;
    if (elapsed < interval) {
      unsigned long remaining = interval - elapsed;
      servoOff();
      sleepWatchdogOnce(remaining);
      return;
    }
  }

  // --- State Machine ---
  switch (currentState) {
    case STATE_SLEEP:   handleSleep(now);   break;
    case STATE_IDLE:    handleIdle(now);     break;
    case STATE_VERIFY:  handleVerify(now);   break;
    case STATE_HUNTING: handleHunting(now);  break;
  }

  // --- Kirim data ke host tanpa baca sensor ganda ---
  if (DEBUG_MODE && telemetryEnabled() && (now - lastDataSendTime >= DATA_SEND_INTERVAL)) {
    ensureFreshSensorData(now);
    sendData();
    lastDataSendTime = now;
  }
}
