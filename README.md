# ☀️ Advanced Solar Tracker v2.0 — FSM Edition

> An Arduino-based solar tracker that actually thinks before it moves, built as a high school physics project by **Kolese Le Cocq d'Armandville Students**.

---

## What is this?

It's a solar panel tracker that uses two LDR sensors to figure out where the light is coming from, then nudges a servo motor until the panel is pointing right at it. Simple idea, but the implementation goes a bit deeper than a basic `if left > right, turn left` loop.

The firmware runs on a **Finite State Machine** with four distinct states, so the tracker isn't just blindly reacting to every sensor blip, it sleeps when it's dark, double-checks before it moves, and knows when to give up and go back to idle. There's also a **Python GUI** you can connect over Serial to watch everything in real time, simulate sensor values, and control the hardware without touching the code.

---

## Features

### Firmware
- **4-state FSM** - `SLEEP`, `IDLE`, `VERIFY`, `HUNTING`. The tracker goes through these in a logical order instead of just reacting to raw sensor data
- **AVR Watchdog sleep** - when it's dark and there's nothing to track, the Arduino actually puts itself to sleep at the hardware level to save power
- **Oversampling** - each LDR is read 4 times per cycle and averaged, so a single noisy reading won't cause a false trigger
- **Confirmation counters** - state transitions only happen after several consistent readings in a row. Shadows, clouds, quick flickers — none of that should cause the panel to start chasing nothing
- **3 runtime modes** - `STANDALONE` (no PC needed), `GUI` (connected to the debugger), and `PRESENTATION` (faster timing for demos)
- **Servo auto-detach** - when the tracker isn't actively moving, the servo gets detached so it's not drawing current or fighting against itself
- **30-second hunt timeout** - if the tracker can't lock on within 30 seconds, it gives up and goes back to idle instead of spinning forever
- **Pin 11 output** - follows the current state automatically, but can be manually overridden from the GUI if needed
- **Full Serial command interface** - you can change almost anything at runtime without reflashing

### Python Debugger
- Live **servo position gauge** and **LDR bar graphs** so you can actually see what the hardware is doing
- **LDR simulation sliders** - feed fake sensor values to the Arduino without physically covering the sensors. Really useful for testing edge cases
- **Force-state buttons** - jump to any FSM state directly, great for demos
- **Manual servo control** with attach/detach buttons
- **Pin 11 override** toggle (AUTO / FORCE ON / FORCE OFF)
- **Presentation Window** - a separate fullscreen display with a nice animated UI, sky gradient, smooth servo needle animation. Press `F11` to go fullscreen
- **Heartbeat system** - if the GUI closes or crashes, the Arduino automatically falls back to standalone mode after a few seconds
- **Xbox controller support** *(optional, needs `pygame`)* left stick moves the servo, A button toggles Pin 11. Mostly for fun during demos
- ANSI-colored console output on terminals that support it

---

## Hardware

| Component | Pin |
|---|---|
| Servo Motor | D9 |
| LDR Left | A0 |
| LDR Right | A1 |
| Output Indicator | D11 |

### Wiring

```
         ┌─────────────┐
LDR_L ───┤ A0          │
LDR_R ───┤ A1          ├──── Servo PWM → D9
         │  Arduino    ├──── Output LED/Relay → D11
         │  Uno/Nano   │
         └─────────────┘
```

> The two LDRs need a small physical divider between them a piece of cardboard or foam works fine. Without it, both sensors see the same light level and the difference is always near zero.

---

## Configuration

All the tunable values are grouped at the top of `SENSORCAHAYA_V2.ino` so you don't have to dig through the code:

```cpp
// Servo range - adjust these if your servo is mounted at a different angle
const bool  IS_FLIPPED  = true;
const int   BATAS_MIN   = 82;    // min angle
const int   BATAS_MAX   = 170;   // max angle

// Light thresholds (0-1023, lower value = brighter light)
const int   BATAS_BANGUN = 650;  // wake up when average brightness is below this
const int   BATAS_TIDUR  = 850;  // go to sleep when average brightness is above this

// How sensitive the tracker is
const int   TOLERANSI      = 20; // minimum LDR difference before the tracker starts moving
const int   TOLERANSI_STOP = 20; // LDR difference at which the tracker considers itself locked on

// How many samples to average per reading
const int   JUMLAH_SAMPLE = 4;
```

