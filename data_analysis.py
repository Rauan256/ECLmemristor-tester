"""
data_analysis.py
================
Parameter extraction and data-management module.

Computes for each I–V cycle:
  • Vset  – voltage at HRS → LRS switching  (positive branch)
  • Vreset – voltage at LRS → HRS switching (negative branch)
  • Ron   – resistance in Low-Resistance State
  • Roff  – resistance in High-Resistance State
  • On/Off ratio = Roff / Ron
"""

import csv
import os
import numpy as np
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Tuple, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class CycleParameters:
    """Extracted parameters for a single I–V cycle."""
    cycle_index:  int
    timestamp:    str
    vset:         Optional[float] = None   # V  (HRS → LRS)
    vreset:       Optional[float] = None   # V  (LRS → HRS)
    ron:          Optional[float] = None   # Ω
    roff:         Optional[float] = None   # Ω
    on_off_ratio: Optional[float] = None   # dimensionless


# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class IVData:
    """Raw I–V data for a single cycle."""
    cycle_index: int
    timestamp:   str
    voltages:    List[float] = field(default_factory=list)
    currents:    List[float] = field(default_factory=list)

    def add_point(self, v: float, i: float):
        self.voltages.append(v)
        self.currents.append(i)

    # convenience ─────────────────────────────────────────────────────────────
    @property
    def v_arr(self) -> np.ndarray:
        return np.asarray(self.voltages)

    @property
    def i_arr(self) -> np.ndarray:
        return np.asarray(self.currents)

    @property
    def r_arr(self) -> np.ndarray:
        """Element-wise |V/I|, guarding against division-by-zero."""
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(np.abs(self.i_arr) > 1e-15,
                         np.abs(self.v_arr / self.i_arr),
                         np.nan)
        return r


# ═══════════════════════════════════════════════════════════════════════════════
class ParameterExtractor:
    """
    Extracts Vset, Vreset, Ron, Roff from a completed I–V cycle.

    Approach
    --------
    • The sweep waveform is: 0 → +Vmax → 0 → −Vmax → 0
      ──  Positive branch (first quarter):  detect Vset  via current jump
      ──  Negative branch (third quarter):  detect Vreset via current jump
    • Ron  = median |V/I| on the positive branch AFTER the Vset point
    • Roff = median |V/I| on the positive branch BEFORE the Vset point
             (excluding V ≈ 0 to avoid noise amplification)
    """

    def __init__(
        self,
        jump_factor: float  = 3.0,   # current must grow by this factor to count as switching
        v_threshold: float  = 0.2,   # ignore points with |V| < v_threshold (noise floor)
        r_percentile: float = 50.0,  # use median for resistance estimate
    ):
        self.jump_factor   = jump_factor
        self.v_threshold   = v_threshold
        self.r_percentile  = r_percentile

    # ── helpers ──────────────────────────────────────────────────────────────
    def _positive_branch_idx(self, iv: IVData) -> np.ndarray:
        """
        Indices of the 0 → +Vmax ascending half.
        This is where V is increasing from 0 to max.
        """
        v = iv.v_arr
        n = len(v)
        if n == 0:
            return np.array([], dtype=int)
        # Find the index of +Vmax
        peak_idx = int(np.argmax(v))
        return np.arange(0, peak_idx + 1)

    def _negative_branch_idx(self, iv: IVData) -> np.ndarray:
        v = iv.v_arr
        n = len(v)
        if n == 0:
           return np.array([], dtype=int)

        peak_idx = int(np.argmax(v))
        trough_idx = int(np.argmin(v))

        if trough_idx <= peak_idx + 1:
          return np.array([], dtype=int)

        idx = np.arange(peak_idx + 1, trough_idx + 1)
        idx = idx[v[idx] < 0]

        return idx

    def _detect_jump(self, voltages: np.ndarray, currents: np.ndarray) -> Optional[float]:
        """
        FIX 2: Detect switching voltage using a smoothed log-derivative.
        More robust than a single-point ratio check on noisy real data.

        Works in log space — switching appears as a sharp sustained positive
        slope. A 3-point moving average smooths out single-point noise spikes
        before the threshold check runs.
        """
        abs_i = np.abs(currents)
        n = len(abs_i)
        if n < 4:
            return None

        # Work in log space — switching appears as a sharp positive slope
        safe_i = np.where(abs_i > 1e-14, abs_i, 1e-14)
        log_i  = np.log10(safe_i)

        # Smooth with a 3-point moving average to suppress single-point noise
        kernel     = np.ones(3) / 3
        log_smooth = np.convolve(log_i, kernel, mode='same')

        # First-order differences of the smoothed log-current
        dlog = np.diff(log_smooth)

        # A real switching event: derivative exceeds threshold AND stays high
        # for at least one more step (avoids spike false positives)
        threshold = np.log10(self.jump_factor)

        for k in range(1, len(dlog) - 1):
            if dlog[k] >= threshold and dlog[k + 1] >= threshold * 0.5:
                return float(voltages[k + 1])

        return None
    # ── main API ─────────────────────────────────────────────────────────────
    def extract(self, iv: IVData) -> CycleParameters:
        params = CycleParameters(
            cycle_index=iv.cycle_index,
            timestamp=iv.timestamp,
        )

        v = iv.v_arr
        i = iv.i_arr
        r = iv.r_arr

        if len(v) < 4:
            logger.warning(f"Cycle {iv.cycle_index}: too few points for extraction.")
            return params

        pos_idx = self._positive_branch_idx(iv)
        neg_idx = self._negative_branch_idx(iv)

        # ── Vset (positive branch) ─────────────────────────────────────────
        pos_mask = pos_idx[np.abs(v[pos_idx]) >= self.v_threshold]
        if len(pos_mask) >= 2:
            params.vset = self._detect_jump(v[pos_mask], i[pos_mask])

        # ── Vreset (negative branch) ───────────────────────────────────────
        if len(neg_idx) >= 2:
            neg_mask = neg_idx[np.abs(v[neg_idx]) >= self.v_threshold]
            if len(neg_mask) >= 2:
                params.vreset = self._detect_jump(v[neg_mask], i[neg_mask])

        # ── Ron (after Vset on positive branch) ───────────────────────────
        if params.vset is not None and len(pos_idx) > 0:
            after_set = pos_idx[v[pos_idx] > params.vset]
            if len(after_set) > 0:
                r_lrs = r[after_set]
                r_lrs = r_lrs[np.isfinite(r_lrs)]
                if len(r_lrs):
                    params.ron = float(np.percentile(r_lrs, self.r_percentile))

        # ── Roff (before Vset on positive branch) ─────────────────────────
        if params.vset is not None and len(pos_idx) > 0:
            before_set = pos_idx[
                (v[pos_idx] >= self.v_threshold) & (v[pos_idx] < params.vset)
            ]
            if len(before_set) > 0:
                r_hrs = r[before_set]
                r_hrs = r_hrs[np.isfinite(r_hrs)]
                if len(r_hrs):
                    params.roff = float(np.percentile(r_hrs, self.r_percentile))
        else:
            # Fallback: take Roff from whole positive branch below threshold V
            low_v_mask = pos_idx[
                (v[pos_idx] >= self.v_threshold) & (v[pos_idx] <= self.v_threshold * 4)
            ]
            if len(low_v_mask) > 0:
                r_hrs = r[low_v_mask]
                r_hrs = r_hrs[np.isfinite(r_hrs)]
                if len(r_hrs):
                    params.roff = float(np.percentile(r_hrs, self.r_percentile))

        # ── On/Off ratio ──────────────────────────────────────────────────
        if params.ron is not None and params.roff is not None and params.ron > 0:
            params.on_off_ratio = params.roff / params.ron

        if params.ron is not None and params.roff is not None:
            logger.info(
             f"Cycle {iv.cycle_index}: Vset={params.vset}, Vreset={params.vreset}, "
             f"Ron={params.ron:.1f} Ω, Roff={params.roff:.1f} Ω, "
             f"On/Off={params.on_off_ratio}"
            )
        else:
             logger.info(
              f"Cycle {iv.cycle_index}: Vset={params.vset}, Vreset={params.vreset}, "
              f"Ron={params.ron}, Roff={params.roff}, "
              f"On/Off={params.on_off_ratio}"
            )

        return params


