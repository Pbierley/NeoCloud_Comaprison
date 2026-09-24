"""
Offline test for yfinance_data.py's resampling logic. Doesn't touch the
network or the yfinance package at all - _resample_to_quarterly is the pure
part of fetch_shares_outstanding_yf, taking a plain pandas Series shaped
like what yfinance's Ticker.get_shares_full() returns (datetime index ->
share count) and is fully testable without a live request.

Run: python tests/test_yfinance_data.py
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yfinance_data import _resample_to_quarterly, _row_to_quarterly_df


def test_resample_to_quarterly_picks_last_value_per_quarter():
    # Yahoo reports shares outstanding on irregular dates (filing-driven),
    # sometimes more than once per quarter - the resample should keep only
    # the LAST (most recent) value within each calendar quarter.
    idx = pd.to_datetime(
        ["2025-01-15", "2025-02-20", "2025-04-10", "2025-06-30", "2025-07-05"]
    )
    series = pd.Series([500_000_000, 510_000_000, 520_000_000, 525_000_000, 530_000_000], index=idx)

    df = _resample_to_quarterly(series)

    assert list(df["quarter_label"]) == ["2025Q1", "2025Q2", "2025Q3"]

    def val(label):
        return df.loc[df["quarter_label"] == label, "value"].iloc[0]

    # Q1 2025: last of the two Jan/Feb points.
    assert val("2025Q1") == 510_000_000
    # Q2 2025: only one point (Apr) plus the Jun 30 point - last one wins.
    assert val("2025Q2") == 525_000_000
    # Q3 2025: single point.
    assert val("2025Q3") == 530_000_000

    assert df["period_end"].is_monotonic_increasing

    print("yfinance shares resample checks passed.")
    print(df.to_string(index=False))


def test_resample_to_quarterly_handles_empty_input():
    empty = pd.Series(dtype=float)
    df = _resample_to_quarterly(empty)
    assert df.empty
    assert list(df.columns) == ["period_end", "quarter_label", "value"]

    assert _resample_to_quarterly(None).empty

    print("yfinance shares resample empty-input check passed.")


def test_row_to_quarterly_df_drops_nan_and_sorts():
    """
    _row_to_quarterly_df is the pure core of fetch_quarterly_metric_yf: a
    single row from yfinance's quarterly_income_stmt (columns = quarter-end
    Timestamps, most-recent-first, as yfinance actually orders them) turned
    into the app's standard period_end/quarter_label/value shape. A quarter
    yfinance didn't report for this line item (NaN) should be dropped
    rather than shown as a fake zero.
    """
    # yfinance returns columns newest-first - deliberately out of order here
    # to confirm the function sorts by period_end, not input order.
    idx = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
    row = pd.Series([2575000000.0, 2200000000.0, float("nan"), 1360000000.0], index=idx)

    df = _row_to_quarterly_df(row)

    # The NaN quarter (2025-12-31, e.g. a 10-K period yfinance didn't carry
    # a quarterly figure for) should be dropped, not zeroed.
    assert len(df) == 3, f"expected 3 non-NaN quarters, got {len(df)}\n{df}"
    assert not (df["period_end"] == pd.Timestamp("2025-12-31")).any()

    assert list(df["quarter_label"]) == ["2025Q3", "2026Q1", "2026Q2"]
    assert df["period_end"].is_monotonic_increasing

    def val(label):
        return df.loc[df["quarter_label"] == label, "value"].iloc[0]

    assert val("2026Q2") == 2575000000.0
    assert val("2025Q3") == 1360000000.0

    print("yfinance row-to-quarterly-df checks passed.")
    print(df.to_string(index=False))


def test_row_to_quarterly_df_handles_empty_row():
    empty = pd.Series(dtype=float)
    df = _row_to_quarterly_df(empty)
    assert df.empty
    assert list(df.columns) == ["period_end", "quarter_label", "value"]

    all_nan = pd.Series([float("nan"), float("nan")], index=pd.to_datetime(["2026-03-31", "2026-06-30"]))
    assert _row_to_quarterly_df(all_nan).empty

    assert _row_to_quarterly_df(None).empty

    print("yfinance row-to-quarterly-df empty-input check passed.")


if __name__ == "__main__":
    test_resample_to_quarterly_picks_last_value_per_quarter()
    test_resample_to_quarterly_handles_empty_input()
    test_row_to_quarterly_df_drops_nan_and_sorts()
    test_row_to_quarterly_df_handles_empty_row()
