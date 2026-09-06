"""
gui_tab_live_control.py — Tab 2: Live Control

Spec section 7.2 Tab 2:
  - Dropdown per symbol (only validated symbols)
  - Automation level switch (0/1/2) per symbol, displayed prominently
  - Pending signals table with Send Order / Skip buttons per row (Level 0 = manual)
  - Open positions table with P/L in R
  - Emergency Stop button always visible (rendered in gui_main.py, not here)

All controls are immediate-response (no web reload).
Every destructive action needs a second confirmation dialog.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from live.gui.gui_components import (
    COLOR_NEGATIVE,
    COLOR_POSITIVE,
    COLOR_WARNING,
    COLOR_NEUTRAL,
    COLOR_SURFACE,
    COLOR_TEXT,
    ConfirmationDialog,
    fmt_time,
    fmt_r,
)
from live.gui.gui_bridge import SystemBridge


class LiveControlTab(QWidget):
    """Tab 2: Live Control — signals, positions, automation."""

    def __init__(self, bridge: SystemBridge, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._current_symbol: str = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 10, 12, 10)

        # --- Header ---
        header = QLabel("🎮 Live Control")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: white;")
        layout.addWidget(header)

        desc = QLabel(
            "Monitor and control live trading. "
            "Only <b>validated</b> symbols appear in the dropdown."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {COLOR_NEUTRAL}; padding-bottom: 4px;")
        layout.addWidget(desc)

        # --- Symbol selector + Automation level row ---
        selector_row = QHBoxLayout()
        selector_row.setSpacing(16)

        # Symbol dropdown
        selector_row.addWidget(QLabel("Symbol:"))
        self._symbol_combo = QComboBox()
        self._symbol_combo.setMinimumWidth(140)
        self._symbol_combo.currentTextChanged.connect(self._on_symbol_changed)
        selector_row.addWidget(self._symbol_combo)
        selector_row.addSpacing(20)

        # Automation level
        selector_row.addWidget(QLabel("Automation Level:"))
        self._auto_combo = QComboBox()
        self._auto_combo.addItems(["1 — Manual Confirm", "2 — Semi-Auto", "3 — Full Auto"])
        self._auto_combo.setMinimumWidth(180)
        self._auto_combo.currentIndexChanged.connect(self._on_auto_level_changed)
        selector_row.addWidget(self._auto_combo)
        selector_row.addStretch()

        layout.addLayout(selector_row)

        # --- Status info ---
        self._status_label = QLabel("Select a symbol above.")
        self._status_label.setStyleSheet(f"color: {COLOR_NEUTRAL};")
        layout.addWidget(self._status_label)

        # --- Split: Pending Signals (left) | Open Positions (right) ---
        split_row = QHBoxLayout()
        split_row.setSpacing(8)

        # LEFT: Pending Signals
        sig_group = QGroupBox("📡 Pending Signals (Level 1 — Manual)")
        sig_layout = QVBoxLayout(sig_group)
        self._signals_table = QTableWidget(0, 8)
        self._signals_table.setHorizontalHeaderLabels(
            ["Asset", "Dir", "Entry", "Stop Loss", "Take Profit", "Rule Score", "Model Prob", "Action"]
        )
        self._signals_table.horizontalHeader().setStretchLastSection(True)
        self._signals_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._signals_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._signals_table.setAlternatingRowColors(True)
        self._signals_table.verticalHeader().setVisible(False)
        sig_layout.addWidget(self._signals_table)
        split_row.addWidget(sig_group, stretch=7)

        # RIGHT: Open Positions
        pos_group = QGroupBox("💼 Open Positions")
        pos_layout = QVBoxLayout(pos_group)
        self._positions_table = QTableWidget(0, 8)
        self._positions_table.setHorizontalHeaderLabels(
            ["Asset", "Dir", "Entry", "Current", "Size", "P/L (R)", "Open Time", "Close"]
        )
        self._positions_table.horizontalHeader().setStretchLastSection(True)
        self._positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._positions_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._positions_table.setAlternatingRowColors(True)
        self._positions_table.verticalHeader().setVisible(False)
        pos_layout.addWidget(self._positions_table)
        split_row.addWidget(pos_group, stretch=5)

        layout.addLayout(split_row)

        # --- Refresh symbols on load ---
        self._refresh_symbol_dropdown()

    # ------------------------------------------------------------------
    # Symbol dropdown management
    # ------------------------------------------------------------------

    def _refresh_symbol_dropdown(self) -> None:
        """Re-populate the symbol dropdown with validated symbols."""
        current = self._symbol_combo.currentText()
        self._symbol_combo.blockSignals(True)
        self._symbol_combo.clear()

        symbols = self._get_validated_symbols()
        if symbols:
            self._symbol_combo.addItems(symbols)
            if current in symbols:
                self._symbol_combo.setCurrentText(current)
            else:
                self._symbol_combo.setCurrentIndex(0)
                self._current_symbol = symbols[0]
        else:
            self._symbol_combo.addItem("(no symbols)")

        self._symbol_combo.blockSignals(False)

    def _get_validated_symbols(self) -> List[str]:
        """Get list of validated symbol names from shared state."""
        snap = self._bridge.state.get_snapshot()
        reg = snap.get("symbol_registry", {})
        return sorted([
            name for name, cfg in reg.items()
            if isinstance(cfg, dict) and cfg.get("status") == "validated"
        ])

    def _on_symbol_changed(self, symbol: str) -> None:
        """React to symbol selection change."""
        if not symbol or symbol == "(no symbols)":
            return
        self._current_symbol = symbol

        # Update automation level combo to current level
        snap = self._bridge.state.get_snapshot()
        auto_levels = snap.get("automation_levels", {})
        level = auto_levels.get(symbol, 1)
        self._auto_combo.blockSignals(True)
        self._auto_combo.setCurrentIndex(level)
        self._auto_combo.blockSignals(False)

        self._status_label.setText(f"Selected: {symbol} | Automation: Level {level}")
        self._refresh_display(snap)

    def _on_auto_level_changed(self, index: int) -> None:
        """Handle automation level change — requires confirmation for level > 0."""
        if not self._current_symbol or self._current_symbol == "(no symbols)":
            return

        if index == 0:
            # Level 1 = Manual — no confirmation needed (default)
            self._apply_auto_level(index + 1)
        else:
            # Level 2 or 3 — require confirmation
            labels = {1: "Semi-Auto", 2: "Full Auto"}
            level = index + 1
            msg = (
                f"Change automation level for <b>{self._current_symbol}</b> "
                f"to <b>Level {level} — {labels.get(index, '?')}</b>?\n\n"
                f"{'Agent will send orders automatically after a delay.' if index == 0 else '⚠️ Orders placed entirely without manual review.'}"
            )
            dialog = ConfirmationDialog(
                title="Change Automation Level",
                message=msg,
                confirm_text=f"✅ Set Level {index}",
                parent=self,
            )
            if dialog.exec():
                self._apply_auto_level(level)

    def _apply_auto_level(self, level: int) -> None:
        """Apply the automation level change."""
        self._bridge.state.set_automation_level(self._current_symbol, level)
        self._bridge.log_action("change_automation_level", {
            "symbol": self._current_symbol,
            "new_level": level,
        })
        self._status_label.setText(
            f"Automation for {self._current_symbol} set to Level {level}"
        )

    # ------------------------------------------------------------------
    # Display refresh
    # ------------------------------------------------------------------

    def refresh_from_state(self) -> None:
        """Called periodically by the main window timer."""
        snap = self._bridge.state.get_snapshot()
        self._refresh_symbol_dropdown()
        self._refresh_display(snap)

    def _refresh_display(self, snap: Dict[str, Any]) -> None:
        """Refresh signals and positions tables from snapshot."""
        if not self._current_symbol or self._current_symbol == "(no symbols)":
            return
        symbol = self._current_symbol

        # Pending signals filtered by symbol
        pending = [s for s in snap.get("pending_signals", [])
                   if s.get("asset", "").upper() == symbol.upper()]
        self._populate_signals_table(pending)

        # Open positions filtered by symbol
        positions = [p for p in snap.get("open_positions", [])
                     if p.get("asset", "").upper() == symbol.upper()]
        self._populate_positions_table(positions)

    def _populate_signals_table(self, signals: List[Dict]) -> None:
        """Fill the pending signals table."""
        self._signals_table.setRowCount(len(signals))

        for row, sig in enumerate(signals):
            self._signals_table.setItem(row, 0, QTableWidgetItem(sig.get("asset", "")))
            dir_text = "🟢 BUY" if sig.get("direction", "").lower() == "buy" else "🔴 SELL"
            self._signals_table.setItem(row, 1, QTableWidgetItem(dir_text))
            self._signals_table.setItem(row, 2, QTableWidgetItem(f"{sig.get('entry_price', 0):.5f}"))
            self._signals_table.setItem(row, 3, QTableWidgetItem(f"{sig.get('stop_loss', 0):.5f}"))
            self._signals_table.setItem(row, 4, QTableWidgetItem(f"{sig.get('take_profit', 0):.5f}"))
            self._signals_table.setItem(row, 5, QTableWidgetItem(f"{sig.get('rule_score', 0):.3f}"))
            self._signals_table.setItem(row, 6, QTableWidgetItem(f"{sig.get('model_probability', 0):.3f}"))

            # Send Order button
            signal_id = sig.get("signal_id", str(row))
            send_btn = QPushButton("Send Order")
            send_btn.setStyleSheet(f"background-color: {COLOR_POSITIVE}; color: #1e1e1e; font-weight: bold;")
            send_btn.clicked.connect(lambda checked, sid=signal_id: self._on_send_order(sid))
            self._signals_table.setCellWidget(row, 7, send_btn)

    def _populate_positions_table(self, positions: List[Dict]) -> None:
        """Fill the open positions table."""
        self._positions_table.setRowCount(len(positions))

        for row, pos in enumerate(positions):
            self._positions_table.setItem(row, 0, QTableWidgetItem(pos.get("asset", "")))
            dir_text = "🟢 BUY" if pos.get("direction", "").lower() == "buy" else "🔴 SELL"
            self._positions_table.setItem(row, 1, QTableWidgetItem(dir_text))
            self._positions_table.setItem(row, 2, QTableWidgetItem(
                f"{pos.get('entry_price', 0):.5f}" if pos.get('entry_price') else "—"))
            self._positions_table.setItem(row, 3, QTableWidgetItem(
                f"{pos.get('current_price', 0):.5f}" if pos.get('current_price') else "—"))
            self._positions_table.setItem(row, 4, QTableWidgetItem(
                f"{pos.get('position_size', 0):.2f}"))

            # P/L in R
            entry = pos.get("entry_price", 0)
            current = pos.get("current_price", 0)
            stop = pos.get("stop_loss", 0)
            direction = pos.get("direction", "buy")
            r_range = abs(entry - stop) if stop and stop != 0 else 1
            raw_pl = (current - entry) / r_range if direction == "buy" else (entry - current) / r_range
            pl_item = QTableWidgetItem(fmt_r(raw_pl))
            pl_item.setForeground(Qt.GlobalColor.darkGreen if raw_pl >= 0 else Qt.GlobalColor.darkRed)
            self._positions_table.setItem(row, 5, pl_item)

            self._positions_table.setItem(row, 6, QTableWidgetItem(fmt_time(pos.get("open_time", 0))))

            # Close button
            pos_id = pos.get("position_id", "")
            close_btn = QPushButton("Close")
            close_btn.setStyleSheet(f"background-color: {COLOR_NEGATIVE}; color: white;")
            close_btn.clicked.connect(lambda checked, pid=pos_id: self._on_close_position(pid))
            self._positions_table.setCellWidget(row, 7, close_btn)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_send_order(self, signal_id: str) -> None:
        """Send order for a pending signal — requires confirmation."""
        dialog = ConfirmationDialog(
            title="Send Order Confirmation",
            message=f"Confirm sending market order for signal <b>{signal_id}</b>?\n\n"
                    "The order will be placed at the current market price.",
            confirm_text="✅ SEND ORDER",
            parent=self,
        )
        if dialog.exec():
            self._bridge.log_action("send_order_click", {"signal_id": signal_id})
            self._status_label.setText(f"Order sent for signal {signal_id}")

    def _on_close_position(self, position_id: str) -> None:
        """Close an open position — requires confirmation."""
        dialog = ConfirmationDialog(
            title="Close Position Confirmation",
            message=f"Confirm closing position <b>{position_id}</b>?\n\n"
                    "The position will be closed at the current market price.",
            confirm_text="✅ CLOSE POSITION",
            require_input=True,
            input_match="CLOSE",
            input_placeholder='Type "CLOSE" to confirm',
            parent=self,
        )
        if dialog.exec():
            self._bridge.close_position(position_id)
            self._bridge.log_action("close_position_click", {"position_id": position_id})
            self._status_label.setText(f"Position {position_id} closed.")