# Memristor Tester — Keithley 2450

A desktop application for automated I–V characterization and endurance cycling of memristive devices (TiN–HZO–TiN and similar oxide-based stacks) using a Keithley 2450 SourceMeter.

---

## Features

- **Live I–V sweep** — bipolar triangle waveform (0 → +Vmax → 0 → −Vmax → 0) with real-time plotting
- **R vs V plot** — resistance curve plotted alongside the I–V characteristic, log-scale Y axis
- **Automatic parameter extraction** per cycle:
  - Vset (HRS → LRS switching voltage)
  - Vreset (LRS → HRS switching voltage)
  - Ron (Low-Resistance State)
  - Roff (High-Resistance State)
  - On/Off ratio
- **Endurance cycling** — run up to 100,000 cycles with Ron, Roff, Vset, Vreset tracked over time
- **CSV export** — raw I–V data and extracted parameters saved automatically after every cycle
- **PNG export** — save all plots as images in one click
- **Simulation mode** — full offline demo with realistic memristive hysteresis, no hardware needed
- **Dark theme UI** built with PyQt5 + pyqtgraph

---

## Project Structure

```
memristor-tester/
│
├── gui.py                  # Main application window, PyQt5 UI, live plotting
├── keithley_controller.py  # Keithley 2450 SCPI driver, sweep engine, simulator
├── data_analysis.py        # Parameter extraction (Vset, Vreset, Ron, Roff)
│
├── data/                   # Auto-created on first run
│   ├── iv_data_YYYYMMDD_HHMMSS.csv       # Raw I–V points for all cycles
│   └── parameters_YYYYMMDD_HHMMSS.csv    # Extracted parameters per cycle
│
├── requirements.txt
└── README.md
```

---

## Hardware

| Item | Details |
|---|---|
| Instrument | Keithley 2450 SourceMeter |
| Connection | USB or LAN (TCPIP) via PyVISA |
| Device under test | TiN–HZO–TiN memristor (or any 2-terminal switching device) |
| Terminals used | FORCE HI (red) and FORCE LO (green) — 2-wire measurement |

---

## Installation

### 1. Clone the repository
```bash
git clone https://github.com/YOURUSERNAME/memristor-tester.git
cd memristor-tester
```

### 2. Create a virtual environment (recommended)
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Run the application
```bash
python gui.py
```

---

## Usage

### Simulation mode (no hardware needed)
1. Launch `gui.py`
2. Tick **Simulation mode** checkbox
3. Click **Connect**
4. Enter a **Sample name / structure** (required), e.g. `10nm 10nm 10nm TiN-HZO-TiN`
5. Optionally add a short note such as `device A3, top-left cell`
6. Set parameters (Vmax = 3V, Compliance = 0.001A recommended)
7. Click **START**

You will see a realistic hysteresis loop and all parameters extracted automatically.

### Real hardware measurement
1. Connect the Keithley 2450 via USB or LAN
2. Enter the VISA resource string (e.g. `TCPIP0::10.1.199.9::inst0::INSTR`)
3. Untick Simulation mode
4. Click **Connect**
5. Connect device probes: one probe to **FORCE HI**, other to **FORCE LO**
6. Enter a **Sample name / structure** before the run
7. Optionally enter a note identifying the device/cell
8. Set sweep parameters (see recommended values below)
9. Click **START**

### Recommended sweep parameters for TiN–HZO–TiN

| Parameter | Value | Notes |
|---|---|---|
| Vmax | 3.5 V | Increase to 4.0–4.5 V for forming step |
| Step | 0.02 V | Fine enough to catch switching transition |
| Delay | 0.05 s | Device response time per step |
| Compliance | 0.001 A | 1 mA — standard for HZO, protects device |
| NPLC | 1.0 | Higher = cleaner measurements |
| Cycles | 1 (first run), then 10, then 100+ | Start low, increase gradually |

### Forming (first measurement on fresh device)
Fresh HZO devices require a higher voltage forming step before normal cycling:
1. Set Vmax = `4.5 V`, Compliance = `0.001 A`, Cycles = `1`
2. Run the sweep — watch for a sudden current jump (forming event)
3. After forming, reduce Vmax back to `3.5 V` for normal cycling

### Expected results for a working memristor

| Parameter | Typical range |
|---|---|
| Vset | 2.5 V – 3.5 V |
| Vreset | -1.5 V – -2.5 V |
| Ron | 1 kΩ – 50 kΩ |
| Roff | 100 kΩ – 10 MΩ |
| On/Off ratio | 10 – 1000 |

---

## Output Files

Before every run, the GUI requires a **Sample name / structure**. You can also enter the **Anneal temperature (°C)**; leave it empty if unknown or not annealed. The application creates a folder for that sample and uses the sample plus the main sweep settings in every filename.

Example sample name:
```
10nm 10nm 10nm TiN-HZO-TiN
```

Example output:
```
data/
└── 10nm_10nm_10nm_TiN-HZO-TiN/
    ├── run_info__10nm_10nm_10nm_TiN-HZO-TiN__anneal_450C__N10__sweep_pm3V__step_0p02V__Icomp_1mA__YYYYMMDD_HHMMSS.csv
    ├── iv_data__10nm_10nm_10nm_TiN-HZO-TiN__anneal_450C__N10__sweep_pm3V__step_0p02V__Icomp_1mA__YYYYMMDD_HHMMSS.csv
    └── parameters__10nm_10nm_10nm_TiN-HZO-TiN__anneal_450C__N10__sweep_pm3V__step_0p02V__Icomp_1mA__YYYYMMDD_HHMMSS.csv
```

`pm3V` means the bipolar sweep reaches both **+3 V and -3 V**.

**`run_info__...csv`** stores the exact run setup once:
- sample name, anneal temperature (°C, optional), and optional notes
- sweep pattern (`0 -> +Vmax -> 0 -> -Vmax -> 0`)
- Vmax/Vmin and voltage step
- delay, compliance current, and NPLC
- requested number of cycles, actual cycles saved, and final run status
- simulation/real-instrument setting and VISA resource

**`iv_data__...csv`** contains raw I-V points and includes `sample_name` and `run_id` columns.

**`parameters__...csv`** contains Vset, Vreset, Ron, Roff, and On/Off ratio for each cycle, also with `sample_name` and `run_id` columns.

---

## Architecture

```
gui.py  (UI layer)
  └── SweepWorker (QThread)
        └── SweepEngine  ──→  Keithley2450  (real hardware)
              │           or  SimulatedKeithley  (offline)
              │
              └── on_point() callback
                    └── IVData  ──→  ParameterExtractor  ──→  DataManager
                                          (data_analysis.py)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Cannot connect to instrument | Check VISA resource string. Make sure NI-VISA or pyvisa-py is installed |
| Current reads in pA, no switching | Check compliance is set to 1 mA not 1 µA |
| Flat I-V line, no hysteresis | Device may need forming — increase Vmax to 4.5V for first sweep |
| Vset/Vreset showing `-` | Normal for resistors and unformed devices. Check compliance |
| Data folder not found | Click "Open Data Folder" button — it creates automatically |
| Plots exported as blank | Run at least one complete cycle before exporting |

---

## License

MIT License — free to use, modify, and distribute.
