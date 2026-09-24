"""
Turn raw SEC XBRL "company concept" JSON into a clean, de-duplicated
pandas DataFrame of quarterly figures.

SEC XBRL data has a few quirks this module handles:
  - The same period often appears multiple times (restatements, amendments,
    the value getting re-reported in a later filing's comparison column).
    We keep only the most-recently-filed value for each period.
  - Filings mix quarterly (~91 day) and annual (~365 day) periods under the
    same tag. We filter to quarter-length periods for a clean quarterly series.
  - Some early SEC filings won't exist for a private company at all - that
    surfaces as None from sec_edgar.fetch_revenue_concept and should be
    handled by the caller (e.g. show a "no SEC data - private company"
    message rather than crashing).
"""
from __future__ import annotations

import pandas as pd

_QUARTER_MIN_DAYS = 80
_QUARTER_MAX_DAYS = 100

# Day-count ranges for year-to-date cumulative periods, keyed by how many
# quarters they cover (1 = "three months ended", 2 = "six months ended", ...).
# Generous enough to absorb calendar variation (28-31 day months, leap years).
_YTD_DAY_RANGES = {
    1: (80, 100),    # Q1 - identical range to _QUARTER_*, kept separate on
                      # purpose since this table's meaning ("N quarters of the
                      # fiscal year so far") is different from a discrete
                      # single quarter even when the day count overlaps.
    2: (170, 195),
    3: (260, 290),
    4: (350, 380),
}


def concept_to_quarterly_df(concept_json: dict, units_key: str = "USD") -> pd.DataFrame:
    """
    concept_json: raw JSON from sec_edgar.get_company_concept / fetch_revenue_concept.
    units_key: which unit bucket to read from the concept's "units" object -
        "USD" for dollar figures (the default), "shares" for a share-count
        concept like WeightedAverageNumberOfSharesOutstandingBasic. SEC keys
        this by the filer's declared unit, so a dollar-tag helper reading
        "shares" (or vice versa) would silently find nothing rather than
        error - pass the right one for the concept you're parsing.
    Returns columns: period_end (datetime), period_start (datetime),
                      value (float), fy, fp, form, filed.
    """
    usd_entries = concept_json.get("units", {}).get(units_key, [])
    rows = []
    for e in usd_entries:
        start = pd.to_datetime(e.get("start"))
        end = pd.to_datetime(e.get("end"))
        if pd.isna(start) or pd.isna(end):
            continue
        days = (end - start).days
        if not (_QUARTER_MIN_DAYS <= days <= _QUARTER_MAX_DAYS):
            continue  # skip annual / half-year / stub periods
        rows.append(
            {
                "period_start": start,
                "period_end": end,
                "value": e.get("val"),
                "fy": e.get("fy"),
                "fp": e.get("fp"),
                "form": e.get("form"),
                "filed": pd.to_datetime(e.get("filed")),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["period_start", "period_end", "value", "fy", "fp", "form", "filed"]
        )

    df = pd.DataFrame(rows)
    # Keep the most-recently-filed value for each (start, end) pair.
    df = df.sort_values("filed").drop_duplicates(
        subset=["period_start", "period_end"], keep="last"
    )
    df = df.sort_values("period_end").reset_index(drop=True)
    df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)
    return df


