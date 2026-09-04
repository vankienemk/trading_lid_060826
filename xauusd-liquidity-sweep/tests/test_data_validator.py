"""Unit tests for the data engineering layer (loader + validator).

Covers the mandatory validation rules from the guide (section 7.1), MT5 CSV
parsing (tab-separated, angle-bracketed headers, locale numbers), timezone
normalization (broker server time -> UTC with inferred per-day offsets),
Parquet round-trip schema and the data-quality report fields.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.data.loader import (
    infer_utc_offsets,
    load_ohlcv,
    normalize_ohlcv,
    read_parquet,
    save_parquet,
)
from src.data.validator import (
    DAILY_BREAK,
    build_data_quality_report,
    detect_gaps,
    validate_ohlcv,
)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def make_valid_frame(n: int = 100, start: str = "2023-01-02 01:00") -> pd.DataFrame:
    """A clean, sorted, non-duplicated OHLCV frame (timestamp-indexed)."""
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC", name="timestamp")
    rng = np.random.default_rng(42)
    base = 2000.0
    open_ = base + np.cumsum(rng.normal(0, 0.5, n))
    close = open_ + rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.2, n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.2, n))
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 2000, n).astype("float64"),
        },
        index=idx,
    )


def mt5_csv_text(rows: list[str]) -> str:
    """Wrap data rows with the MT5-style tab-separated, angle-bracketed header."""
    header = "\t".join(
        f"<{c}>" for c in ["DATE", "TIME", "OPEN", "HIGH", "LOW", "CLOSE", "TICKVOL", "VOL", "SPREAD"]
    )
    return header + "\n" + "\n".join(rows)


# --------------------------------------------------------------------------
# validate_ohlcv — mandatory rules (guide section 7.1)
# --------------------------------------------------------------------------


def test_validate_ohlcv_ok_on_valid_frame():
    df = make_valid_frame()
    validate_ohlcv(df)  # must not raise
    # Also works when timestamp is a plain column instead of an index.
    col_form = df.reset_index()
    validate_ohlcv(col_form)


def test_validate_ohlcv_missing_columns():
    df = make_valid_frame().drop(columns=["volume"])
    with pytest.raises(ValueError, match="Missing columns"):
        validate_ohlcv(df)


def test_validate_ohlcv_duplicate_timestamps():
    df = make_valid_frame()
    dup = pd.concat([df.iloc[:5], df.iloc[4:10]])
    with pytest.raises(ValueError, match="Duplicate timestamps"):
        validate_ohlcv(dup)


def test_validate_ohlcv_unsorted_timestamps():
    df = make_valid_frame().iloc[::-1]
    with pytest.raises(ValueError, match="sorted"):
        validate_ohlcv(df)


def test_validate_ohlcv_null_ohlc():
    df = make_valid_frame()
    df.loc[df.index[3], "high"] = np.nan
    with pytest.raises(ValueError, match="Null OHLC"):
        validate_ohlcv(df)


def test_validate_ohlcv_invalid_high():
    df = make_valid_frame()
    # Overwrite one row with a clean, explicit violation: high below the body
    # but still above low (so only the high rule is violated).
    idx = df.index[3]
    df.loc[idx, ["open", "close"]] = 100.0
    df.loc[idx, "low"] = 90.0
    df.loc[idx, "high"] = 95.0
    with pytest.raises(ValueError, match="Invalid high"):
        validate_ohlcv(df)


def test_validate_ohlcv_invalid_low():
    df = make_valid_frame()
    # low above the body but still below high (only the low rule violated).
    idx = df.index[3]
    df.loc[idx, ["open", "close"]] = 100.0
    df.loc[idx, "high"] = 110.0
    df.loc[idx, "low"] = 105.0
    with pytest.raises(ValueError, match="Invalid low"):
        validate_ohlcv(df)


def test_validate_ohlcv_high_below_low():
    df = make_valid_frame()
    # high strictly below low: the fundamental range invariant is broken.
    idx = df.index[3]
    df.loc[idx, ["open", "close"]] = 100.0
    df.loc[idx, "low"] = 105.0
    df.loc[idx, "high"] = 95.0
    with pytest.raises(ValueError, match="High below low"):
        validate_ohlcv(df)


# --------------------------------------------------------------------------
# detect_gaps
# --------------------------------------------------------------------------


def _series_from_deltas(first: str, deltas_min: list[int]) -> pd.Series:
    t0 = pd.Timestamp(first)
    times = [t0]
    for m in deltas_min:
        times.append(times[-1] + pd.Timedelta(minutes=m))
    return pd.Series(times)


def test_detect_gaps_regular_spacing_returns_empty():
    ts = _series_from_deltas("2023-01-02 01:00", [15] * 20)
    gaps = detect_gaps(ts)
    assert gaps.empty


def test_detect_gaps_daily_break_and_weekend():
    # Friday 23:45 -> Saturday 01:00? No: daily break is 23:45 -> 01:00 next
    # day (75 min, rollover).  Then a weekend Thursday/Friday span.
    ts = _series_from_deltas(
        "2023-01-05 23:00", [15, 15, 15, 75, 15, 15, 15, 15]  # Thu->Fri break
    )
    gaps = detect_gaps(ts)
    assert len(gaps) == 1
    assert gaps.iloc[0]["kind"] == "daily_break"
    assert gaps.iloc[0]["gap"] == DAILY_BREAK


def test_detect_gaps_weekend_friday_to_monday():
    # Friday 2023-01-06 23:45 -> Monday 2023-01-09 01:00.
    ts = pd.Series(
        [pd.Timestamp("2023-01-06 23:45"), pd.Timestamp("2023-01-09 01:00")]
    )
    gaps = detect_gaps(ts)
    assert len(gaps) == 1
    assert gaps.iloc[0]["kind"] == "weekend"


def test_detect_gaps_holiday_multiday():
    # 2022-12-23 (Fri) 23:45 -> 2022-12-27 (Tue) 01:00 = Christmas break.
    ts = pd.Series(
        [pd.Timestamp("2022-12-23 23:45"), pd.Timestamp("2022-12-27 01:00")]
    )
    gaps = detect_gaps(ts)
    assert len(gaps) == 1
    assert gaps.iloc[0]["kind"] == "holiday"


def test_detect_gaps_short_gap_intraday():
    # A midday 30-min hole: same-day short gap.
    ts = _series_from_deltas("2023-01-02 14:00", [15, 15, 30, 15, 15])
    gaps = detect_gaps(ts)
    assert len(gaps) == 1
    assert gaps.iloc[0]["kind"] == "short_gap"


def test_detect_gaps_sorts_input():
    ts = pd.Series(
        [pd.Timestamp("2023-01-06 23:45"), pd.Timestamp("2023-01-09 01:00"),
         pd.Timestamp("2023-01-06 23:15")]
    )
    gaps = detect_gaps(ts)
    assert len(gaps) == 2  # 23:45->Mon (weekend), 23:15->23:45 (daily gap? no)
    kinds = set(gaps["kind"])
    assert "weekend" in kinds


# --------------------------------------------------------------------------
# MT5 CSV parsing + normalization
# --------------------------------------------------------------------------


@pytest.fixture
def mt5_csv(tmp_path):
    text = mt5_csv_text(
        [
            "2023.01.02\t01:00:00\t2000.00\t2001.00\t1999.00\t2000.50\t100\t0\t3",
            "2023.01.02\t01:15:00\t2000.50\t2002.00\t2000.25\t2001.75\t150\t0\t2",
            "2023.01.02\t01:30:00\t2001.75\t2001.90\t2000.80\t2001.10\t90\t0\t2",
        ]
    )
    p = tmp_path / "mt5.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_ohlcv_parses_mt5_columns(mt5_csv):
    df = load_ohlcv(str(mt5_csv))
    assert list(df.columns) == [
        "timestamp", "open", "high", "low", "close",
        "real_volume", "tick_volume", "spread",
    ]
    assert len(df) == 3
    assert df["open"].tolist() == [2000.0, 2000.5, 2001.75]
    assert df["tick_volume"].tolist() == [100.0, 150.0, 90.0]
    assert df["real_volume"].tolist() == [0.0, 0.0, 0.0]
    assert df["spread"].tolist() == [3.0, 2.0, 2.0]
    assert df["timestamp"].iloc[0] == pd.Timestamp("2023-01-02 01:00:00")


def test_load_ohlcv_locale_comma_decimal(tmp_path):
    # EU locale: comma as decimal separator, dot as thousands separator.
    text = mt5_csv_text(
        [
            "2023.01.02\t01:00:00\t2000,00\t2001,50\t1999,00\t2000,25\t1234\t0\t3",
        ]
    )
    p = tmp_path / "loc_comma.csv"
    p.write_text(text, encoding="utf-8")
    df = load_ohlcv(str(p))
    assert df["open"].iloc[0] == 2000.0
    assert df["high"].iloc[0] == 2001.5
    assert df["low"].iloc[0] == 1999.0
    assert df["close"].iloc[0] == 2000.25
    assert df["tick_volume"].iloc[0] == 1234.0


def test_load_ohlcv_locale_dot_thousands(tmp_path):
    # US locale: dot decimal; comma thousands separator on volume.
    text = mt5_csv_text(
        [
            "2023.01.02\t01:00:00\t2000.00\t2001.50\t1999.00\t2000.25\t1,234\t0\t3",
        ]
    )
    p = tmp_path / "loc_dot.csv"
    p.write_text(text, encoding="utf-8")
    df = load_ohlcv(str(p))
    assert df["open"].iloc[0] == 2000.0
    assert df["high"].iloc[0] == 2001.5
    assert df["tick_volume"].iloc[0] == 1234.0


def test_normalize_ohlcv_schema_and_volume(mt5_csv):
    raw = load_ohlcv(str(mt5_csv))
    df = normalize_ohlcv(raw, volume_kind="tick")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index.name == "timestamp"
    assert str(df.index.dtype) == "datetime64[ns, UTC]"
    assert all(df.dtypes == "float64")
    assert df["volume"].tolist() == [100.0, 150.0, 90.0]  # TICKVOL, not VOL


def test_normalize_ohlcv_real_volume_selection(mt5_csv):
    raw = load_ohlcv(str(mt5_csv))
    df = normalize_ohlcv(raw, volume_kind="real")
    assert df["volume"].tolist() == [0.0, 0.0, 0.0]


def test_normalize_ohlcv_sorts_and_dedups(mt5_csv):
    text = mt5_csv_text(
        [
            "2023.01.02\t01:30:00\t2001.75\t2001.90\t2000.80\t2001.10\t90\t0\t2",
            "2023.01.02\t01:00:00\t2000.00\t2001.00\t1999.00\t2000.50\t100\t0\t3",
            "2023.01.02\t01:00:00\t2000.00\t2001.00\t1999.00\t2000.50\t100\t0\t3",
            "2023.01.02\t01:15:00\t2000.50\t2002.00\t2000.25\t2001.75\t150\t0\t2",
        ]
    )
    p = mt5_csv.parent / "dup.csv"
    p.write_text(text, encoding="utf-8")
    df = normalize_ohlcv(load_ohlcv(str(p)))
    assert len(df) == 3
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()


def test_normalize_ohlcv_utc_offset_zero_keeps_time(mt5_csv):
    raw = load_ohlcv(str(mt5_csv))
    df = normalize_ohlcv(raw, utc_offset_hours=0)
    assert df.index[0] == pd.Timestamp("2023-01-02 01:00", tz="UTC")


def test_infer_utc_offsets_hybrid_regime():
    # Two days: one opening at 01:00 (UTC+3), one at 00:00 (UTC+2).
    ts = pd.Series(
        [
            pd.Timestamp("2023-06-01 01:00"),  # summer-style day start
            pd.Timestamp("2023-06-01 01:15"),
            pd.Timestamp("2023-11-01 00:00"),  # winter-style day start
            pd.Timestamp("2023-11-01 00:15"),
        ]
    )
    offsets = infer_utc_offsets(ts)
    assert offsets.loc[pd.Timestamp("2023-06-01")] == 3.0
    assert offsets.loc[pd.Timestamp("2023-11-01")] == 2.0


def test_infer_utc_offsets_partial_day_inherits():
    # First day starts 03:15 (partial) -> inherits the next day's offset.
    ts = pd.Series(
        [
            pd.Timestamp("2023-06-01 03:15"),
            pd.Timestamp("2023-06-01 03:30"),
            pd.Timestamp("2023-06-02 01:00"),
            pd.Timestamp("2023-06-02 01:15"),
        ]
    )
    offsets = infer_utc_offsets(ts)
    assert offsets.loc[pd.Timestamp("2023-06-01")] == 3.0
    assert offsets.loc[pd.Timestamp("2023-06-02")] == 3.0


def test_normalize_ohlcv_auto_converts_to_utc():
    # Server 01:00 with +3 offset -> 22:00 previous UTC day.
    ts = pd.Series([pd.Timestamp("2023-06-01 01:00"), pd.Timestamp("2023-06-01 01:15")])
    raw = pd.DataFrame(
        {
            "timestamp": ts,
            "open": [1.0, 1.0],
            "high": [2.0, 2.0],
            "low": [0.5, 0.5],
            "close": [1.5, 1.5],
            "tick_volume": [10.0, 10.0],
        }
    )
    df = normalize_ohlcv(raw)
    assert df.index[0] == pd.Timestamp("2023-05-31 22:00", tz="UTC")


# --------------------------------------------------------------------------
# Parquet round-trip
# --------------------------------------------------------------------------


def test_parquet_roundtrip_schema(tmp_path):
    df = make_valid_frame(50)
    p = tmp_path / "out.parquet"
    save_parquet(df, str(p))
    back = read_parquet(str(p))
    assert list(back.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert str(back["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert {str(dt) for dt in back.dtypes[1:]} == {"float64"}
    assert len(back) == 50
    pd.testing.assert_frame_equal(df.reset_index(), back, check_dtype=True)


# --------------------------------------------------------------------------
# Data-quality report
# --------------------------------------------------------------------------


def test_report_mandatory_fields_and_volume_type(mt5_csv):
    raw = load_ohlcv(str(mt5_csv))
    df = normalize_ohlcv(raw, volume_kind="tick", utc_offset_hours=0)
    report = build_data_quality_report(
        df, "tick", "UTC", raw=raw, utc_offsets=None
    )
    for field in [
        "row_count", "start_time", "end_time", "duplicate_count",
        "missing_value_count", "invalid_ohlc_count", "suspected_gap_count",
        "zero_volume_count",
    ]:
        assert field in report, field
    assert report["row_count"] == 3
    assert report["duplicate_count"] == 0
    assert report["missing_value_count"] == 0
    assert report["invalid_ohlc_count"] == 0
    assert report["suspected_gap_count"] == 0  # regular 15-min spacing
    assert report["volume_type"] == "tick"
    assert report["zero_volume_count"] == 0
    assert report["schema"]["timestamp"] == "datetime64[ns, UTC]"
    assert report["spread"]["zero_count"] == 0
    assert report["spread"]["mean"] == pytest.approx(7 / 3, abs=0.001)


def test_report_with_utc_offsets_documents_timezone():
    # Two server-time days with a real 75-min daily rollover break:
    # day 1 opens 01:00 (UTC+3), breaks 23:45 -> 01:00, day 2 opens 01:00.
    server_ts = pd.Series(
        [
            pd.Timestamp("2023-06-01 01:00"),
            pd.Timestamp("2023-06-01 01:15"),
            pd.Timestamp("2023-06-01 23:45"),
            pd.Timestamp("2023-06-02 01:00"),
            pd.Timestamp("2023-06-02 01:15"),
        ]
    )
    raw = pd.DataFrame(
        {
            "timestamp": server_ts,
            "open": [2000.0] * 5,
            "high": [2002.0] * 5,
            "low": [1998.0] * 5,
            "close": [2001.0] * 5,
            "tick_volume": [100.0] * 5,
            "real_volume": [0.0] * 5,
            "spread": [2.0] * 5,
        }
    )
    df = normalize_ohlcv(raw)
    offsets = infer_utc_offsets(raw["timestamp"])
    report = build_data_quality_report(df, "tick", "UTC", raw=raw, utc_offsets=offsets)
    tz = report["timezone"]
    assert tz["raw_timestamps"] == "broker server time (not UTC)"
    assert tz["start_time_server"] == "2023-06-01 01:00:00"
    assert tz["offset_hours_histogram"] == {"3.0": 2}
    assert tz["utc_break_hour_histogram"] == {"20": 1}  # break lands 20:45 UTC
    assert "validation" in tz
    # Converted UTC start: server 01:00 - 3h = 22:00 previous UTC day.
    assert df.index[0] == pd.Timestamp("2023-05-31 22:00", tz="UTC")


def test_report_zero_volume_and_real_volume_all_zero(tmp_path):
    text = mt5_csv_text(
        [
            "2023.01.02\t01:00:00\t2000.00\t2001.00\t1999.00\t2000.50\t0\t0\t3",
            "2023.01.02\t01:15:00\t2000.50\t2002.00\t2000.25\t2001.75\t0\t0\t2",
        ]
    )
    p = tmp_path / "zero.csv"
    p.write_text(text, encoding="utf-8")
    raw = load_ohlcv(str(p))
    df = normalize_ohlcv(raw)
    report = build_data_quality_report(df, "tick", "UTC", raw=raw)
    assert report["zero_volume_count"] == 2
    assert report["real_volume_all_zero"] is True


def test_report_serializable_json():
    df = make_valid_frame(20)
    report = build_data_quality_report(df, "tick", "UTC")
    import json

    json.dumps(report)  # must not raise
    assert report["schema"]["open"] == "float64"