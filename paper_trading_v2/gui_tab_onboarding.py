"""
gui_tab_onboarding.py — Tab 1: Symbol Onboarding

Implements:
  - List of symbols with status colors (validated=green, candidate=yellow, rejected=red)
  - "Add New Symbol" button → 7-step wizard (data audit → build events → build dataset →
    train → walk-forward → gate decision → activate)
  - Each step unlocks the next upon completion
  - Wizard progress bar at the top of the wizard panel
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from paper_trading_v2.gui_components import (
    COLOR_POSITIVE,
    COLOR_NEGATIVE,
    COLOR_WARNING,
    COLOR_NEUTRAL,
    COLOR_SURFACE,
    COLOR_TEXT,
    ConfirmationDialog,
    fmt_time,
)
from paper_trading_v2.gui_bridge import SystemBridge


# ---------------------------------------------------------------------------
# Wizard step definitions
# ---------------------------------------------------------------------------

WIZARD_STEPS = [
    ("1. Data Audit",     "Audit raw data (missing bars, outliers)"),
    ("2. Build Events",   "Detect sweep events on historical data"),
    ("3. Build Dataset",  "Build labeled dataset with features"),
    ("4. Train",          "Train model on primary horizon"),
    ("5. Walk-Forward",   "Walk-forward validation"),
    ("6. Gate Decision",  "Review gate metrics → pass/fail"),
    ("7. Activate",       "Set status=validated and register in engine"),
]


class SymbolOnboardingTab(QWidget):
    """Tab 1: Symbol Onboarding — registry view + 7-step wizard."""

    def __init__(self, bridge: SystemBridge, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._wizard_open = False  # True when wizard is active
        self._wizard_current_step = 0
        self._wizard_completed_steps: List[int] = []

        self._build_ui()
        self._refresh_symbols()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 12, 16, 12)

        # --- Header ---
        header = QLabel("📋 Symbol Onboarding")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: white;")
        layout.addWidget(header)

        desc = QLabel(
            "Manage symbols and onboard new ones through the 7-step pipeline. "
            "Only symbols with status <b>validated</b> receive live signal engines."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {COLOR_NEUTRAL}; padding-bottom: 8px;")
        layout.addWidget(desc)

        # --- Add Symbol button ---
        btn_row = QHBoxLayout()
        self._add_btn = QPushButton("➕ Add New Symbol")
        self._add_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {COLOR_POSITIVE};
                color: #1e1e1e;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 4px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: #00dd77;
            }}
            """
        )
        self._add_btn.clicked.connect(self._on_add_symbol)
        btn_row.addWidget(self._add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # --- Symbol table ---
        self._table = QTableWidget(0, 4)
        self._table.setAlternatingRowColors(True)
        self._table.setHorizontalHeaderLabels(["Symbol", "Status", "Active", "Actions"])
        self._table.setColumnWidth(0, 140)
        self._table.setColumnWidth(1, 120)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 200)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self._table, stretch=1)

        # --- Wizard panel (starts hidden) ---
        self._wizard_frame = QFrame()
        self._wizard_frame.setStyleSheet(
            f"background-color: #252525; border: 1px solid #3d3d3d; border-radius: 6px; padding: 12px;"
        )
        self._wizard_frame.setVisible(False)
        self._build_wizard_ui()
        layout.addWidget(self._wizard_frame)

    def _build_wizard_ui(self) -> None:
        """Build the 7-step wizard panel inside _wizard_frame."""
        wiz_layout = QVBoxLayout(self._wizard_frame)
        wiz_layout.setSpacing(8)

        # Wizard title
        self._wiz_title = QLabel("Onboarding Wizard")
        self._wiz_title.setStyleSheet("font-size: 15px; font-weight: bold; color: white;")
        wiz_layout.addWidget(self._wiz_title)

        self._wiz_progress = QLabel("Step 1 of 7")
        self._wiz_progress.setStyleSheet(f"color: {COLOR_NEUTRAL};")
        wiz_layout.addWidget(self._wiz_progress)

        # Scrollable step list
        self._wiz_scroll = QScrollArea()
        self._wiz_scroll.setWidgetResizable(True)
        self._wiz_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._wiz_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._wiz_scroll.setStyleSheet("background: transparent; border: none;")
        self._wiz_scroll.setMaximumHeight(250)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(4)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        # Step list
        self._step_labels: List[QLabel] = []
        for idx, (step_name, step_desc) in enumerate(WIZARD_STEPS):
            lbl = QLabel(f"{'⬜'} {step_name} — {step_desc}")
            lbl.setWordWrap(True)
            lbl.setMinimumHeight(22)
            self._step_labels.append(lbl)
            scroll_layout.addWidget(lbl)

        scroll_layout.addStretch()
        self._wiz_scroll.setWidget(scroll_content)
        wiz_layout.addWidget(self._wiz_scroll)

        # Action button row
        action_row = QHBoxLayout()
        self._wiz_back_btn = QPushButton("← Back")
        self._wiz_next_btn = QPushButton("Start Step →")
        self._wiz_next_btn.setStyleSheet(
            f"background-color: {COLOR_POSITIVE}; color: #1e1e1e; font-weight: bold;"
        )
        self._wiz_cancel_btn = QPushButton("Cancel")
        self._wiz_cancel_btn.setStyleSheet(f"background-color: {COLOR_NEGATIVE}; color: white;")

        self._wiz_back_btn.clicked.connect(self._on_wizard_back)
        self._wiz_next_btn.clicked.connect(self._on_wizard_next)
        self._wiz_cancel_btn.clicked.connect(self._on_wizard_cancel)

        action_row.addWidget(self._wiz_cancel_btn)
        action_row.addStretch()
        action_row.addWidget(self._wiz_back_btn)
        action_row.addWidget(self._wiz_next_btn)
        wiz_layout.addLayout(action_row)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_symbols(self) -> None:
        """Refresh the symbol table from shared state."""
        snapshot = self._bridge.state.get_snapshot()
        registry = snapshot.get("symbol_registry", {})

        self._table.setRowCount(len(registry))

        for row, (name, cfg) in enumerate(sorted(registry.items())):
            # Symbol
            self._table.setItem(row, 0, QTableWidgetItem(name))

            # Status with color
            status_item = QTableWidgetItem(cfg["status"])
            status_color = {
                "validated": COLOR_POSITIVE,
                "candidate": COLOR_WARNING,
                "rejected": COLOR_NEGATIVE,
                "auditing": COLOR_WARNING,
                "building_events": COLOR_WARNING,
                "building_dataset": COLOR_WARNING,
                "training": COLOR_WARNING,
                "walk_forward": COLOR_WARNING,
                "gating": COLOR_WARNING,
            }.get(cfg["status"], COLOR_NEUTRAL)
            status_item.setForeground(QColor(status_color))
            self._table.setItem(row, 1, status_item)

            # Active
            active_item = QTableWidgetItem("✅ Yes" if cfg.get("active", False) else "❌ No")
            self._table.setItem(row, 2, active_item)

            # Actions: Onboard button
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 0, 4, 0)

            onboard_btn = QPushButton("Start Wizard")
            onboard_btn.setStyleSheet(
                f"background-color: {COLOR_WARNING}; color: #1e1e1e; padding: 4px 10px;"
            )
            onboard_btn.clicked.connect(
                lambda checked, s=name: self._start_wizard(s)
            )
            actions_layout.addWidget(onboard_btn)

            if cfg["status"] == "validated":
                deactivate_btn = QPushButton("Deactivate")
                deactivate_btn.setStyleSheet("padding: 4px 10px;")
                deactivate_btn.clicked.connect(
                    lambda checked, s=name: self._on_deactivate(s)
                )
                actions_layout.addWidget(deactivate_btn)

            actions_layout.addStretch()
            self._table.setCellWidget(row, 3, actions_widget)

        # Force row heights so all rows are visible after setRowCount changes
        self._table.resizeRowsToContents()

    # ------------------------------------------------------------------
    # Add Symbol
    # ------------------------------------------------------------------

    def _on_add_symbol(self) -> None:
        """Open a dialog with a dropdown of MCP-available symbols, then open wizard."""
        # Fetch available symbols from MCP — returns List[Dict] with 'symbol' key
        raw_symbols = self._bridge.fetch_symbols()
        if not raw_symbols:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "No Symbols Available",
                "No symbols are available from MCP/MT5. "
                "Please ensure MCP is connected and MT5 market watch has symbols.",
            )
            return

        # Normalize to string list (handle both List[str] and List[Dict])
        available_symbols: List[str] = []
        for s in raw_symbols:
            if isinstance(s, str):
                available_symbols.append(s)
            elif isinstance(s, dict):
                available_symbols.append(s.get("symbol", s.get("name", "")))
        available_symbols = sorted([s for s in available_symbols if s])

        registered = set(self._bridge.state.get_registered_symbols())
        # Filter out already-registered symbols
        unregistered = sorted([s for s in available_symbols if s.upper() not in {r.upper() for r in registered}])

        if not unregistered:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "All Symbols Registered",
                "All available symbols from MCP are already in the registry.",
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Add New Symbol")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        msg = QLabel("Select a symbol to add from the MCP market watch:")
        msg.setWordWrap(True)
        layout.addWidget(msg)

        combo = QComboBox()
        combo.addItems(unregistered)
        combo.setEditable(False)
        layout.addWidget(combo)

        button_box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        confirm_btn = button_box.button(QDialogButtonBox.Ok)
        confirm_btn.setText("Add & Open Wizard")
        confirm_btn.setStyleSheet(
            "background-color: #00cc66; color: #1e1e1e; font-weight: bold;"
        )
        layout.addWidget(button_box)

        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)

        if dialog.exec() == QDialog.Accepted:
            name = combo.currentText().strip().upper()
            if not name:
                return
            from paper_trading_v2.shared_app_state_v2 import SymbolConfig
            config = SymbolConfig(name=name, status="candidate", active=False)
            self._bridge.state.register_symbol(name, config)
            self._bridge.log_action("add_symbol", {"symbol": name})
            self._refresh_symbols()
            self._start_wizard(name)

    # ------------------------------------------------------------------
    # Wizard lifecycle
    # ------------------------------------------------------------------

    def _start_wizard(self, symbol: str) -> None:
        """Open the 7-step wizard for the given symbol."""
        self._wizard_symbol = symbol
        self._wizard_current_step = 0
        self._wizard_completed_steps = []
        self._wizard_open = True
        self._wizard_frame.setVisible(True)
        self._update_wizard_ui()
        self._bridge.log_action("wizard_start", {"symbol": symbol})

    def _update_wizard_ui(self) -> None:
        """Refresh wizard step labels and button state."""
        if not self._wizard_open:
            return

        self._wiz_title.setText(f"Onboarding Wizard — {self._wizard_symbol}")
        self._wiz_progress.setText(
            f"Step {self._wizard_current_step + 1} of {len(WIZARD_STEPS)}"
        )

        for idx, lbl in enumerate(self._step_labels):
            step_name = WIZARD_STEPS[idx][0]
            step_desc = WIZARD_STEPS[idx][1]
            if idx in self._wizard_completed_steps:
                lbl.setText(f"{'✅'} {step_name} — {step_desc}")
                lbl.setStyleSheet(f"color: {COLOR_POSITIVE}; font-weight: bold;")
            elif idx == self._wizard_current_step:
                lbl.setText(f"{'⏳'} {step_name} — {step_desc} {'(current)'}")
                lbl.setStyleSheet(f"color: {COLOR_WARNING}; font-weight: bold; "
                                  f"background-color: #3a3a3a; padding: 2px 4px; border-radius: 3px;")
            elif idx < self._wizard_current_step:
                # Completed steps stay fully visible (not collapsed)
                lbl.setText(f"{'✅'} {step_name} — {step_desc}")
                lbl.setStyleSheet(f"color: {COLOR_POSITIVE};")
            else:
                lbl.setText(f"{'⬜'} {step_name} — {step_desc}")
                lbl.setStyleSheet(f"color: {COLOR_TEXT};")

        # Button state
        self._wiz_back_btn.setEnabled(self._wizard_current_step > 0)

        if self._wizard_current_step < len(WIZARD_STEPS):
            if self._wizard_current_step in self._wizard_completed_steps:
                self._wiz_next_btn.setText("Next Step →")
                self._wiz_next_btn.setEnabled(True)
            elif self._wizard_current_step == len(WIZARD_STEPS) - 1:
                self._wiz_next_btn.setText("✅ Activate Symbol")
                self._wiz_next_btn.setEnabled(True)
            else:
                self._wiz_next_btn.setText(f"▶ Run {WIZARD_STEPS[self._wizard_current_step][0]}")
                self._wiz_next_btn.setEnabled(True)
        else:
            self._wiz_next_btn.setText("Done")
            self._wiz_next_btn.setEnabled(True)

        # Scroll to keep current step visible
        if self._wizard_current_step < len(self._step_labels):
            target = self._step_labels[self._wizard_current_step]
            self._wiz_scroll.ensureWidgetVisible(target, 0, 0)

    def _on_wizard_next(self) -> None:
        """Advance to the next wizard step or complete."""
        if not self._wizard_open:
            return

        # Mark current step as completed
        step = self._wizard_current_step
        if step not in self._wizard_completed_steps:
            self._wizard_completed_steps.append(step)
            self._bridge.log_action(
                "wizard_step_complete",
                {
                    "symbol": self._wizard_symbol,
                    "step": WIZARD_STEPS[step][0],
                    "step_index": step,
                },
            )

            # Update symbol status to reflect progress
            status_map = {
                0: "auditing",
                1: "building_events",
                2: "building_dataset",
                3: "training",
                4: "walk_forward",
                5: "gating",
                6: "validated",
            }
            new_status = status_map.get(step + 1)
            if new_status and self._wizard_symbol:
                cfg = self._bridge.state.get_symbol_config(self._wizard_symbol)
                if cfg:
                    from paper_trading_v2.shared_app_state_v2 import SymbolConfig
                    self._bridge.state.register_symbol(
                        self._wizard_symbol,
                        SymbolConfig(
                            name=self._wizard_symbol,
                            status=new_status,
                            position_size_multiplier=cfg.position_size_multiplier,
                            active=(new_status == "validated"),
                        ),
                    )

        # Advance step
        if step + 1 >= len(WIZARD_STEPS):
            self._close_wizard()
            self._refresh_symbols()
            return

        self._wizard_current_step += 1
        self._update_wizard_ui()
        self._refresh_symbols()

    def _on_wizard_back(self) -> None:
        """Go back one step — remove completed flag from the step we leave."""
        if self._wizard_current_step <= 0:
            return
        # Remove completed flag from the step we are leaving
        prev_step = self._wizard_current_step - 1
        if prev_step in self._wizard_completed_steps:
            self._wizard_completed_steps.remove(prev_step)
        self._wizard_current_step = prev_step
        self._update_wizard_ui()

    def _on_wizard_cancel(self) -> None:
        """Cancel the wizard."""
        dialog = ConfirmationDialog(
            title="Cancel Wizard",
            message=f"Cancel onboarding wizard for {self._wizard_symbol}? Progress will be lost.",
            confirm_text="Yes, Cancel",
        )
        if dialog.exec() == ConfirmationDialog.Accepted:
            self._close_wizard()

    def _close_wizard(self) -> None:
        """Close the wizard panel."""
        self._wizard_open = False
        self._wizard_frame.setVisible(False)
        self._wizard_symbol = ""
        self._wizard_current_step = 0
        self._wizard_completed_steps = []
        self._refresh_symbols()

    def _on_deactivate(self, symbol: str) -> None:
        """Deactivate a validated symbol."""
        dialog = ConfirmationDialog(
            title="Deactivate Symbol",
            message=f"Deactivate {symbol}? It will remain registered but inactive.",
            confirm_text="Deactivate",
        )
        if dialog.exec() == ConfirmationDialog.Accepted:
            cfg = self._bridge.state.get_symbol_config(symbol)
            if cfg:
                from paper_trading_v2.shared_app_state_v2 import SymbolConfig
                self._bridge.state.register_symbol(
                    symbol,
                    SymbolConfig(
                        name=symbol, status="validated",
                        position_size_multiplier=cfg.position_size_multiplier,
                        active=False,
                    ),
                )
            self._bridge.log_action("deactivate_symbol", {"symbol": symbol})
            self._refresh_symbols()

    # ------------------------------------------------------------------
    # External refresh (called by main window timer)
    # ------------------------------------------------------------------

    def refresh_from_state(self) -> None:
        """Called by the main window timer to refresh symbol data."""
        self._refresh_symbols()