"""
gui.py
======
PyQt5 + pyqtgraph GUI for the Keithley 2450 Memristor Tester.
"""

import sys
import os
import logging
import pyqtgraph as pg
import pyqtgraph.exporters
from datetime import datetime
from typing import Optional, List

import numpy as np
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton,
    QSpinBox, QDoubleSpinBox, QTextEdit, QTabWidget,
    QFileDialog, QMessageBox, QCheckBox, QSplitter,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont



from keithley_controller import Keithley2450, SimulatedKeithley, SweepEngine
from data_analysis import IVData, CycleParameters, DataManager, ParameterExtractor

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Stable data directory next to the script, regardless of launch directory
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.path.join(_SCRIPT_DIR, "data")

# ── colour palette ─────────────────────────────────────────────────────────────
BG      = "#0d1117"
BG2     = "#161b22"
BG3     = "#21262d"
ACCENT  = "#58a6ff"
GREEN   = "#3fb950"
RED     = "#f85149"
YELLOW  = "#d29922"
TEXT    = "#c9d1d9"
TEXT_DIM= "#8b949e"
BORDER  = "#30363d"

PLOT_COLORS = [
    "#58a6ff", "#3fb950", "#d29922", "#f78166",
    "#bc8cff", "#39d353", "#ff7b72", "#ffa657",
]