def concept_to_ytd_cumulative_df(concept_json: dict) -> pd.DataFrame:
    """
    Parse a cash-flow-statement-style XBRL concept that SEC filers report as
    YEAR-TO-DATE CUMULATIVE totals from the start of the fiscal year (e.g.
    "six months ended June 30"), rather than as discrete quarters the way
    revenue or balance-sheet items are. Keeps only periods that start exactly
    on Jan 1 of their own end year (i.e. assumes a calendar-year fiscal
    year - true for CoreWeave, check before reusing elsewhere) and whose
    length matches one of the four YTD buckets in _YTD_DAY_RANGES (three,
    six, nine, or twelve months).

    Returns columns: period_start, period_end, value, quarters_covered (1-4),
    form, filed. Feed this into cumulative_ytd_to_discrete_quarterly() to get
    actual per-quarter figures.
    """
    usd_entries = concept_json.get("units", {}).get("USD", [])
    rows = []
    for e in usd_entries:
        start = pd.to_datetime(e.get("start"))
        end = pd.to_datetime(e.get("end"))
        if pd.isna(start) or pd.isna(end):
            continue
        if not (start.month == 1 and start.day == 1 and start.year == end.year):
            continue  # not a "start of this fiscal year through some date" period
        days = (end - start).days
        quarters_covered = next(
            (n for n, (lo, hi) in _YTD_DAY_RANGES.items() if lo <= days <= hi), None
        )
        if quarters_covered is None:
            continue
        rows.append(
            {
                "period_start": start,
                "period_end": end,
                "value": e.get("val"),
                "quarters_covered": quarters_covered,
                "form": e.get("form"),
                "filed": pd.to_datetime(e.get("filed")),
            }
        )

    if not rows:
        return pd.DataFrame(
            columns=["period_start", "period_end", "value", "quarters_covered", "form", "filed"]
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("filed").drop_duplicates(
        subset=["period_start", "period_end"], keep="last"
    )
    return df.sort_values("period_end").reset_index(drop=True)


def cumulative_ytd_to_discrete_quarterly(ytd_df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert concept_to_ytd_cumulative_df's output into discrete per-quarter
    values by subtracting consecutive cumulative totals within each fiscal
    year: Q1 = the "three months" figure itself, Q2 = "six months" minus
    "three months", Q3 = "nine months" minus "six months", Q4 = "twelve
    months" minus "nine months".

    A quarter is only emitted if the PRECEDING quarter's cumulative figure is
    also present that fiscal year (Q1 needs nothing extra, since it's already
    discrete by construction). This deliberately skips periods that can't be
    safely decomposed - e.g. a company's first annual report after IPO, which
    often has only a full-year figure and no quarterly comparatives to
    subtract, would otherwise get its whole-year total mislabeled as a single
    quarter. Skipping is silent by design here; a caller wanting to know
    what got dropped can compare against ytd_df directly.

    Returns columns: period_end, value (discrete quarter), ytd_value
    (original cumulative figure, kept for reference/debugging), quarter_label,
    form, filed.
    """
    if ytd_df.empty:
        return pd.DataFrame(
            columns=["period_end", "value", "ytd_value", "quarter_label", "form", "filed"]
        )

    df = ytd_df.copy()
    df["fiscal_year"] = df["period_start"].dt.year

    rows = []
    for _fy, group in df.groupby("fiscal_year"):
        by_quarters_covered = {row["quarters_covered"]: row for _, row in group.iterrows()}
        for n in (1, 2, 3, 4):
            if n not in by_quarters_covered:
                continue
            current = by_quarters_covered[n]
            if n == 1:
                discrete_value = current["value"]
            elif (n - 1) in by_quarters_covered:
                discrete_value = current["value"] - by_quarters_covered[n - 1]["value"]
            else:
                continue  # can't safely decompose without the prior cumulative figure
            rows.append(
                {
                    "period_end": current["period_end"],
                    "value": discrete_value,
                    "ytd_value": current["value"],
                    "form": current["form"],
                    "filed": current["filed"],
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=["period_end", "value", "ytd_value", "quarter_label", "form", "filed"]
        )

    result = pd.DataFrame(rows).sort_values("period_end").reset_index(drop=True)
    result["quarter_label"] = result["period_end"].dt.to_period("Q").astype(str)
    return result


def compute_fcf_df(ocf_discrete_df: pd.DataFrame, capex_discrete_df: pd.DataFrame) -> pd.DataFrame:
    """
    Free cash flow = operating cash flow - capex, computed per quarter from
    two DataFrames shaped like cumulative_ytd_to_discrete_quarterly's output.
    Only quarters present in BOTH inputs are kept (an inner join) - a quarter
    where one of the two couldn't be safely decomposed (see that function's
    docstring) can't produce a trustworthy FCF figure either, so it's left
    out rather than guessed at.

    Returns columns: period_end, quarter_label, operating_cash_flow, capex, fcf.
    """
    if ocf_discrete_df.empty or capex_discrete_df.empty:
        return pd.DataFrame(
            columns=["period_end", "quarter_label", "operating_cash_flow", "capex", "fcf"]
        )

    merged = ocf_discrete_df[["period_end", "quarter_label", "value"]].merge(
        capex_discrete_df[["period_end", "value"]],
        on="period_end",
        how="inner",
        suffixes=("_ocf", "_capex"),
    )
    merged = merged.rename(columns={"value_ocf": "operating_cash_flow", "value_capex": "capex"})
    merged["fcf"] = merged["operating_cash_flow"] - merged["capex"]
    return merged.sort_values("period_end").reset_index(drop=True)


def compute_ebitda_df(operating_income_df: pd.DataFrame, da_discrete_df: pd.DataFrame) -> pd.DataFrame:
    """
    EBITDA proxy = operating income + depreciation & amortization, per
    quarter. operating_income_df is a discrete-quarter income-statement
    series (concept_to_quarterly_df's shape, like net_income); da_discrete_df
    is a decomposed discrete-quarter series (cumulative_ytd_to_discrete_
    quarterly's shape, like operating_cash_flow/capex - D&A is reported
    year-to-date cumulative on the cash flow statement, not discrete).

    Only quarters present in BOTH inputs are kept (an inner join), same
    reasoning as compute_fcf_df: a quarter where D&A couldn't be safely
    decomposed can't produce a trustworthy EBITDA figure either.

    Returns columns: period_end, quarter_label, operating_income,
    depreciation_amortization, ebitda.
    """
    cols = ["period_end", "quarter_label", "operating_income", "depreciation_amortization", "ebitda"]
    if operating_income_df is None or da_discrete_df is None or operating_income_df.empty or da_discrete_df.empty:
        return pd.DataFrame(columns=cols)

    merged = operating_income_df[["period_end", "quarter_label", "value"]].merge(
        da_discrete_df[["period_end", "value"]],
        on="period_end",
        how="inner",
        suffixes=("_oi", "_da"),
    )
    if merged.empty:
        return pd.DataFrame(columns=cols)

    merged = merged.rename(columns={"value_oi": "operating_income", "value_da": "depreciation_amortization"})
    merged["ebitda"] = merged["operating_income"] + merged["depreciation_amortization"]
    return merged.sort_values("period_end").reset_index(drop=True)[cols]


def compute_enterprise_value(market_cap, total_debt, cash):
    """
    Enterprise Value = market cap + total debt - cash & equivalents. Used
    for the Forward EV/Sales metric (see config.py's note on it). Returns
    None if any of the three inputs is None/missing - a partial EV would be
    misleading rather than merely approximate.
    """
    if market_cap is None or total_debt is None or cash is None:
        return None
    return market_cap + total_debt - cash


def compute_forward_multiple(numerator, forward_revenue):
    """
    A forward valuation multiple = numerator / forward_revenue - shared by
    Forward EV/Sales (numerator = enterprise value) and Forward P/S
    (numerator = market cap). Returns None if either input is missing or
    forward_revenue is zero/falsy (avoids a divide-by-zero into +/-inf).
    """
    if numerator is None or not forward_revenue:
        return None
    return numerator / forward_revenue


def concept_to_point_in_time_df(concept_json: dict) -> pd.DataFrame:
    """
    Parse an "instant" XBRL concept - e.g. total assets, cash, debt - which
    is reported as of a single balance-sheet date rather than over a period.
    These entries have an 'end' but no 'start' in the SEC response.

    Returns columns: period_end (datetime), value (float, USD), fy, fp, form, filed.
    """
    usd_entries = concept_json.get("units", {}).get("USD", [])
    rows = []
    for e in usd_entries:
        end = pd.to_datetime(e.get("end"))
        if pd.isna(end):
            continue
        rows.append(
            {
                "period_end": end,
                "value": e.get("val"),
                "fy": e.get("fy"),
                "fp": e.get("fp"),
                "form": e.get("form"),
                "filed": pd.to_datetime(e.get("filed")),
            }
        )

    if not rows:
        return pd.DataFrame(columns=["period_end", "value", "fy", "fp", "form", "filed"])

    df = pd.DataFrame(rows)
    # Keep the most-recently-filed value for each balance-sheet date.
    df = df.sort_values("filed").drop_duplicates(subset=["period_end"], keep="last")
    df = df.sort_values("period_end").reset_index(drop=True)
    df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)

    # Some filers occasionally tag an ad-hoc disclosure - e.g. announcing one
    # big new contract the day it's signed - under the same "instant" concept
    # as their normal quarter-end balance (seen with IREN's RPO: a $9.7B
    # figure dated 2025-11-02, a contract-signing date, alongside the real
    # 2025-12-31 quarter-end RPO of $289M). That announcement date falls in
    # the same calendar quarter as the real quarter-end, so both rows get the
    # same quarter_label - which makes a line chart zigzag up to the one-off
    # figure and back down at the "same" x position. When more than one
    # period_end lands in a quarter_label, prefer the one that's an actual
    # quarter-end date (the last calendar day of Mar/Jun/Sep/Dec - the real
    # balance-sheet date); only fall back to the latest period_end in that
    # quarter if none of them are.
    def _is_quarter_end(ts: pd.Timestamp) -> bool:
        return ts.month in (3, 6, 9, 12) and ts.day == ts.days_in_month

    def _pick_one_per_quarter(group: pd.DataFrame) -> pd.Series:
        quarter_end_rows = group[group["period_end"].apply(_is_quarter_end)]
        return quarter_end_rows.iloc[-1] if not quarter_end_rows.empty else group.iloc[-1]

    if df["quarter_label"].duplicated().any():
        df = df.groupby("quarter_label", group_keys=False).apply(_pick_one_per_quarter)
        # groupby(...).apply(...) on the same column it grouped by can drop
        # that column from the result (pandas quirk), so it's recomputed
        # fresh from period_end rather than trusted to have survived.
        df = df.sort_values("period_end").reset_index(drop=True)
        df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)

    return df


def build_debt_segment_df(
    primary_json: dict | None, component_jsons: list[tuple[dict | None, str]]
) -> pd.DataFrame:
    """
    Build one debt segment's per-date series with a "primary tag, fall back
    to summed components" rule (see the long comment on config.DEBT_SEGMENTS
    for why this exists - CoreWeave split a single "long-term debt" line into
    "recourse"/"non-recourse debt" partway through its filing history, and
    the old tag simply has no value for dates after that split).

    For each balance-sheet date found in EITHER the primary series or any
    component series:
      - if the primary tag reported a value for that date, use it.
      - otherwise, sum whichever components reported a value for that date
        (missing components treated as 0 - most likely the company had
        nothing outstanding in that sub-category, not missing data).

    component_jsons: list of (concept_json_or_None, "taxonomy:tag" label) -
      the label isn't used here, just carried by the caller for display; this
      function only needs the json.

    Returns columns: period_end, value, quarter_label, source
      (source is "primary" or "components", useful for a UI caption/tooltip
      explaining why a given bar segment looks the way it does).
    """
    primary_df = (
        concept_to_point_in_time_df(primary_json)
        if primary_json
        else pd.DataFrame(columns=["period_end", "value"])
    )

    component_dfs = [
        concept_to_point_in_time_df(cj) for cj, _label in component_jsons if cj
    ]
    non_empty_components = [df for df in component_dfs if not df.empty]
    if non_empty_components:
        comp_stacked = pd.concat(
            [df[["period_end", "value"]] for df in non_empty_components], ignore_index=True
        )
        comp_summed = comp_stacked.groupby("period_end", as_index=False)["value"].sum()
    else:
        comp_summed = pd.DataFrame(columns=["period_end", "value"])

    all_dates = sorted(set(primary_df["period_end"]) | set(comp_summed["period_end"]))
    if not all_dates:
        return pd.DataFrame(columns=["period_end", "value", "quarter_label", "source"])

    primary_lookup = primary_df.set_index("period_end")["value"] if not primary_df.empty else pd.Series(dtype=float)
    comp_lookup = comp_summed.set_index("period_end")["value"] if not comp_summed.empty else pd.Series(dtype=float)

    rows = []
    for d in all_dates:
        if d in primary_lookup.index and pd.notna(primary_lookup.loc[d]):
            rows.append({"period_end": d, "value": primary_lookup.loc[d], "source": "primary"})
        elif d in comp_lookup.index:
            rows.append({"period_end": d, "value": comp_lookup.loc[d], "source": "components"})
        else:
            rows.append({"period_end": d, "value": 0.0, "source": "none"})

    df = pd.DataFrame(rows).sort_values("period_end").reset_index(drop=True)
    df["quarter_label"] = df["period_end"].dt.to_period("Q").astype(str)
    return df


def build_stacked_segment_df(segment_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Combine several point-in-time segment DataFrames (one per category, each
    shaped like concept_to_point_in_time_df's output) into a single wide
    DataFrame for a stacked bar chart: one row per balance-sheet date, one
    column per segment, plus a 'total' column.

    segment_dfs: {segment_label: df_with_period_end_and_value_columns}.
    A segment missing entirely (company never reported it - e.g. no finance
    leases yet) can be an empty DataFrame; it's treated as all-zero and still
    gets a column, so the chart legend stays consistent across companies.

    Periods that only some segments reported on are filled with 0 for the
    others (rather than dropped), since a missing balance almost always means
    "nothing outstanding in that category as of that date," not missing data.
    """
    labels = list(segment_dfs.keys())
    all_dates = sorted(
        {
            d
            for df in segment_dfs.values()
            if not df.empty
            for d in df["period_end"]
        }
    )
    if not all_dates:
        return pd.DataFrame(columns=["period_end", "quarter_label", *labels, "total"])

    wide = pd.DataFrame({"period_end": all_dates})
    for label in labels:
        df = segment_dfs[label]
        if df.empty:
            wide[label] = 0.0
            continue
        merged = wide.merge(
            df[["period_end", "value"]].rename(columns={"value": label}),
            on="period_end",
            how="left",
        )
        wide[label] = merged[label].fillna(0.0)

    wide["quarter_label"] = wide["period_end"].dt.to_period("Q").astype(str)
    wide["total"] = wide[labels].sum(axis=1)
    wide = wide.sort_values("period_end").reset_index(drop=True)
    return wide[["period_end", "quarter_label", *labels, "total"]]


def merge_quarterly_series(named_dfs: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Outer-join several quarterly DataFrames (each shaped like
    concept_to_quarterly_df's output - at least period_end/value columns)
    into one wide DataFrame, one column per name in named_dfs.

    Used where two related concepts don't necessarily cover the exact same
    set of quarters (e.g. basic vs. diluted EPS - one could in principle be
    reported for a quarter the other isn't) but both should still show up on
    the same chart. A quarter missing from one series gets NaN in that
    column rather than being dropped, so the caller can still plot or
    display whichever value(s) it has.

    named_dfs: {"eps_basic": basic_df, "eps_diluted": diluted_df, ...} -
    a missing/empty DataFrame for a name is fine, it just contributes no
    data (that column comes back all-NaN).

    Returns columns: period_end, quarter_label, <name> for each key in
    named_dfs (in the order given). All-empty input returns an empty df
    with just those columns.
    """
    names = list(named_dfs.keys())
    non_empty = {k: df for k, df in named_dfs.items() if df is not None and not df.empty}
    if not non_empty:
        return pd.DataFrame(columns=["period_end", "quarter_label", *names])

    all_dates = sorted(set().union(*(set(df["period_end"]) for df in non_empty.values())))
    wide = pd.DataFrame({"period_end": all_dates})
    for name in names:
        df = named_dfs.get(name)
        if df is None or df.empty:
            wide[name] = float("nan")
            continue
        lookup = df.set_index("period_end")["value"]
        wide[name] = wide["period_end"].map(lookup)

    wide["quarter_label"] = wide["period_end"].dt.to_period("Q").astype(str)
    wide = wide.sort_values("period_end").reset_index(drop=True)
    return wide[["period_end", "quarter_label", *names]]


def compute_margin_df(
    numerator_df: pd.DataFrame, revenue_df: pd.DataFrame, margin_col: str = "margin"
) -> pd.DataFrame:
    """
    margin = numerator / revenue * 100, per quarter - e.g. net margin
    (numerator=net income) or FCF margin (numerator=fcf).

    numerator_df / revenue_df: each shaped like concept_to_quarterly_df's
    output (period_end, quarter_label, value at minimum) - revenue_df only
    needs period_end/value, since quarter_label is taken from numerator_df.

    INNER-joined on period_end (unlike merge_quarterly_series's outer join)
    since a margin is only meaningful for a quarter where BOTH figures
    exist - there's no sensible "partial" margin the way EPS basic/diluted
    can each stand alone. A quarter with exactly $0 revenue is dropped
    too, rather than producing a +/-inf or NaN margin.

    Returns columns: period_end, quarter_label, numerator, revenue,
    <margin_col> (a percentage, e.g. 42.5 for 42.5%). Empty (with those
    columns, so callers can check .empty safely) if either input is
    missing/empty, nothing lines up on period_end, or every remaining
    quarter has zero revenue.
    """
    cols = ["period_end", "quarter_label", "numerator", "revenue", margin_col]
    if numerator_df is None or revenue_df is None or numerator_df.empty or revenue_df.empty:
        return pd.DataFrame(columns=cols)

    merged = numerator_df[["period_end", "quarter_label", "value"]].merge(
        revenue_df[["period_end", "value"]], on="period_end", how="inner", suffixes=("_num", "_rev")
    )
    if merged.empty:
        return pd.DataFrame(columns=cols)

    merged = merged.rename(columns={"value_num": "numerator", "value_rev": "revenue"})
    merged = merged[merged["revenue"] != 0]
    if merged.empty:
        return pd.DataFrame(columns=cols)

    merged[margin_col] = merged["numerator"] / merged["revenue"] * 100
    return merged.sort_values("period_end").reset_index(drop=True)[cols]


def add_yoy_growth(df: pd.DataFrame, value_col: str = "value") -> pd.DataFrame:
    """Add a year-over-year % growth column, matched on quarter_label's Q (e.g. Q2)."""
    df = df.copy()
    df["quarter"] = df["period_end"].dt.quarter
    df["year"] = df["period_end"].dt.year
    df = df.sort_values(["quarter", "year"])
    df["yoy_growth"] = df.groupby("quarter")[value_col].pct_change() * 100
    df = df.sort_values("period_end").reset_index(drop=True)
    return df
