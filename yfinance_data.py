"""
Optional Yahoo Finance data source, used to SUPPLEMENT (not replace) SEC EDGAR.

Why this exists: SEC's XBRL data has no clean point-in-time "shares
outstanding as of this date" figure for CoreWeave. Its multiple stock
classes (A/B/C) are each tagged separately with a dimensional member rather
than as one combined non-dimensional total, so the only combined series
SEC's simple companyconcept API exposes is the WEIGHTED-AVERAGE basic share
count used for EPS (see config.py's note on "shares_outstanding") - not an
actual snapshot count. Yahoo Finance publishes a real historical
shares-outstanding series, so it's used here to fill that specific gap.

This is deliberately kept separate from sec_edgar.py: it's a different data
provider, unauthenticated and not SEC-structured, with its own reliability
and rate-limit characteristics. Every function here catches its own
exceptions and returns None on any failure (yfinance not installed, ticker
not found, network hiccup, yfinance API change) - a problem here should
never take down the rest of the dashboard, just mean that one supplemental
line doesn't show up.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _resample_to_quarterly(shares_series: "pd.Series") -> pd.DataFrame:
    """
    Pure, easily-testable core of fetch_shares_outstanding_yf: takes a
    datetime-indexed Series of share counts (as yfinance's get_shares_full
    returns) and collapses it to one snapshot per calendar quarter - the
    last (most recent) reported value within that quarter - mirroring how
    the SEC-derived balance-sheet charts treat a quarter's "as of" date.

    Returns columns: period_end (quarter-end timestamp), quarter_label, value.
    """
    if shares_series is None or shares_series.empty:
        return pd.DataFrame(columns=["period_end", "quarter_label", "value"])

    s = shares_series.sort_index()
    if getattr(s.index, "tz", None) is not None:
        s.index = s.index.tz_localize(None)

    quarterly = s.resample("QE").last().dropna()
    if quarterly.empty:
        return pd.DataFrame(columns=["period_end", "quarter_label", "value"])

    df = quarterly.reset_index()
    df.columns = ["period_end", "value"]
    df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)
    return df[["period_end", "quarter_label", "value"]]


def _row_to_quarterly_df(row: "pd.Series") -> pd.DataFrame:
    """
    Pure/testable core of fetch_quarterly_metric_yf: yfinance's
    quarterly_income_stmt is a DataFrame with one row per line item and one
    column per reported quarter-end (a Timestamp), most recent first. This
    turns a single row (one metric) into the same period_end/quarter_label/
    value shape used throughout the app, dropping any quarter where that
    metric wasn't reported (NaN) rather than showing a fake zero.
    """
    if row is None or row.empty:
        return pd.DataFrame(columns=["period_end", "quarter_label", "value"])

    s = row.dropna()
    if s.empty:
        return pd.DataFrame(columns=["period_end", "quarter_label", "value"])

    df = s.reset_index()
    df.columns = ["period_end", "value"]
    df["period_end"] = pd.to_datetime(df["period_end"])
    if getattr(df["period_end"].dt, "tz", None) is not None:
        df["period_end"] = df["period_end"].dt.tz_localize(None)
    df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)
    df = df.sort_values("period_end").reset_index(drop=True)
    return df[["period_end", "quarter_label", "value"]]


def fetch_quarterly_metric_yf(ticker: str, row_candidates: list) -> Optional[pd.DataFrame]:
    """
    One line item (revenue, net income, EPS, ...) from Yahoo Finance's
    quarterly income statement, meant as a gap-filler for filers where SEC
    has NO quarterly XBRL data at all - e.g. foreign private issuers like
    Nebius, which file an annual 20-F and 6-Ks instead of a 10-K/10-Q, so
    SEC's API never has a discrete-quarter figure for them to fall back on.

    row_candidates: label(s) to try against the statement's row index, in
    order, since yfinance's exact row naming has shifted across versions
    (e.g. "Total Revenue" vs "Operating Revenue") - the first one present
    wins.

    IMPORTANT CAVEAT: unlike SEC's XBRL archive (which goes back years),
    yfinance's quarterly_income_stmt typically only exposes a handful of the
    MOST RECENT quarters. This fills the gap where SEC has nothing, it does
    not reproduce the same depth of history the SEC-derived charts have
    elsewhere in this app.

    Never raises - returns None on any failure (yfinance not installed,
    ticker not found, network issue, no matching row).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        stmt = yf.Ticker(ticker).quarterly_income_stmt
    except Exception:
        return None

    if stmt is None or stmt.empty:
        return None

    for name in row_candidates:
        if name in stmt.index:
            df = _row_to_quarterly_df(stmt.loc[name])
            if not df.empty:
                return df
    return None