# ═══════════════════════════════════════════════════════════════════════════════
class SweepWorker(QThread):
    point_ready = pyqtSignal(float, float)
    cycle_done  = pyqtSignal(int, object)
    all_done    = pyqtSignal()
    error       = pyqtSignal(str)

    def __init__(self, instrument, vmax, step, delay, compliance, nplc, n_cycles):
        super().__init__()
        self.instrument = instrument
        self.vmax       = vmax
        self.step       = step
        self.delay      = delay
        self.compliance = compliance
        self.nplc       = nplc
        self.n_cycles   = n_cycles
        self._stop      = False
        self._extractor = ParameterExtractor()

    def request_stop(self):
        self._stop = True

    def run(self):
        try:
            for cycle_idx in range(1, self.n_cycles + 1):
                if self._stop:
                    break

                engine = SweepEngine(
                    self.instrument,
                    vmax=self.vmax, step=self.step,
                    delay=self.delay, compliance=self.compliance, nplc=self.nplc,
                )
                engine.reset_stop()

                iv = IVData(cycle_index=cycle_idx,
                            timestamp=datetime.now().isoformat())

                def on_point(v, i):
                    if self._stop:
                        engine.stop()
                    iv.add_point(v, i)
                    self.point_ready.emit(v, i)

                engine.run_cycle(on_point=on_point)
                params = self._extractor.extract(iv)
                self.cycle_done.emit(cycle_idx, (iv, params))

                if self._stop:
                    break

            self.all_done.emit()

        except Exception as exc:
            logger.exception("SweepWorker error")
            self.error.emit(str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
class StyledGroupBox(QGroupBox):
    def __init__(self, title, parent=None):
        super().__init__(title, parent)
        self.setStyleSheet(f"""
            QGroupBox {{
                color: {ACCENT};
                border: 1px solid {BORDER};
                border-radius: 6px;
                margin-top: 8px;
                font-weight: bold;
                font-size: 11px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
        """)


# ═══════════════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Memristor Tester  ·  Keithley 2450")
        self.resize(1400, 860)

        self._instrument = None
        self._worker     = None
        self._data_mgr   = None

        self._v_buf: List[float] = []
        self._i_buf: List[float] = []

        self._cycles  = []
        self._ron     = []
        self._roff    = []
        self._vset    = []
        self._vreset  = []

        self._apply_theme()
        self._build_ui()

    def _apply_theme(self):
        pg.setConfigOption("background", BG)
        pg.setConfigOption("foreground", TEXT)

        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: Consolas, monospace;
                font-size: 12px;
            }}
            QLabel {{ color: {TEXT}; }}
            QLineEdit, QDoubleSpinBox, QSpinBox {{
                background: {BG3};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 4px 6px;
                color: {TEXT};
            }}
            QPushButton {{
                background: {BG3};
                border: 1px solid {BORDER};
                border-radius: 4px;
                padding: 6px 14px;
                color: {TEXT};
            }}
            QPushButton:hover {{ background: {BG2}; border-color: {ACCENT}; }}
            QPushButton#start_btn {{
                background: #1f6feb;
                border-color: #388bfd;
                color: white;
                font-weight: bold;
            }}
            QPushButton#stop_btn {{
                background: #b91c1c;
                border-color: #ef4444;
                color: white;
                font-weight: bold;
            }}
            QTextEdit {{
                background: {BG2};
                border: 1px solid {BORDER};
                border-radius: 4px;
                color: {TEXT_DIM};
                font-size: 11px;
            }}
            QTabWidget::pane {{ border: 1px solid {BORDER}; background: {BG}; }}
            QTabBar::tab {{
                background: {BG3}; border: 1px solid {BORDER};
                padding: 6px 16px; color: {TEXT_DIM};
            }}
            QTabBar::tab:selected {{ background: {BG}; color: {ACCENT}; }}
            QCheckBox {{ color: {TEXT}; }}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QHBoxLayout(central)
        main_lay.setSpacing(8)
        main_lay.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Horizontal)
        main_lay.addWidget(splitter)

        left = QWidget()
        left.setFixedWidth(310)
        left_lay = QVBoxLayout(left)
        left_lay.setSpacing(8)
        left_lay.setContentsMargins(0, 0, 0, 0)

        left_lay.addWidget(self._build_connection_group())
        left_lay.addWidget(self._build_sweep_group())
        left_lay.addWidget(self._build_cycle_group())
        left_lay.addWidget(self._build_control_group())
        left_lay.addWidget(self._build_status_group())
        left_lay.addWidget(self._build_export_group())
        left_lay.addStretch()

        right = QTabWidget()
        right.addTab(self._build_iv_tab(),        "I-V Curve")
        right.addTab(self._build_endurance_tab(), "Endurance")
        right.addTab(self._build_log_tab(),       "Log")

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _build_connection_group(self):
        grp = StyledGroupBox("Instrument")
        lay = QGridLayout(grp)
        lay.setSpacing(6)

        lay.addWidget(QLabel("VISA Resource:"), 0, 0)
        self.visa_edit = QLineEdit("TCPIP0::10.1.198.37::inst0::INSTR")
        lay.addWidget(self.visa_edit, 0, 1)

        self.sim_check = QCheckBox("Simulation mode")
        self.sim_check.setChecked(True)
        lay.addWidget(self.sim_check, 1, 0, 1, 2)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        lay.addWidget(self.connect_btn, 2, 0, 1, 2)

        return grp

    def _build_sweep_group(self):
        grp = StyledGroupBox("Sweep Parameters")
        lay = QGridLayout(grp)
        lay.setSpacing(6)

        def spin(mn, mx, val, dec, suffix):
            s = QDoubleSpinBox()
            s.setRange(mn, mx)
            s.setValue(val)
            s.setDecimals(dec)
            s.setSuffix(suffix)
            return s

        self.vmax_spin       = spin(0.1,  20.0,  3.0,  2, " V")
        self.step_spin       = spin(0.001, 1.0,  0.02, 3, " V")
        self.delay_spin      = spin(0.0,   5.0,  0.01, 3, " s")
        self.compliance_spin = spin(1e-6,  0.1,  1e-3, 6, " A")
        self.nplc_spin       = spin(0.01, 10.0,  0.1,  2, "")

        rows = [
            ("Vmax:",       self.vmax_spin),
            ("Step:",       self.step_spin),
            ("Delay:",      self.delay_spin),
            ("Compliance:", self.compliance_spin),
            ("NPLC:",       self.nplc_spin),
        ]
        for r, (lbl, w) in enumerate(rows):
            lay.addWidget(QLabel(lbl), r, 0)
            lay.addWidget(w, r, 1)

        return grp

    def _build_cycle_group(self):
        grp = StyledGroupBox("Cycling")
        lay = QGridLayout(grp)
        lay.setSpacing(6)

        lay.addWidget(QLabel("Cycles (N):"), 0, 0)
        self.n_cycles_spin = QSpinBox()
        self.n_cycles_spin.setRange(1, 100000)
        self.n_cycles_spin.setValue(10)
        lay.addWidget(self.n_cycles_spin, 0, 1)

        self.retention_check = QCheckBox("Retention after sweep")
        lay.addWidget(self.retention_check, 1, 0, 1, 2)

        return grp

    def _build_control_group(self):
        grp = StyledGroupBox("Control")
        lay = QHBoxLayout(grp)

        self.start_btn = QPushButton("▶  START")
        self.start_btn.setObjectName("start_btn")
        self.start_btn.clicked.connect(self._on_start)

        self.stop_btn = QPushButton("■  STOP")
        self.stop_btn.setObjectName("stop_btn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)

        lay.addWidget(self.start_btn)
        lay.addWidget(self.stop_btn)
        return grp

    def _build_status_group(self):
        grp = StyledGroupBox("Status")
        lay = QGridLayout(grp)
        lay.setSpacing(4)

        self.status_label = QLabel("IDLE")
        self.status_label.setStyleSheet(
            f"color: {YELLOW}; font-weight: bold; font-size: 14px;")
        lay.addWidget(self.status_label, 0, 0, 1, 2)

        self.cycle_label  = QLabel("Cycle: -")
        self.vset_label   = QLabel("Vset:  -")
        self.vreset_label = QLabel("Vreset: -")
        self.ron_label    = QLabel("Ron:   -")
        self.roff_label   = QLabel("Roff:  -")
        self.ratio_label  = QLabel("On/Off: -")

        for i, lbl in enumerate([self.cycle_label, self.vset_label,
                                  self.vreset_label, self.ron_label,
                                  self.roff_label, self.ratio_label]):
            lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
            lay.addWidget(lbl, i + 1, 0, 1, 2)

        return grp

    def _build_export_group(self):
        grp = StyledGroupBox("Export")
        lay = QVBoxLayout(grp)

        self.export_png_btn = QPushButton("Save Plots as PNG")
        self.export_png_btn.clicked.connect(self._on_export_png)
        lay.addWidget(self.export_png_btn)

        self.open_data_btn = QPushButton("Open Data Folder")
        self.open_data_btn.clicked.connect(self._on_open_data)
        lay.addWidget(self.open_data_btn)

        return grp

    def _build_iv_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)

        # ── I-V plot ──────────────────────────────────────────────────────
        self._iv_plot_widget = pg.PlotWidget(title="I-V Characteristic")
        pw = self._iv_plot_widget
        pw.setLabel("bottom", "Voltage", units="V")
        pw.setLabel("left",   "Current", units="A")
        pw.showGrid(x=True, y=True, alpha=0.15)
        pw.addLegend(offset=(10, 10))

        pw.addItem(pg.InfiniteLine(pos=0, angle=90,
                                   pen=pg.mkPen(BORDER, width=1)))
        pw.addItem(pg.InfiniteLine(pos=0, angle=0,
                                   pen=pg.mkPen(BORDER, width=1)))

        self._iv_curves = []
        self._iv_current_curve = pw.plot(
            [], [], pen=pg.mkPen(PLOT_COLORS[0], width=2),
            name="Current cycle"
        )

        self._vset_marker = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(GREEN, width=1, style=Qt.DashLine),
            label="Vset", labelOpts={"color": GREEN}
        )
        self._vreset_marker = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(RED, width=1, style=Qt.DashLine),
            label="Vreset", labelOpts={"color": RED}
        )
        pw.addItem(self._vset_marker)
        pw.addItem(self._vreset_marker)
        self._vset_marker.setVisible(False)
        self._vreset_marker.setVisible(False)

        # ── R vs V plot ───────────────────────────────────────────────────
        self._rv_plot_widget = pg.PlotWidget(title="Resistance vs Voltage")
        rw = self._rv_plot_widget
        rw.setLabel("bottom", "Voltage", units="V")
        rw.setLabel("left",   "Resistance", units="Ω")
        rw.showGrid(x=True, y=True, alpha=0.15)
        rw.addLegend(offset=(10, 10))
        rw.setLogMode(x=False, y=True)   # log scale on Y — resistance spans decades

        rw.addItem(pg.InfiniteLine(pos=0, angle=90,
                                   pen=pg.mkPen(BORDER, width=1)))

        self._rv_curves  = []
        self._rv_current_curve = rw.plot(
            [], [], pen=pg.mkPen(YELLOW, width=2),
            name="Current cycle"
        )

        # Vset marker on R-V plot too
        self._rv_vset_marker = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(GREEN, width=1, style=Qt.DashLine),
            label="Vset", labelOpts={"color": GREEN}
        )
        self._rv_vreset_marker = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(RED, width=1, style=Qt.DashLine),
            label="Vreset", labelOpts={"color": RED}
        )
        rw.addItem(self._rv_vset_marker)
        rw.addItem(self._rv_vreset_marker)
        self._rv_vset_marker.setVisible(False)
        self._rv_vreset_marker.setVisible(False)

        # Stack both plots vertically, R-V takes 40% of height
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(pw)
        splitter.addWidget(rw)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)

        lay.addWidget(splitter)
        return w

    def _build_endurance_tab(self):
        w = QWidget()
        lay = QGridLayout(w)
        lay.setSpacing(4)
        lay.setContentsMargins(4, 4, 4, 4)

        def make_plot(title, y_label, y_unit, color):
            pw = pg.PlotWidget(title=title)
            pw.setLabel("bottom", "Cycle #")
            pw.setLabel("left", y_label, units=y_unit)
            pw.showGrid(x=True, y=True, alpha=0.15)
            curve = pw.plot([], [], pen=None,
                            symbol="o", symbolSize=5,
                            symbolBrush=pg.mkBrush(color),
                            symbolPen=pg.mkPen(color))
            return pw, curve

        self._ron_plot,    self._ron_curve    = make_plot("Ron vs Cycle",    "Ron",    "Ohm", ACCENT)
        self._roff_plot,   self._roff_curve   = make_plot("Roff vs Cycle",   "Roff",   "Ohm", YELLOW)
        self._vset_plot,   self._vset_curve   = make_plot("Vset vs Cycle",   "Vset",   "V",   GREEN)
        self._vreset_plot, self._vreset_curve = make_plot("Vreset vs Cycle", "Vreset", "V",   RED)

        lay.addWidget(self._ron_plot,    0, 0)
        lay.addWidget(self._roff_plot,   0, 1)
        lay.addWidget(self._vset_plot,   1, 0)
        lay.addWidget(self._vreset_plot, 1, 1)
        return w

    def _build_log_tab(self):
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        return self._log_edit

    def _on_connect(self):
        if self._instrument is not None:
            self._instrument.close()
            self._instrument = None
            self.connect_btn.setText("Connect")
            self._log("Instrument disconnected.")
            return

        try:
            if self.sim_check.isChecked():
                self._instrument = SimulatedKeithley()
                self._log(f"Connected (simulation): {self._instrument.resource_name}")
            else:
                resource = self.visa_edit.text().strip()
                self._instrument = Keithley2450(resource)
                self._log(f"Connected: {resource}")
            self.connect_btn.setText("Disconnect")
        except Exception as e:
            QMessageBox.critical(self, "Connection Error", str(e))
            self._log(f"ERROR: {e}")

    def _on_start(self):
        if self._instrument is None:
            QMessageBox.warning(self, "Not connected",
                                "Please connect to the instrument first.")
            return

        self._v_buf.clear()
        self._i_buf.clear()
        self._cycles.clear()
        self._ron.clear(); self._roff.clear()
        self._vset.clear(); self._vreset.clear()
        self._iv_current_curve.setData([], [])
        for c in self._iv_curves:
            self._iv_plot_widget.removeItem(c)
        self._iv_curves.clear()
        self._vset_marker.setVisible(False)
        self._vreset_marker.setVisible(False)

        self._data_mgr = DataManager(output_dir=DATA_DIR)

        self._worker = SweepWorker(
            instrument=self._instrument,
            vmax=self.vmax_spin.value(),
            step=self.step_spin.value(),
            delay=self.delay_spin.value(),
            compliance=self.compliance_spin.value(),
            nplc=self.nplc_spin.value(),
            n_cycles=self.n_cycles_spin.value(),
        )
        self._worker.point_ready.connect(self._on_point)
        self._worker.cycle_done.connect(self._on_cycle_done)
        self._worker.all_done.connect(self._on_all_done)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_status("RUNNING", ACCENT)
        self._log("Sweep started.")

    def _on_stop(self):
        if self._worker:
            self._worker.request_stop()
            self._log("Stop requested...")

    def _on_point(self, v: float, i: float):
        self._v_buf.append(v)
        self._i_buf.append(i)
        self._iv_current_curve.setData(self._v_buf, self._i_buf)

        # Update R vs V live — only plot points where |V| > threshold to avoid noise
        v_arr = np.array(self._v_buf)
        i_arr = np.array(self._i_buf)
        mask  = np.abs(i_arr) > 1e-12   # ignore near-zero current points
        if mask.sum() > 2:
            with np.errstate(divide="ignore", invalid="ignore"):
                r_arr = np.where(mask,
                                 np.abs(v_arr / i_arr),
                                 np.nan)
            valid = np.isfinite(r_arr) & (r_arr > 0)
            if valid.sum() > 2:
                self._rv_current_curve.setData(v_arr[valid], r_arr[valid])

    def _on_cycle_done(self, cycle_idx: int, payload):
        iv, params = payload

        self._data_mgr.add_iv(iv)
        self._data_mgr.add_params(params)

        color_idx = (cycle_idx - 1) % len(PLOT_COLORS)
        archived = self._iv_plot_widget.plot(
            iv.voltages, iv.currents,
            pen=pg.mkPen(PLOT_COLORS[color_idx], width=1.2),
            name=f"Cycle {cycle_idx}"
        )
        self._iv_curves.append(archived)

        # Archive R vs V curve
        v_arr = np.array(iv.voltages)
        i_arr = np.array(iv.currents)
        mask  = np.abs(i_arr) > 1e-12
        if mask.sum() > 2:
            with np.errstate(divide="ignore", invalid="ignore"):
                r_arr = np.where(mask, np.abs(v_arr / i_arr), np.nan)
            valid = np.isfinite(r_arr) & (r_arr > 0)
            if valid.sum() > 2:
                rv_archived = self._rv_plot_widget.plot(
                    v_arr[valid], r_arr[valid],
                    pen=pg.mkPen(PLOT_COLORS[color_idx], width=1.2),
                    name=f"Cycle {cycle_idx}"
                )
                self._rv_curves.append(rv_archived)

        self._v_buf.clear()
        self._i_buf.clear()
        self._iv_current_curve.setData([], [])
        self._rv_current_curve.setData([], [])

        if params.vset is not None:
            self._vset_marker.setPos(params.vset)
            self._vset_marker.setVisible(True)
            self._rv_vset_marker.setPos(params.vset)
            self._rv_vset_marker.setVisible(True)
        if params.vreset is not None:
            self._vreset_marker.setPos(params.vreset)
            self._vreset_marker.setVisible(True)
            self._rv_vreset_marker.setPos(params.vreset)
            self._rv_vreset_marker.setVisible(True)
        self._cycles.append(cycle_idx)
        self._ron.append(params.ron if params.ron is not None else float("nan"))
        self._roff.append(params.roff if params.roff is not None else float("nan"))
        self._vset.append(params.vset if params.vset is not None else float("nan"))
        self._vreset.append(params.vreset if params.vreset is not None else float("nan"))

        self._ron_curve.setData(   self._cycles, self._ron)
        self._roff_curve.setData(  self._cycles, self._roff)
        self._vset_curve.setData(  self._cycles, self._vset)
        self._vreset_curve.setData(self._cycles, self._vreset)

        fmt = lambda v, u: f"{v:.4g} {u}" if v is not None else "-"
        self.cycle_label.setText(  f"Cycle: {cycle_idx} / {self.n_cycles_spin.value()}")
        self.vset_label.setText(   f"Vset:   {fmt(params.vset,  'V')}")
        self.vreset_label.setText( f"Vreset: {fmt(params.vreset,'V')}")
        self.ron_label.setText(    f"Ron:    {fmt(params.ron,   'Ohm')}")
        self.roff_label.setText(   f"Roff:   {fmt(params.roff,  'Ohm')}")
        self.ratio_label.setText(  f"On/Off: {fmt(params.on_off_ratio, '')}")

        self._log(
            f"Cycle {cycle_idx}: Vset={fmt(params.vset,'V')}, "
            f"Vreset={fmt(params.vreset,'V')}, "
            f"Ron={fmt(params.ron,'Ohm')}, Roff={fmt(params.roff,'Ohm')}, "
            f"On/Off={fmt(params.on_off_ratio,'')}"
        )

    def _on_all_done(self):
        self._set_status("DONE", GREEN)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._log("All cycles complete.")
        if self._data_mgr:
            self._log(f"I-V data   -> {self._data_mgr.iv_path}")
            self._log(f"Parameters -> {self._data_mgr.param_path}")

    def _on_worker_error(self, msg: str):
        self._set_status("ERROR", RED)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        QMessageBox.critical(self, "Sweep Error", msg)
        self._log(f"ERROR: {msg}")

    def _on_export_png(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder for PNG export")
        if not folder:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for name, widget in [
            ("iv_curve",     self._iv_plot_widget),
            ("ron_cycle",    self._ron_plot),
            ("roff_cycle",   self._roff_plot),
            ("vset_cycle",   self._vset_plot),
            ("vreset_cycle", self._vreset_plot),
        ]:
            path = os.path.join(folder, f"{name}_{ts}.png")
            exporter = pg.exporters.ImageExporter(widget.plotItem)
            exporter.export(path)
        self._log(f"Plots exported to {folder}")

    def _on_open_data(self):
       import subprocess, sys
       path = os.path.abspath("data")
       os.makedirs(path, exist_ok=True)
       if sys.platform == "win32":
         subprocess.Popen(f'explorer "{path}"')
       elif sys.platform == "darwin":
         subprocess.Popen(["open", path])
       else:
         subprocess.Popen(["xdg-open", path])   # Linux

    def _set_status(self, text: str, color: str):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {color}; font-weight: bold; font-size: 14px;")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_edit.append(f"[{ts}] {msg}")

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.request_stop()
            self._worker.wait(3000)
        if self._instrument:
            self._instrument.close()
        event.accept()


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Memristor Tester")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
