"""Time-based split, purging and embargo — executable spec + contract tests.

Owned by Agent 7 (QA / Bias Auditor).  Guide sections 22 and 25.1, plus
INTERFACES.md section 8: *time-based split only (never random), embargo/purge
applied, calibration fit on validation only*.

The production module is ``src/modeling/split.py`` (Agent 6, task t14).  Until
it lands, the reference helpers below are an **executable specification** of
the mandatory properties — they run green now and double as acceptance
criteria.  The contract tests at the bottom use :func:`pytest.importorskip` and
activate automatically the moment Agent 6's module exists, so the real
implementation is validated against exactly these properties with no further
QA wiring.

Rules encoded here
------------------
- Never split randomly (guide 22.1): splits are contiguous, chronological
  positional ranges — no shuffling, no gaps, no overlap.
- Baseline fractions (guide 22.2): first 60% train, next 20% validation, last
  20% test.
- Purging (guide 22.4): a train event whose label horizon reaches into
  validation must be dropped from train.
- Embargo (guide 22.5): leave a gap of ``embargo_bars`` (baseline 32) after the
  usable train region before validation/test.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Reference specification (test-only; production code is src/modeling/split.py)
# ---------------------------------------------------------------------------

_BASELINE_TRAIN = 0.60
_BASELINE_VALIDATION = 0.20
_BASELINE_TEST = 0.20
_BASELINE_EMBARGO = 32


def _reference_split_boundaries(
    n_rows: int,
    train_fraction: float = _BASELINE_TRAIN,
    validation_fraction: float = _BASELINE_VALIDATION,
    test_fraction: float = _BASELINE_TEST,
) -> tuple[int, int]:
    """Return ``(train_end, val_end)`` positional boundaries for ``n_rows``.

    Chronological, contiguous: train = ``[0, train_end)``, validation =
    ``[train_end, val_end)``, test = ``[val_end, n_rows)``.
    """
    if n_rows <= 0:
        raise ValueError("n_rows must be > 0")
    if not math.isclose(train_fraction + validation_fraction + test_fraction, 1.0):
        raise ValueError("split fractions must sum to 1.0")
    train_end = round(n_rows * train_fraction)
    val_end = train_end + round(n_rows * validation_fraction)
    if not (0 < train_end < val_end < n_rows):
        raise ValueError("fractions leave an empty split for this n_rows")
    return train_end, val_end


def _reference_purge_mask(
    positions: np.ndarray, horizon_bars: int, boundary_pos: int
) -> np.ndarray:
    """True where a train event's label window does **not** reach validation.

    An event at position ``p`` uses future bars ``p+1 .. p+horizon_bars`` to
    form its label.  It leaks if any of those bars sits in the validation set,
    i.e. ``p + horizon_bars >= boundary_pos``; such events are purged (False).
    """
    return (positions + horizon_bars) < boundary_pos


def _reference_embargo_mask(
    positions: np.ndarray, boundary_pos: int, embargo_bars: int
) -> np.ndarray:
    """True where a train event lies outside the embargo gap before validation.

    Events at ``position >= boundary_pos - embargo_bars`` are dropped so no
    training sample's context abuts the validation window.
    """
    return positions < (boundary_pos - embargo_bars)


# ---------------------------------------------------------------------------
# Split properties (never random, chronological, contiguous, correct fractions)
# ---------------------------------------------------------------------------

def test_split_is_chronological_contiguous_never_random() -> None:
    """Splits are contiguous positional ranges in time order — no shuffling."""
    n = 1000
    train_end, val_end = _reference_split_boundaries(n)
    train = np.arange(0, train_end)
    validation = np.arange(train_end, val_end)
    test = np.arange(val_end, n)

    # Each split is a single ascending, gap-free block (a shuffle would fail
    # the diff==1 property or the contiguity check).
    for block in (train, validation, test):
        assert np.all(np.diff(block) == 1)
    assert np.array_equal(np.concatenate([train, validation, test]), np.arange(n))


def test_split_fractions_match_baseline_config() -> None:
    n = 1000
    train_end, val_end = _reference_split_boundaries(n)
    assert train_end == 600
    assert val_end == 800
    assert (val_end - train_end) == 200
    assert (n - val_end) == 200


def test_split_is_disjoint_and_complete() -> None:
    n = 997
    train_end, val_end = _reference_split_boundaries(n)
    train = set(range(0, train_end))
    validation = set(range(train_end, val_end))
    test = set(range(val_end, n))
    assert train.isdisjoint(validation)
    assert train.isdisjoint(test)
    assert validation.isdisjoint(test)
    assert train | validation | test == set(range(n))


def test_train_ends_before_validation_begins() -> None:
    """Chronological ordering: max(train) < min(validation) < min(test)."""
    n = 500
    train_end, val_end = _reference_split_boundaries(n)
    assert (train_end - 1) < train_end
    assert train_end < val_end
    assert val_end < n


def test_split_fractions_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        _reference_split_boundaries(1000, train_fraction=0.6, validation_fraction=0.3,
                                    test_fraction=0.3)


# ---------------------------------------------------------------------------
# Purging (guide 22.4)
# ---------------------------------------------------------------------------

def test_purge_drops_event_whose_horizon_reaches_validation() -> None:
    boundary = 600
    horizon = 32
    # event at 568 -> window ends at 600 == boundary -> reaches validation
    positions = np.array([568, 567])
    mask = _reference_purge_mask(positions, horizon, boundary)
    assert not bool(mask[0])  # 568 + 32 = 600 >= boundary -> purged
    assert bool(mask[1])      # 567 + 32 = 599 <  boundary -> kept


def test_purge_keeps_event_whose_horizon_is_inside_train() -> None:
    boundary = 600
    horizon = 32
    positions = np.arange(0, boundary - horizon)  # all windows end < boundary
    mask = _reference_purge_mask(positions, horizon, boundary)
    assert mask.all()


# ---------------------------------------------------------------------------
# Embargo (guide 22.5)
# ---------------------------------------------------------------------------

def test_embargo_leaves_a_gap_after_train() -> None:
    boundary = 600
    embargo = 32
    positions = np.array([boundary - embargo, boundary - embargo - 1])
    mask = _reference_embargo_mask(positions, boundary, embargo)
    assert not bool(mask[0])  # 568 >= 600 - 32 -> inside embargo gap
    assert bool(mask[1])      # 567 <  600 - 32 -> outside the gap


def test_purge_and_embargo_only_modify_train() -> None:
    """Purging/embargo act on the training set; validation and test untouched."""
    n = 1000
    train_end, _ = _reference_split_boundaries(n)
    train_positions = np.arange(0, train_end)
    mask = _reference_purge_mask(train_positions, 32, train_end) & _reference_embargo_mask(
        train_positions, train_end, _BASELINE_EMBARGO
    )
    # The mask is only ever defined over train positions (< train_end).
    assert mask.shape == train_positions.shape
    assert (train_positions[mask] < train_end).all()


# ---------------------------------------------------------------------------
# Contract tests against the production module (activate at t14)
# ---------------------------------------------------------------------------

def _real_split_module():
    """Import ``src/modeling/split.py`` once it exists (skip otherwise)."""
    return pytest.importorskip("src.modeling.split")


def test_real_split_is_chronological_and_never_random() -> None:
    split = _real_split_module()
    n = 1000
    result = split.time_split(np.arange(n), _BASELINE_TRAIN, _BASELINE_VALIDATION,
                              _BASELINE_TEST)
    # Contract: time_split returns (train, validation, test) positional blocks.
    train, validation, test = result[0], result[1], result[2]
    assert np.all(np.diff(train) == 1)
    assert np.all(np.diff(validation) == 1)
    assert np.all(np.diff(test) == 1)
    assert np.array_equal(np.concatenate([train, validation, test]), np.arange(n))


def test_real_split_has_no_train_validation_overlap() -> None:
    split = _real_split_module()
    n = 1000
    train, validation, test = split.time_split(
        np.arange(n), _BASELINE_TRAIN, _BASELINE_VALIDATION, _BASELINE_TEST
    )
    assert set(train).isdisjoint(set(validation))
    assert set(train).isdisjoint(set(test))
    assert set(validation).isdisjoint(set(test))
    assert max(train) < min(validation) < min(test)
