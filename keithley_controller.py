"""
keithley_controller.py
======================
Instrument control module for Keithley 2450 SourceMeter.
Handles VISA communication, SCPI commands, and voltage sweeping.

Device: Keithley 2450 SourceMeter
DUT:    TiN–HZO–TiN memristor (capacitor stack)
"""

import time
import numpy as np
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)

# ── Try to import pyvisa; fall back to a simulator for offline testing ──────
try:
    import pyvisa
    PYVISA_AVAILABLE = True
except ImportError:
    PYVISA_AVAILABLE = False
    logger.warning("PyVISA not found. Running in SIMULATION mode.")


# ═══════════════════════════════════════════════════════════════════════════════
class SimulatedKeithley:
    """
    Offline simulator that mimics a real Keithley 2450 response.
    Generates realistic memristive I–V with hysteresis so the UI
    can be developed / demonstrated without hardware.
    """

    def __init__(self):
        self._voltage = 0.0
        self._compliance = 0.01          # A
        self._state = "HRS"              # High-Resistance State initially
        self._lrs_resistance = 1e3       # ~1 kΩ  (LRS)
        self._hrs_resistance = 1e5       # ~100 kΩ (HRS)
        self._vset  =  2.5 + np.random.uniform(-0.3, 0.3)
        self._vreset = -1.8 + np.random.uniform(-0.3, 0.3)
        logger.info("SimulatedKeithley initialised (no hardware required).")

    # --- private helpers ---------------------------------------------------
    def _simulate_current(self, voltage: float) -> float:
        """Memristive I–V with stochastic noise."""
        if self._state == "LRS":
            R = self._lrs_resistance
        else:
            R = self._hrs_resistance

        # Switching transitions
        if self._state == "HRS" and voltage >= self._vset:
            self._state = "LRS"
            self._vset = 2.5 + np.random.uniform(-0.4, 0.4)

        if self._state == "LRS" and voltage <= self._vreset:
            self._state = "HRS"
            self._vreset = -1.8 + np.random.uniform(-0.3, 0.3)

        current = voltage / R
        # Add ~1 % noise
        current += current * np.random.normal(0, 0.01)
        # Compliance clamp
        current = np.clip(current, -self._compliance, self._compliance)
        return float(current)

    # --- public interface (mirrors Keithley2450 API) -----------------------
    def configure(self, compliance: float, nplc: float):
        self._compliance = compliance
        logger.debug(f"Sim configure: compliance={compliance} A, nplc={nplc}")

    def set_voltage(self, voltage: float):
        self._voltage = voltage

    def measure_current(self) -> float:
        return self._simulate_current(self._voltage)

    def output_on(self):
        logger.debug("Sim output ON")

    def output_off(self):
        self._voltage = 0.0
        logger.debug("Sim output OFF")

    def close(self):
        logger.debug("Sim connection closed")

    @property
    def resource_name(self):
        return "SIMULATED::Keithley2450"


