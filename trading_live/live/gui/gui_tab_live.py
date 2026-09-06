"""
gui_tab_live_control.py — Tab 2: Live Control

Spec section 7.2 Tab 2:
  - Dropdown per symbol (only validated symbols)
  - Automation level switch (0/1/2) per symbol, displayed prominently
  - Per-symbol signal statistics panel (Trigger / Found / Pass), live-updated
    from SharedAppState counters with per-symbol and global reset
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
    COLOR_TEXT_SEC,
    ConfirmationDialog,
    fmt_time,
    fmt_r,
)
from live.gui.gui_bridge import SystemBridge


class LiveControlTab(QWidget):
    """Tab 2: Live Control — signals, positions, automation."""

    # Compact neutral button style for the statistics panel (dark theme).
    _RESET_BTN_QSS = f"""
    QPushButton {{
        background-color: #333333;
        color: {COLOR_TEXT};
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 3px 12px;
        min-height: 20px;
        font-weight: normal;
    }}
    QPushButton:hover {{
        background-color: #444444;
        border-color: {COLOR_TEXT_SEC};
    }}
    QPushButton:pressed {{
        background-color: #555555;
    }}
    QPushButton:disabled {{
        background-color: #2a2a2a;
        color: #666666;
        border-color: #3d3d3d;
    }}
    """

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

        # --- Per-symbol signal statistics (Trigger / Found / Pass) ---
        # Counters are recorded per symbol by the signal engine in
        # SharedAppState (signal_stats) — this panel only displays them.
        self._stats_group = QGroupBox("📊 Signal Statistics")
        stats_row = QHBoxLayout(self._stats_group)
        stats_row.setSpacing(12)

        self._stats_label = QLabel("Trigger: — | Found: — | Pass: —")
        self._stats_label.setTextFormat(Qt.TextFormat.RichText)
        stats_row.addWidget(self._stats_label)
        stats_row.addStretch()

        self._reset_stats_btn = QPushButton("Reset Counters")
        self._reset_stats_btn.setToolTip(
            "Clear Trigger / Found / Pass counters for the selected symbol"
        )
        self._reset_stats_btn.setEnabled(False)
        self._reset_stats_btn.clicked.connect(self._on_reset_stats)
        self._reset_stats_btn.setStyleSheet(self._RESET_BTN_QSS)
        stats_row.addWidget(self._reset_stats_btn)

        self._reset_all_stats_btn = QPushButton("Reset All")
        self._reset_all_stats_btn.setToolTip(
            "Clear Trigger / Found / Pass counters for every symbol"
        )
        self._reset_all_stats_btn.setEnabled(False)
        self._reset_all_stats_btn.clicked.connect(self._on_reset_all_stats)
        self._reset_all_stats_btn.setStyleSheet(self._RESET_BTN_QSS)
        stats_row.addWidget(self._reset_all_stats_btn)

        layout.addWidget(self._stats_group)

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
            self._current_symbol = ""

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
        # Statistics panel always refreshes (shows "—" while no symbol is
        # selected / recorded); tables need a valid symbol.
        self._refresh_stats(snap)

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
    # Signal statistics panel (Trigger / Found / Pass)
    # ------------------------------------------------------------------

    @staticmethod
    def _lookup_stats_entry(stats: Dict[str, Any], symbol: str) -> Optional[Dict[str, int]]:
        """Find a symbol's stats entry — exact key first, then case-insensitive.

        Returns ``None`` when the symbol is empty or has never recorded stats.
        """
        if not symbol:
            return None
        entry = stats.get(symbol)
        if entry is not None:
            return entry
        upper = symbol.upper()
        for key, candidate in stats.items():
            if key.upper() == upper:
                return candidate
        return None

    @staticmethod
    def _stats_html(trigger: Optional[int], found: Optional[int], passed: Optional[int]) -> str:
        """Build the dark-theme rich-text stats line.

        Format: ``Trigger: XXXX | Found: XXX | Pass: XX``; ``None`` values
        render as an em dash ("—").
        """
        dash = "—"

        def number(value: Optional[int], color: str) -> str:
            return f'<b style="color:{color};">{dash if value is None else value}</b>'

        sep = f'<span style="color:{COLOR_TEXT_SEC};"> &nbsp;|&nbsp; </span>'
        return (
            f'<span style="color:{COLOR_NEUTRAL};">Trigger: </span>'
            f'{number(trigger, COLOR_TEXT)}'
            + sep
            + f'<span style="color:{COLOR_NEUTRAL};">Found: </span>'
            f'{number(found, COLOR_TEXT)}'
            + sep
            + f'<span style="color:{COLOR_NEUTRAL};">Pass: </span>'
            f'{number(passed, COLOR_POSITIVE)}'
        )

    def _refresh_stats(self, snap: Dict[str, Any]) -> None:
        """Update the per-symbol statistics panel from a snapshot.

        Reads ``snap["signal_stats"]`` — the thread-safe per-symbol counters
        the signal engine records in SharedAppState — for the currently
        selected symbol.  Pure GUI-thread read; nothing here mutates state.
        Runs on every ``refresh_from_state`` timer tick, so the numbers
        update live.
        """
        stats = snap.get("signal_stats", {}) or {}
        symbol = self._current_symbol
        entry = self._lookup_stats_entry(stats, symbol)

        if entry is not None:
            self._stats_label.setText(
                self._stats_html(
                    entry.get("trigger", 0),
                    entry.get("found", 0),
                    entry.get("pass", 0),
                )
            )
        else:
            # No symbol selected, or nothing recorded for it yet.
            self._stats_label.setText(self._stats_html(None, None, None))

        # Per-symbol reset only makes sense while this symbol has counters.
        self._reset_stats_btn.setEnabled(entry is not None)

        # "Reset All" is useful when any counters exist or any validated
        # symbol is present (it is a harmless no-op otherwise).
        registry = snap.get("symbol_registry", {}) or {}
        has_validated = any(
            isinstance(cfg, dict) and cfg.get("status") == "validated"
            for cfg in registry.values()
        )
        self._reset_all_stats_btn.setEnabled(bool(stats) or has_validated)

        # Keep the group title in sync with the selected symbol.
        title = f"📊 Signal Statistics — {symbol}" if symbol else "📊 Signal Statistics"
        if self._stats_group.title() != title:
            self._stats_group.setTitle(title)

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

    def _on_reset_stats(self) -> None:
        """Reset the selected symbol's counters — confirmation required."""
        symbol = self._current_symbol
        if not symbol or symbol == "(no symbols)":
            return
        dialog = ConfirmationDialog(
            title="Reset Signal Counters",
            message=(
                f"Reset <b>Trigger / Found / Pass</b> counters for "
                f"<b>{symbol}</b>?\n\n"
                "Only the displayed statistics are cleared — pending signals, "
                "open positions and automation settings are not affected."
            ),
            confirm_text="✅ RESET",
            parent=self,
        )
        if dialog.exec():
            self._bridge.state.reset_signal_stats(symbol)
            self._bridge.log_action("reset_signal_stats", {"symbol": symbol, "scope": "symbol"})
            self._status_label.setText(f"Signal statistics reset for {symbol}")
            self._refresh_stats(self._bridge.state.get_snapshot())

    def _on_reset_all_stats(self) -> None:
        """Reset counters for every symbol — confirmation required."""
        dialog = ConfirmationDialog(
            title="Reset All Signal Counters",
            message=(
                "Reset <b>Trigger / Found / Pass</b> counters for "
                "<b>ALL symbols</b>?\n\n"
                "Only the displayed statistics are cleared — pending signals, "
                "open positions and automation settings are not affected."
            ),
            confirm_text="✅ RESET ALL",
            parent=self,
        )
        if dialog.exec():
            self._bridge.state.reset_signal_stats()  # symbol=None → every symbol
            self._bridge.log_action("reset_signal_stats", {"symbol": "*", "scope": "all"})
            self._status_label.setText("Signal statistics reset for all symbols")
            self._refresh_stats(self._bridge.state.get_snapshot())