# ═══════════════════════════════════════════════════════════════════════════════
class DataManager:
    """
    Accumulates I–V data and extracted parameters; saves to CSV files.

    Files created
    -------------
    <output_dir>/iv_data_<timestamp>.csv       – raw I–V (all cycles)
    <output_dir>/parameters_<timestamp>.csv    – extracted parameters
    """

    def __init__(self, output_dir: str = "data"):
        self.output_dir   = output_dir
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._iv_path     = os.path.join(output_dir, f"iv_data_{ts}.csv")
        self._param_path  = os.path.join(output_dir, f"parameters_{ts}.csv")
        self._iv_header_written    = False
        self._param_header_written = False

        self.all_iv:     List[IVData]          = []
        self.all_params: List[CycleParameters] = []

    # ── appending data ───────────────────────────────────────────────────────
    def add_iv(self, iv: IVData):
        self.all_iv.append(iv)
        self._write_iv(iv)

    def add_params(self, params: CycleParameters):
        self.all_params.append(params)
        self._write_params(params)

    # ── CSV writers ──────────────────────────────────────────────────────────
    def _write_iv(self, iv: IVData):
        mode = "a"
        with open(self._iv_path, mode, newline="") as f:
            writer = csv.writer(f)
            if not self._iv_header_written:
                writer.writerow(["cycle_index", "timestamp", "voltage_V", "current_A"])
                self._iv_header_written = True
            for v, i in zip(iv.voltages, iv.currents):
                writer.writerow([iv.cycle_index, iv.timestamp, f"{v:.8g}", f"{i:.8g}"])

    def _write_params(self, p: CycleParameters):
        mode = "a"
        with open(self._param_path, mode, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(p).keys()))
            if not self._param_header_written:
                writer.writeheader()
                self._param_header_written = True
            writer.writerow(asdict(p))

    # ── accessors for plotting ────────────────────────────────────────────────
    def get_cycle_numbers(self) -> List[int]:
        return [p.cycle_index for p in self.all_params]

    def get_series(self, attr: str) -> List[Optional[float]]:
        return [getattr(p, attr) for p in self.all_params]

    @property
    def iv_path(self)    -> str: return self._iv_path
    @property
    def param_path(self) -> str: return self._param_path