There are also three timing profiles already written in the file `PROD` for real deployment (slower, more power-efficient), `DEMO` for open house events (faster), and `PRESENTASI`. You just swap which block of constants is commented out.

---

## How the State Machine Works

```
           [dark, confirmed]
  SLEEP ◄──────────────────── IDLE
    │                          ▲  │
    │  [bright, confirmed]     │  │ [LDR diff > tolerance]
    └──────────────────────────┘  ▼
                               VERIFY
                                  │
                                  │ [confirmed 3×]
                                  ▼
                               HUNTING ──► [aligned / timeout / dark] ──► IDLE
```

| State | What's happening |
|---|---|
| `SLEEP` | It's dark. The Arduino is in hardware sleep mode and only wakes up occasionally to check if the light came back. |
| `IDLE` | Light is detected, panel is in position. Servo is detached. The tracker is watching for any light imbalance. |
| `VERIFY` | One LDR is brighter than the other. Before moving anything, the tracker waits for 3 consecutive confirmations to make sure it's not just a flicker. |
| `HUNTING` | Actively rotating the servo toward the brighter sensor, one degree at a time, until both LDRs read roughly the same value. |

---

## Getting Started

### Flashing the Arduino

You'll need Arduino IDE 1.8+ (or Arduino CLI). The only library used is `Servo.h` which comes built-in.

1. Open `SENSORCAHAYA_V2.ino`
2. Tweak the config constants at the top for your hardware (servo range, light thresholds)
3. Upload to your Arduino Uno or Nano

### Running the Python Debugger

```bash
pip install pyserial
pip install pygame   # optional, only if you want Xbox controller support
```

```bash
python debugger.py
```

Pick your COM port from the dropdown and hit Connect. The Arduino will automatically switch to GUI mode once it detects the heartbeat signal.

---

## Serial Protocol

The Arduino talks at `115200` baud. All log lines from the firmware start with `LOG:` so they're easy to filter out if you're building your own tooling.

### Commands you can send (PC → Arduino)

| Command | What it does |
|---|---|
| `CMD:PING` | Heartbeat — keeps the Arduino in GUI mode |
| `CMD:PRES_ON` / `CMD:PRES_OFF` | Switch to/from presentation run mode |
| `CMD:SIM_ON` / `CMD:SIM_OFF` | Enable/disable LDR simulation |
| `CMD:SIM_L:<0-1023>` | Set the simulated left LDR value |
| `CMD:SIM_R:<0-1023>` | Set the simulated right LDR value |
| `CMD:STATE:<0-3>` | Force jump to a specific FSM state |
| `CMD:SERVO:<angle>` | Move servo to a specific angle |
| `CMD:ATTACH` / `CMD:DETACH` | Attach or detach the servo |
| `CMD:PIN11:AUTO` / `ON` / `OFF` | Change Pin 11 output mode |

### Telemetry (Arduino → PC)

When in GUI or Presentation mode, the Arduino sends a `DATA:` packet every 100ms:

```
DATA:<state>,<pos>,<valL>,<valR>,<selisih>,<rataRata>,<millis>,<verifyCount>,<darkCount>,<brightCount>,<attached>,<simMode>,<pin11Mode>,<pin11State>,<runMode>,<sleepKind>,<sensorAgeMs>
```

---

## File Structure

```
solar-tracker/
├── SolarTracker_V2.ino   # Arduino firmware
└── debugger.py           # Python GUI + presentation tool
```

---

### Donate me!
[![Donasi Lewat Saweria](https://img.shields.io/badge/Saweria-Donate-orange?logo=ko-fi&logoColor=white)](https://saweria.co/Rehan30g)
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/paypalme/rehan30g)

## License

Built for a high school physics class. Do whatever you want with it.