# ═══════════════════════════════════════════════════════════════════════════════
class Keithley2450:
    """
    Real Keithley 2450 SCPI driver via PyVISA.

    Typical VISA resource string (USB):
        USB0::0x05E6::0x2450::XXXXXXX::INSTR

    Usage
    -----
    k = Keithley2450("USB0::0x05E6::0x2450::04572561::INSTR")
    k.configure(compliance=1e-3, nplc=0.1)
    k.output_on()
    k.set_voltage(1.0)
    i = k.measure_current()
    k.output_off()
    k.close()
    """

    def __init__(self, resource_name: str, timeout_ms: int = 25_000):
        if not PYVISA_AVAILABLE:
            raise RuntimeError("PyVISA is not installed. Run: pip install pyvisa pyvisa-py")

        # Force the use of the Python-only network backend
        rm = pyvisa.ResourceManager()
        self._inst = rm.open_resource(resource_name)
        self._inst.timeout = timeout_ms
        self._inst.chunk_size = 102400 
        self._resource_name = resource_name

        # Reset and clear
        self._write("*RST")
        self._write("*CLS")
        logger.info(f"Connected to: {self._query('*IDN?')}")

    # --- low-level helpers --------------------------------------------------
    def _write(self, cmd: str):
        logger.debug(f"WRITE: {cmd}")
        self._inst.write(cmd)

    def _query(self, cmd: str) -> str:
        logger.debug(f"QUERY: {cmd}")
        return self._inst.query(cmd).strip()

    # --- public interface ---------------------------------------------------
    def configure(self, compliance: float = 1e-3, nplc: float = 0.1):
        """
        Set up the instrument for a voltage-sourcing / current-measuring run.

        Parameters
        ----------
        compliance : float
            Current compliance limit in Amperes.
        nplc : float
            Number of power-line cycles per measurement.
            Lower = faster (0.01 minimum, 10 maximum).
            0.1 is a good balance of speed and noise for memristors.
        """
        cmds = [
           "*RST",
           ":ROUT:TERM FRON",                                   # Reset the device to a clean state
           ":SOUR:FUNC VOLT",
           ":SOUR:VOLT:RANG 20",       # Set a fixed range (e.g., 20V) instead of AUTO
           f":SOUR:VOLT:ILIM {compliance:.3e}",     # Set compliance
           ":SENS:FUNC 'CURR'",
           ":SENS:CURR:RANG:AUTO ON",
           f":SENS:CURR:NPLC {nplc}",
           ":OUTP OFF",                # Ensure output is off during config
        ]
        for cmd in cmds:
            self._write(cmd)
        logger.info(f"Configured: compliance={compliance} A, NPLC={nplc}")

    def output_on(self):
        self._write(":OUTP ON")
        logger.debug("Output ON")

    def output_off(self):
        self._write(":SOUR:VOLT 0")
        time.sleep(0.05)
        self._write(":OUTP OFF")
        logger.debug("Output OFF")

    def set_voltage(self, voltage: float):
        self._write(f":SOUR:VOLT {voltage:.6g}")

    def measure_current(self) -> float:
        """Trigger a reading and handle potential buffer sync issues."""
        raw = self._query(":READ?").strip()
        logger.info(f"RAW READ RESPONSE: {raw}")
        print(f"RAW: {raw}")

        # If the response contains text instead of numbers, clear and try once more
        if "KEITHLEY" in raw or "MODEL" in raw:
            logger.warning("Detected sync error (IDN in buffer). Clearing...")
            self._inst.clear() # Clears the VISA buffer
            raw = self._query(":READ?").strip()

        parts = [p.strip() for p in raw.split(",") if p.strip()]

        try:
            if len(parts) >= 2:
               return float(parts[1])
            if len(parts) == 1:
               return float(parts[0])
            raise ValueError("Empty response")
        except ValueError:
            raise RuntimeError(f"Non-numeric data from instrument: {raw!r}")
    def close(self):
        try:
            self.output_off()
        except Exception:
            pass
        self._inst.close()
        logger.info("Instrument connection closed.")

    @property
    def resource_name(self):
        return self._resource_name


# ═══════════════════════════════════════════════════════════════════════════════
class SweepEngine:
    """
    Voltage sweep engine: 0 → +Vmax → 0 → −Vmax → 0

    Designed to be run in a worker thread.
    Calls `on_point(voltage, current)` for every measured point.
    """

    def __init__(
        self,
        instrument,
        vmax: float,
        step: float,
        delay: float,
        compliance: float,
        nplc: float = 0.1,
    ):
        self.instrument  = instrument
        self.vmax        = abs(vmax)
        self.step        = abs(step)
        self.delay       = delay
        self.compliance  = compliance
        self.nplc        = nplc
        self._stop_flag  = False

    # ── control ──────────────────────────────────────────────────────────────
    def stop(self):
        self._stop_flag = True

    def reset_stop(self):
        self._stop_flag = False

    # ── sweep helpers ─────────────────────────────────────────────────────────
    def _build_waveform(self) -> np.ndarray:
        """
        Full bipolar triangle: 0 → +Vmax → 0 → −Vmax → 0
        Using linspace so endpoints are exact (avoids floating-point creep).
        """
        n_half = max(2, int(round(self.vmax / self.step)))
        up   = np.linspace(0,          self.vmax,  n_half + 1)
        down = np.linspace(self.vmax,  0,           n_half + 1)[1:]
        neg  = np.linspace(0,         -self.vmax,  n_half + 1)[1:]
        back = np.linspace(-self.vmax, 0,           n_half + 1)[1:]
        return np.concatenate([up, down, neg, back])

    # ── public ────────────────────────────────────────────────────────────────
    
    def run_cycle(
        self,
        on_point: Callable[[float, float], None],
        on_done:  Optional[Callable[[], None]] = None,
):
        waveform = self._build_waveform()
        inst     = self.instrument

        inst.configure(self.compliance, self.nplc)
        inst.set_voltage(0.0)
        inst.output_on()

        try:
            for v in waveform:
                if self._stop_flag:
                   logger.info("Sweep stopped by user.")
                   break

                inst.set_voltage(float(v))
                if self.delay > 0:
                   time.sleep(self.delay)

                i = inst.measure_current()
                on_point(float(v), float(i))

        finally:
            inst.set_voltage(0.0)
            time.sleep(0.05)
            inst.output_off()

            if on_done:
               on_done()




        