def fetch_quarterly_cashflow_metric_yf(ticker: str, row_candidates: list) -> Optional[pd.DataFrame]:
    """
    Same pattern as fetch_quarterly_metric_yf, but reads Yahoo Finance's
    quarterly CASH FLOW statement instead of the income statement - used for
    operating cash flow / capex, neither of which lives on the income
    statement. Same never-raises, trailing-quarters-only caveats apply (see
    fetch_quarterly_metric_yf's docstring).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        stmt = yf.Ticker(ticker).quarterly_cashflow
    except Exception:
        return None

    if stmt is None or stmt.empty:
        return None

    for name in row_candidates:
        if name in stmt.index:
            df = _row_to_quarterly_df(stmt.loc[name])
            if not df.empty:
                return df
    return None


def fetch_operating_cash_flow_yf(ticker: str) -> Optional[pd.DataFrame]:
    return fetch_quarterly_cashflow_metric_yf(
        ticker, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]
    )


def fetch_capex_yf(ticker: str) -> Optional[pd.DataFrame]:
    """
    Capital expenditure. Yahoo Finance reports this as a NEGATIVE cash
    outflow on the cash flow statement; the rest of this app (SEC's
    PaymentsToAcquirePropertyPlantAndEquipment tag, and compute_fcf_df,
    which does ocf - capex) treats capex as a positive magnitude, so the
    sign is flipped here to match before it's returned.
    """
    df = fetch_quarterly_cashflow_metric_yf(ticker, ["Capital Expenditure", "Capital Expenditure Reported"])
    if df is None:
        return None
    df = df.copy()
    df["value"] = df["value"].abs()
    return df


def fetch_revenue_yf(ticker: str) -> Optional[pd.DataFrame]:
    return fetch_quarterly_metric_yf(ticker, ["Total Revenue", "Operating Revenue"])


def fetch_net_income_yf(ticker: str) -> Optional[pd.DataFrame]:
    return fetch_quarterly_metric_yf(
        ticker,
        ["Net Income", "Net Income Common Stockholders", "Net Income Including Noncontrolling Interests"],
    )


def fetch_eps_basic_yf(ticker: str) -> Optional[pd.DataFrame]:
    return fetch_quarterly_metric_yf(ticker, ["Basic EPS"])


def fetch_eps_diluted_yf(ticker: str) -> Optional[pd.DataFrame]:
    return fetch_quarterly_metric_yf(ticker, ["Diluted EPS"])


def fetch_market_cap_yf(ticker: str) -> Optional[float]:
    """
    Current market capitalization (shares outstanding x current share
    price). Unlike every other function in this app, this is a LIVE
    snapshot - it reflects whatever moment it happened to be fetched, not a
    specific SEC filing date - so it's only ever used for a point-in-time
    "right now" comparison (e.g. market cap vs. RPO), never treated as part
    of a quarterly time series the way everything else here is.

    Tries a few different yfinance access patterns (fast_info's key naming
    has shifted across versions, and older versions need the slower .info
    dict instead) before giving up. Never raises - returns None on any
    failure (yfinance not installed, ticker not found, network issue, the
    field just isn't populated for this ticker).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        t = yf.Ticker(ticker)
    except Exception:
        return None

    for accessor in (
        lambda: t.fast_info["marketCap"],
        lambda: t.fast_info["market_cap"],
        lambda: t.info.get("marketCap"),
    ):
        try:
            value = accessor()
        except Exception:
            continue
        if value:
            return float(value)
    return None


def fetch_ebitda_yf(ticker: str) -> Optional[float]:
    """
    Trailing-twelve-months EBITDA, straight from Yahoo Finance's `info`
    dict. This is TRAILING, not forward - there is no `forwardEbitda` field
    in yfinance (see config.py's note on Forward EBITDA for why). Used only
    as a fallback trailing-EBITDA source when SEC has neither operating
    income nor D&A data at all for a company (e.g. Nebius).

    Never raises - returns None on any failure (yfinance not installed,
    ticker not found, network issue, field not populated - common for
    thinly-covered/recently-IPO'd tickers).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        value = yf.Ticker(ticker).info.get("ebitda")
    except Exception:
        return None
    return float(value) if value else None


def fetch_ttm_revenue_yf(ticker: str) -> Optional[float]:
    """
    Trailing-twelve-months total revenue from Yahoo Finance's `info` dict -
    paired with fetch_ebitda_yf to compute a trailing EBITDA margin when SEC
    has no usable quarterly data to build one from directly.

    Never raises - returns None on any failure, same caveats as
    fetch_ebitda_yf.
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        value = yf.Ticker(ticker).info.get("totalRevenue")
    except Exception:
        return None
    return float(value) if value else None


def fetch_forward_revenue_estimate_yf(ticker: str) -> Optional[dict]:
    """
    Next-fiscal-year consensus analyst REVENUE estimate (yfinance has no
    EBITDA-specific estimate - see config.py's Forward EBITDA note). Reads
    `Ticker.revenue_estimate`, a DataFrame indexed by forward period ("0q",
    "+1q", "0y", "+1y") with columns including avg/low/high/numberOfAnalysts.
    "+1y" = next fiscal year, the closest thing to a standard "forward"
    (NTM-ish) revenue figure available here.

    Returns {"avg": float, "low": float, "high": float, "analysts": int} or
    None. Never raises - returns None on any failure (yfinance not
    installed, ticker not found, network issue, no analyst coverage at all -
    common for smaller/recently-IPO'd tickers, which is exactly where this
    is likely to come back empty).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        est = yf.Ticker(ticker).revenue_estimate
    except Exception:
        return None

    if est is None or est.empty or "+1y" not in est.index:
        return None

    row = est.loc["+1y"]
    avg = row.get("avg")
    if avg is None or pd.isna(avg):
        return None

    return {
        "avg": float(avg),
        "low": float(row["low"]) if pd.notna(row.get("low")) else None,
        "high": float(row["high"]) if pd.notna(row.get("high")) else None,
        "analysts": int(row["numberOfAnalysts"]) if pd.notna(row.get("numberOfAnalysts")) else None,
    }


def fetch_price_history_yf(ticker: str, period: str) -> Optional[pd.DataFrame]:
    """
    Daily closing share price history for `ticker` over `period` - a
    yfinance period string ("1mo", "3mo", "6mo", "ytd", "1y", "2y", "5y",
    "max"). Used for the adjustable-timeframe price chart in each company's
    summary header. Like market cap, this is a LIVE/daily-updating series,
    not something SEC filings have any equivalent of.

    Returns a DataFrame with columns date, close - or None (never raises)
    on any failure (yfinance not installed, ticker not found, network
    issue, no data for that period/ticker combination).
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        hist = yf.Ticker(ticker).history(period=period)
    except Exception:
        return None

    if hist is None or hist.empty or "Close" not in hist.columns:
        return None

    df = hist.reset_index()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df = df[[date_col, "Close"]].rename(columns={date_col: "date", "Close": "close"})
    df["date"] = pd.to_datetime(df["date"])
    if getattr(df["date"].dt, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_localize(None)
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return df if not df.empty else None


def fetch_shares_outstanding_yf(ticker: str) -> Optional[pd.DataFrame]:
    """
    Actual historical shares-outstanding for `ticker`, resampled to one
    figure per calendar quarter. Returns None (never raises) if yfinance
    isn't installed, the ticker isn't recognized, or the request fails for
    any reason - callers should treat None as "no supplemental data
    available" and fall back to whatever SEC-derived data they already have.
    """
    if not ticker:
        return None

    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        raw = yf.Ticker(ticker).get_shares_full(start="2000-01-01")
    except Exception:
        return None

    if raw is None or raw.empty:
        return None

    df = _resample_to_quarterly(raw)
    return df if not df.empty else None
