"""
Offline sanity check for data_processing.py using a saved sample SEC
response (tests/fixture_coreweave_revenue.json), so the parsing logic can
be verified without live network access to data.sec.gov.

Run: python tests/test_data_processing.py
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_processing import (
    add_yoy_growth,
    build_debt_segment_df,
    build_stacked_segment_df,
    compute_ebitda_df,
    compute_enterprise_value,
    compute_fcf_df,
    compute_forward_multiple,
    compute_margin_df,
    concept_to_point_in_time_df,
    concept_to_quarterly_df,
    concept_to_ytd_cumulative_df,
    cumulative_ytd_to_discrete_quarterly,
    merge_quarterly_series,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_revenue.json")
ASSETS_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_assets.json")
IREN_RPO_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_iren_rpo.json")
OCF_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_ocf.json")
CAPEX_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_capex.json")
NET_INCOME_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_net_income.json")
SHARES_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_shares.json")
EPS_BASIC_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_eps_basic.json")
EPS_DILUTED_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_eps_diluted.json")
RPO_FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_coreweave_rpo.json")
DEBT_FIXTURES = {
    "Long-term debt (current)": "fixture_coreweave_debt_ltd_current.json",
    "Long-term debt (non-current)": "fixture_coreweave_debt_ltd_noncurrent.json",
    "Finance lease liabilities (current)": "fixture_coreweave_debt_lease_current.json",
    "Finance lease liabilities (non-current)": "fixture_coreweave_debt_lease_noncurrent.json",
}


def test_revenue():
    with open(FIXTURE) as f:
        concept_json = json.load(f)

    df = concept_to_quarterly_df(concept_json)

    # The fixture has 9 quarter-length entries (including 2 duplicate
    # periods) + 1 annual entry that must be filtered out.
    assert len(df) == 7, f"expected 7 deduped quarters, got {len(df)}\n{df}"

    # Annual (FY2024) entry should have been dropped as non-quarterly length.
    assert not (df["period_end"] == "2024-12-31").any(), "annual entry leaked into quarterly df"

    # Duplicate Q1 2024 (same start/end, same value) should collapse to one row.
    q1_2024 = df[(df["period_start"] == "2024-01-01") & (df["period_end"] == "2024-03-31")]
    assert len(q1_2024) == 1, "duplicate period was not deduplicated"

    # Duplicate Q2 2025 (10-Q vs 10-Q/A, later filed) should keep the later filing.
    q2_2025 = df[(df["period_start"] == "2025-04-01") & (df["period_end"] == "2025-06-30")]
    assert len(q2_2025) == 1
    assert q2_2025.iloc[0]["form"] == "10-Q/A", "should keep most-recently-filed duplicate"

    # Values should be sorted ascending by period_end.
    assert df["period_end"].is_monotonic_increasing

    # Spot-check a known value.
    q2_2026 = df[df["quarter_label"] == "2026Q2"]
    assert q2_2026.iloc[0]["value"] == 2575000000

    df_yoy = add_yoy_growth(df)
    # Q2 2026 vs Q2 2025 (1,212,000,000): (2,575,000,000/1,212,000,000 - 1) * 100
    q2_2026_yoy = df_yoy[df_yoy["quarter_label"] == "2026Q2"].iloc[0]["yoy_growth"]
    expected_yoy = (2575000000 / 1212000000 - 1) * 100
    assert abs(q2_2026_yoy - expected_yoy) < 0.01, f"YoY calc off: {q2_2026_yoy} vs {expected_yoy}"

    print("Revenue checks passed.")
    print(df[["quarter_label", "value", "form"]].to_string(index=False))


def test_total_assets():
    with open(ASSETS_FIXTURE) as f:
        concept_json = json.load(f)

    df = concept_to_point_in_time_df(concept_json)

    # 7 entries in fixture, one duplicate (same end date, 10-Q vs 10-Q/A) -> 6 rows.
    assert len(df) == 6, f"expected 6 deduped balance-sheet dates, got {len(df)}\n{df}"

    # Duplicate 2025-06-30 (10-Q vs 10-Q/A, later filed) should keep the amendment.
    dup = df[df["period_end"] == "2025-06-30"]
    assert len(dup) == 1
    assert dup.iloc[0]["form"] == "10-Q/A", "should keep most-recently-filed duplicate"

    # No 'start'/duration filtering should have been applied - all 6 distinct
    # dates (including the FY10-K date) should survive, unlike the quarterly parser.
    assert (df["period_end"] == "2025-12-31").any(), "10-K balance date should NOT be filtered out here"

    assert df["period_end"].is_monotonic_increasing

    latest = df.iloc[-1]
    assert latest["value"] == 55570000000
    assert latest["period_end"] == pd.Timestamp("2026-03-31")

    print("Total assets checks passed.")
    print(df[["period_end", "value", "form"]].to_string(index=False))


def test_point_in_time_ad_hoc_disclosure_collapsed_to_quarter_end():
    """
    Reproduces and fixes the reported bug: IREN's RPO chart looked "weird" -
    a real SEC quirk where a $9.7B contract-signing disclosure, dated
    2025-11-02 (not a quarter-end), was tagged under the same instant
    concept as the real 2025-12-31 quarter-end RPO of $289M. Both land in
    quarter_label "2025Q4", so a naive line chart plotted 195M (Q3) -> 9.7B
    (Q4, 11-02) -> 289M (Q4, 12-31) -> 710M (Q1), zigzagging within "Q4".

    concept_to_point_in_time_df should collapse each quarter_label to a
    single row, preferring the genuine quarter-end date over the ad-hoc one.
    """
    with open(IREN_RPO_FIXTURE) as f:
        concept_json = json.load(f)

    df = concept_to_point_in_time_df(concept_json)

    # 5 raw entries: one exact duplicate by period_end (2025-11-02, filed
    # twice) collapses first, then the remaining 2025-11-02 and 2025-12-31
    # rows both fall in quarter_label "2025Q4" and collapse again -> 3
    # distinct quarters total (2025Q3, 2025Q4, 2026Q1).
    assert len(df) == 3, f"expected 3 one-row-per-quarter entries, got {len(df)}\n{df}"
    assert sorted(df["quarter_label"].tolist()) == df["quarter_label"].tolist(), "must stay chronological"
    assert not df["quarter_label"].duplicated().any(), "each quarter should appear exactly once"

    q4 = df[df["quarter_label"] == "2025Q4"]
    assert len(q4) == 1
    # The real quarter-end (2025-12-31, $289M) should win over the ad-hoc
    # contract-signing date (2025-11-02, $9.7B), even though the latter was
    # also the most-recently-filed value for its own exact date.
    assert q4.iloc[0]["period_end"] == pd.Timestamp("2025-12-31")
    assert q4.iloc[0]["value"] == 289411000

    assert df["period_end"].is_monotonic_increasing
    # No lingering $9.7B outlier anywhere in the cleaned series.
    assert not (df["value"] == 9700000000).any()

    print("Point-in-time ad-hoc-disclosure collapse checks passed.")
    print(df[["period_end", "quarter_label", "value", "form"]].to_string(index=False))


def test_total_debt_stack():
    segment_dfs = {}
    for label, fname in DEBT_FIXTURES.items():
        with open(os.path.join(os.path.dirname(__file__), fname)) as f:
            concept_json = json.load(f)
        segment_dfs[label] = concept_to_point_in_time_df(concept_json)

    stacked = build_stacked_segment_df(segment_dfs)

    # Union of all dates across the 4 fixtures: LTD current/noncurrent go
    # back to 2024-12-31, finance leases only start at 2025-03-31.
    assert len(stacked) == 6, f"expected 6 distinct balance-sheet dates, got {len(stacked)}\n{stacked}"

    # 2024-12-31: LTD current dedupes to the 10-K-restated value (2,468,000,000),
    # LTD noncurrent 5,460,000,000, both finance lease columns should be
    # backfilled to 0 since neither fixture has data for that date.
    row = stacked[stacked["period_end"] == "2024-12-31"].iloc[0]
    assert row["Long-term debt (current)"] == 2468000000
    assert row["Long-term debt (non-current)"] == 5460000000
    assert row["Finance lease liabilities (current)"] == 0
    assert row["Finance lease liabilities (non-current)"] == 0
    assert row["total"] == 2468000000 + 5460000000

    # 2026-03-31: only the two LTD columns have data (leases fixture stops
    # at 2025-12-31 in this sample) - still shouldn't drop the row or NaN out.
    latest = stacked.iloc[-1]
    assert latest["period_end"] == pd.Timestamp("2026-03-31")
    assert latest["Finance lease liabilities (current)"] == 0
    assert latest["total"] == 7547000000 + 17310000000

    # A fully-populated row: 2025-12-31 has all 4 categories reported.
    full_row = stacked[stacked["period_end"] == "2025-12-31"].iloc[0]
    expected_total = 6708000000 + 14670000000 + 38000000 + 216000000
    assert full_row["total"] == expected_total

    assert stacked["period_end"].is_monotonic_increasing

    print("Total debt stack checks passed.")
    print(stacked.to_string(index=False))


def test_missing_segment_becomes_all_zero_column():
    """A category a company has never reported (e.g. no finance leases at
    all) should still appear as an all-zero column, not disappear - so the
    chart legend/colors stay identical across companies."""
    segment_dfs = {
        "Long-term debt (current)": pd.DataFrame(
            {"period_end": [pd.Timestamp("2026-03-31")], "value": [100.0]}
        ),
        "Finance lease liabilities (current)": pd.DataFrame(),  # never reported
    }
    stacked = build_stacked_segment_df(segment_dfs)
    assert list(stacked.columns) == [
        "period_end",
        "quarter_label",
        "Long-term debt (current)",
        "Finance lease liabilities (current)",
        "total",
    ]
    assert stacked.iloc[0]["Finance lease liabilities (current)"] == 0
    assert stacked.iloc[0]["total"] == 100.0
    print("Missing-segment-as-zero-column check passed.")


def test_debt_segment_primary_with_component_fallback():
    """
    Reproduces and fixes the reported bug: CoreWeave's Q2 2026 10-Q stopped
    reporting the old LongTermDebtCurrent tag entirely (last value is at
    2026-03-31) and started reporting the split "RecourseDebtCurrent" /
    "NonRecourseDebtCurrent" instead. A naive "pick whichever tag has any
    data" approach used the old tag and showed $0 debt for 2026-06-30 (only
    finance lease liabilities were still using an unchanged tag, hence they
    were the only category that showed up). build_debt_segment_df should
    use the primary tag where it has data, and fall back to summed
    components where it doesn't.
    """
    with open(os.path.join(os.path.dirname(__file__), "fixture_coreweave_debt_ltd_current.json")) as f:
        primary_json = json.load(f)
    with open(os.path.join(os.path.dirname(__file__), "fixture_coreweave_recourse_debt_current.json")) as f:
        recourse_json = json.load(f)
    with open(os.path.join(os.path.dirname(__file__), "fixture_coreweave_nonrecourse_debt_current.json")) as f:
        nonrecourse_json = json.load(f)

    component_jsons = [(recourse_json, "crwv:RecourseDebtCurrent"), (nonrecourse_json, "crwv:NonRecourseDebtCurrent")]
    df = build_debt_segment_df(primary_json, component_jsons)

    # Old dates: only the primary tag has data -> used directly.
    row_2024 = df[df["period_end"] == "2024-12-31"].iloc[0]
    assert row_2024["value"] == 2468000000
    assert row_2024["source"] == "primary"

    # 2025-12-31: BOTH primary and components have data (6,708,000,000 either
    # way) - primary should still win per the stated rule.
    row_2025q4 = df[df["period_end"] == "2025-12-31"].iloc[0]
    assert row_2025q4["value"] == 6708000000
    assert row_2025q4["source"] == "primary"

    # THE BUG: 2026-06-30 has no primary value at all - must fall back to
    # summed components (6,235,000,000 + 1,278,000,000).
    row_2026q2 = df[df["period_end"] == "2026-06-30"].iloc[0]
    assert row_2026q2["value"] == 6235000000 + 1278000000, (
        f"expected component fallback sum, got {row_2026q2['value']} "
        "(this is the bug: falling back to 0 instead of summing components)"
    )
    assert row_2026q2["source"] == "components"

    print("Debt segment primary/component fallback checks passed (Q2 2026 bug fixed).")
    print(df.to_string(index=False))


def test_rpo_falls_back_to_contract_liability_when_primary_tag_goes_stale():
    """
    Reproduces and fixes the reported bug: Applied Digital's RPO looked
    stuck at 2023-05-31 (~$48.7M) even in 2026, because SEC confirms it
    genuinely stopped reporting RevenueRemainingPerformanceObligation after
    its FY2023 10-K - it's not a caching/staleness bug in the app, the tag
    itself has nothing newer. Reuses build_debt_segment_df (the same
    primary-tag-else-fallback merge already proven for CoreWeave's debt
    reclassification) with ContractWithCustomerLiability as a single
    fallback "component", per config.RPO_FALLBACK_TAGS.
    """
    with open(os.path.join(os.path.dirname(__file__), "fixture_apld_rpo.json")) as f:
        primary_json = json.load(f)
    with open(os.path.join(os.path.dirname(__file__), "fixture_apld_contract_liability.json")) as f:
        fallback_json = json.load(f)

    df = build_debt_segment_df(primary_json, [(fallback_json, "us-gaap:ContractWithCustomerLiability")])

    # The old stale date should still be there, sourced from the primary tag.
    stale = df[df["period_end"] == "2023-05-31"].iloc[0]
    assert stale["value"] == 48700000
    assert stale["source"] == "primary"

    # THE BUG: without a fallback, nothing after 2023-05-31 would show up at
    # all. With it, 2025/2026 dates should appear, sourced from the fallback.
    assert (df["period_end"] == "2026-05-31").any(), "should have a 2026 date after the fallback merge"
    latest = df.sort_values("period_end").iloc[-1]
    assert latest["period_end"] == pd.Timestamp("2026-05-31")
    assert latest["value"] == 4666000
    assert latest["source"] == "components"

    assert list(df["period_end"]) == sorted(df["period_end"]), "dates should come out in chronological order"

    print("RPO stale-tag-to-contract-liability fallback checks passed.")
    print(df[["period_end", "quarter_label", "value", "source"]].to_string(index=False))


def test_fcf():
    with open(OCF_FIXTURE) as f:
        ocf_json = json.load(f)
    with open(CAPEX_FIXTURE) as f:
        capex_json = json.load(f)

    ocf_quarterly = cumulative_ytd_to_discrete_quarterly(concept_to_ytd_cumulative_df(ocf_json))
    capex_quarterly = cumulative_ytd_to_discrete_quarterly(concept_to_ytd_cumulative_df(capex_json))

    # 2023 has only a standalone FY (12-month) entry with no Q1/Q2/Q3 that
    # year to decompose against - it must be silently skipped, not
    # mislabeled as a single giant "quarter".
    assert not (ocf_quarterly["period_end"] == "2023-12-31").any(), "undecomposable FY-only period leaked through"
    assert not (capex_quarterly["period_end"] == "2023-12-31").any()

    # 2024 is a complete chain (Q1..Q4 all present) - check every discrete value.
    def val(df, end):
        return df.loc[df["period_end"] == end, "value"].iloc[0]

    assert val(ocf_quarterly, "2024-03-31") == 2039038000       # Q1 = the YTD figure itself
    assert val(ocf_quarterly, "2024-06-30") == 1921214000 - 2039038000   # Q2 = 6mo - 3mo
    assert val(ocf_quarterly, "2024-09-30") == 2562436000 - 1921214000   # Q3 = 9mo - 6mo
    assert val(ocf_quarterly, "2024-12-31") == 2749000000 - 2562436000   # Q4 = FY - 9mo

    # 2025 also exercises the restatement-dedup path (Q1 and Q2 each had two
    # filed values; the later-filed one should be the one used downstream).
    assert val(ocf_quarterly, "2025-03-31") == 61000000             # not the earlier 61,168,000
    assert val(ocf_quarterly, "2025-06-30") == -190000000 - 61000000  # not using -190,083,000

    # 2026 is a partial, still-growing chain (only Q1 and Q2 filed so far) -
    # both should still come through since Q2's prior quarter (Q1) IS present.
    assert val(ocf_quarterly, "2026-03-31") == 2984000000
    assert val(ocf_quarterly, "2026-06-30") == 3663000000 - 2984000000

    # Same completeness check for capex.
    assert val(capex_quarterly, "2024-03-31") == 1741935000
    assert val(capex_quarterly, "2026-06-30") == 14117000000 - 7695000000

    fcf = compute_fcf_df(ocf_quarterly, capex_quarterly)
    assert len(fcf) == 10, f"expected 10 quarters (2024x4 + 2025x4 + 2026x2), got {len(fcf)}\n{fcf}"

    row_2024q1 = fcf[fcf["quarter_label"] == "2024Q1"].iloc[0]
    assert row_2024q1["operating_cash_flow"] == 2039038000
    assert row_2024q1["capex"] == 1741935000
    assert row_2024q1["fcf"] == 2039038000 - 1741935000

    row_2026q2 = fcf[fcf["quarter_label"] == "2026Q2"].iloc[0]
    expected_ocf_q2_2026 = 3663000000 - 2984000000
    expected_capex_q2_2026 = 14117000000 - 7695000000
    assert row_2026q2["fcf"] == expected_ocf_q2_2026 - expected_capex_q2_2026
    assert row_2026q2["fcf"] < 0, "expected deeply negative FCF for this heavy-capex quarter"

    print("Free cash flow checks passed.")
    print(fcf[["quarter_label", "operating_cash_flow", "capex", "fcf"]].to_string(index=False))


def test_net_income():
    """
    NetIncomeLoss (unlike operating_cash_flow/capex) IS tagged as a discrete
    quarter directly, alongside the YTD figure, so this should go straight
    through concept_to_quarterly_df with no decomposition needed - this test
    is really checking two things: (1) that the discrete quarters really are
    picked up and deduped correctly across the repeated/restated entries,
    and (2) that Q4 is correctly absent (10-Ks never carry a standalone
    "three months ended Dec 31" figure), the same known gap the revenue
    chart already has.
    """
    with open(NET_INCOME_FIXTURE) as f:
        concept_json = json.load(f)

    df = concept_to_quarterly_df(concept_json)  # default units_key="USD"

    quarters = set(df["quarter_label"])
    assert quarters == {
        "2024Q1", "2024Q2", "2024Q3",
        "2025Q1", "2025Q2", "2025Q3",
        "2026Q1", "2026Q2",
    }, f"unexpected quarter set: {quarters}"
    assert "2024Q4" not in quarters and "2025Q4" not in quarters, "Q4 should never be present"
    assert not (df["period_end"] == "2023-12-31").any(), "the standalone FY2023 figure should not appear"

    def val(end):
        return df.loc[df["period_end"] == end, "value"].iloc[0]

    assert val("2024-03-31") == -129248000
    assert val("2024-06-30") == -323021000       # discrete Q2, not the -452,269,000 YTD figure
    assert val("2025-06-30") == -290000000        # latest-filed dedup, not -290,509,000
    assert val("2026-06-30") == -626000000

    print("Net income/loss checks passed.")
    print(df[["quarter_label", "value", "form"]].to_string(index=False))


def test_shares_outstanding_uses_shares_unit():
    """
    Confirms the units_key generalization actually works end to end: this
    concept is filed under XBRL's "shares" unit, not "USD" - reading the
    wrong bucket would silently return zero rows rather than error, so this
    checks real data comes back, not just that nothing crashes.
    """
    with open(SHARES_FIXTURE) as f:
        concept_json = json.load(f)

    # Reading with the default (USD) bucket should find nothing - this concept
    # simply isn't filed under "USD" at all.
    wrong_unit_df = concept_to_quarterly_df(concept_json)
    assert wrong_unit_df.empty, "shares data should not appear when reading the USD bucket"

    df = concept_to_quarterly_df(concept_json, units_key="shares")
    assert not df.empty, "shares data should be found when reading the shares bucket"

    def val(end):
        return df.loc[df["period_end"] == end, "value"].iloc[0]

    assert val("2024-03-31") == 209228000
    assert val("2025-06-30") == 487000000   # latest-filed dedup, not 486,591,000
    assert val("2026-06-30") == 551000000
    assert df["period_end"].is_monotonic_increasing

    print("Shares outstanding (units_key='shares') checks passed.")
    print(df[["quarter_label", "value"]].to_string(index=False))


def test_eps():
    """
    EPS (basic and diluted) is a standard discrete-quarter income-statement
    tag, like net income - reads straight through concept_to_quarterly_df
    once the right unit bucket ("USD/shares", not "USD") is used. Checks the
    unit-key plumbing and a few spot values from both tags.
    """
    with open(EPS_BASIC_FIXTURE) as f:
        basic_json = json.load(f)
    with open(EPS_DILUTED_FIXTURE) as f:
        diluted_json = json.load(f)

    # Wrong bucket ("USD", the default) should find nothing - EPS is filed
    # under "USD/shares".
    wrong_unit_df = concept_to_quarterly_df(basic_json)
    assert wrong_unit_df.empty, "EPS should not appear when reading the USD bucket"

    basic_df = concept_to_quarterly_df(basic_json, units_key="USD/shares")
    diluted_df = concept_to_quarterly_df(diluted_json, units_key="USD/shares")
    assert not basic_df.empty and not diluted_df.empty

    quarters = set(basic_df["quarter_label"])
    assert quarters == {
        "2024Q1", "2024Q2", "2024Q3",
        "2025Q1", "2025Q2", "2025Q3",
        "2026Q1", "2026Q2",
    }, f"unexpected quarter set: {quarters}"
    assert "2024Q4" not in quarters, "Q4 should never be present, same gap as revenue/net income"

    def val(df, end):
        return df.loc[df["period_end"] == end, "value"].iloc[0]

    assert val(basic_df, "2026-06-30") == -1.14
    assert val(diluted_df, "2025-06-30") == -0.60
    # Basic and diluted diverge for Q1 2025 (loss per share still differs
    # slightly pre-dedup across the two tags) - -1.40 vs -1.49.
    assert val(basic_df, "2025-03-31") == -1.40
    assert val(diluted_df, "2025-03-31") == -1.49

    merged = merge_quarterly_series({"eps_basic": basic_df, "eps_diluted": diluted_df})
    assert len(merged) == 8, f"expected 8 aligned quarters, got {len(merged)}\n{merged}"
    row = merged[merged["quarter_label"] == "2026Q2"].iloc[0]
    assert row["eps_basic"] == -1.14
    assert row["eps_diluted"] == -1.14  # equal for this quarter in the real data
    assert merged["period_end"].is_monotonic_increasing

    print("EPS checks passed.")
    print(merged.to_string(index=False))


def test_merge_quarterly_series_handles_mismatched_quarters():
    """
    merge_quarterly_series should outer-join rather than drop a quarter just
    because only one of the two input series has it - e.g. a quarter where
    only basic EPS (not diluted) happened to be tagged should still show up,
    with NaN in the missing column, rather than disappearing from the chart.
    """
    df_a = pd.DataFrame(
        {
            "period_end": pd.to_datetime(["2025-03-31", "2025-06-30"]),
            "value": [1.0, 2.0],
        }
    )
    df_b = pd.DataFrame(
        {
            "period_end": pd.to_datetime(["2025-06-30", "2025-09-30"]),
            "value": [20.0, 30.0],
        }
    )
    merged = merge_quarterly_series({"a": df_a, "b": df_b})

    assert list(merged["quarter_label"]) == ["2025Q1", "2025Q2", "2025Q3"]
    row_q1 = merged[merged["quarter_label"] == "2025Q1"].iloc[0]
    assert row_q1["a"] == 1.0
    assert pd.isna(row_q1["b"])
    row_q3 = merged[merged["quarter_label"] == "2025Q3"].iloc[0]
    assert pd.isna(row_q3["a"])
    assert row_q3["b"] == 30.0

    # An entirely empty named df (e.g. a concept that fetched no data at all)
    # should still produce that column, all-NaN, not raise or vanish.
    merged_with_empty = merge_quarterly_series({"a": df_a, "c": pd.DataFrame()})
    assert "c" in merged_with_empty.columns
    assert merged_with_empty["c"].isna().all()

    print("merge_quarterly_series mismatched-quarters check passed.")


def test_remaining_performance_obligation():
    """
    RPO ("backlog") is point-in-time like total_assets - one figure per
    balance-sheet date, not a per-quarter flow - so it goes straight through
    concept_to_point_in_time_df with no special handling. Checks the values
    against CoreWeave's actual reported RPO growth.
    """
    with open(RPO_FIXTURE) as f:
        concept_json = json.load(f)

    df = concept_to_point_in_time_df(concept_json)
    assert len(df) == 6, f"expected 6 balance-sheet dates, got {len(df)}\n{df}"
    assert df["period_end"].is_monotonic_increasing

    def val(end):
        return df.loc[df["period_end"] == end, "value"].iloc[0]

    assert val("2025-03-31") == 14700000000
    assert val("2026-06-30") == 103700000000

    # Should be monotonically increasing - CoreWeave's backlog has only ever
    # grown quarter over quarter in this data.
    assert df["value"].is_monotonic_increasing, "RPO should be strictly increasing in this sample"

    print("Remaining performance obligation checks passed.")
    print(df[["quarter_label", "value", "form"]].to_string(index=False))


def test_net_margin():
    """
    Net margin = net income / revenue, computed from the same real revenue
    and net income fixtures used by test_revenue/test_net_income. The
    revenue fixture doesn't have every quarter the net income fixture has
    (it's missing 2026Q1) - compute_margin_df should inner-join, so that
    quarter is silently dropped from the margin series rather than
    producing a bogus or NaN margin.
    """
    with open(FIXTURE) as f:
        revenue_json = json.load(f)
    with open(NET_INCOME_FIXTURE) as f:
        ni_json = json.load(f)

    revenue_df = concept_to_quarterly_df(revenue_json)
    ni_df = concept_to_quarterly_df(ni_json)

    margin_df = compute_margin_df(ni_df, revenue_df, margin_col="net_margin")

    # Revenue fixture has 2024Q1-3, 2025Q1-3, 2026Q2 (7 quarters, no 2026Q1).
    # Net income fixture additionally has 2026Q1. The inner join should drop
    # that unmatched quarter rather than erroring or fabricating a value.
    assert len(margin_df) == 7, f"expected 7 quarters where both series overlap, got {len(margin_df)}\n{margin_df}"
    assert not (margin_df["quarter_label"] == "2026Q1").any(), "unmatched quarter should be dropped, not kept"

    def val(label):
        return margin_df.loc[margin_df["quarter_label"] == label, "net_margin"].iloc[0]

    # 2026Q2: net income -626,000,000 / revenue 2,575,000,000 * 100.
    expected_2026q2 = -626000000 / 2575000000 * 100
    assert abs(val("2026Q2") - expected_2026q2) < 0.001, f"{val('2026Q2')} vs {expected_2026q2}"

    assert margin_df["period_end"].is_monotonic_increasing

    print("Net margin checks passed.")
    print(margin_df[["quarter_label", "numerator", "revenue", "net_margin"]].to_string(index=False))


def test_compute_ebitda_df():
    """
    Synthetic inputs (not from a fixture file - these two SEC concepts
    aren't fetched elsewhere in the existing fixtures) shaped exactly like
    concept_to_quarterly_df (operating income) and cumulative_ytd_to_
    discrete_quarterly (D&A) output, verifying the inner-join-and-sum EBITDA
    proxy logic used by load_ttm_ebitda_margin for Forward EBITDA.
    """
    oi_df = pd.DataFrame(
        {
            "period_end": pd.to_datetime(["2025-03-31", "2025-06-30", "2025-09-30"]),
            "quarter_label": ["2025Q1", "2025Q2", "2025Q3"],
            "value": [-100_000_000, -50_000_000, 20_000_000],
        }
    )
    # D&A only has 2025Q1 and 2025Q2 (e.g. 2025Q3's YTD figure couldn't be
    # decomposed yet) - 2025Q3 should be dropped from the result (inner join).
    da_df = pd.DataFrame(
        {
            "period_end": pd.to_datetime(["2025-03-31", "2025-06-30"]),
            "quarter_label": ["2025Q1", "2025Q2"],
            "value": [150_000_000, 160_000_000],
        }
    )

    ebitda_df = compute_ebitda_df(oi_df, da_df)

    assert list(ebitda_df.columns) == [
        "period_end",
        "quarter_label",
        "operating_income",
        "depreciation_amortization",
        "ebitda",
    ]
    assert len(ebitda_df) == 2, f"expected only the 2 overlapping quarters, got {len(ebitda_df)}\n{ebitda_df}"
    assert not (ebitda_df["quarter_label"] == "2025Q3").any(), "non-overlapping quarter should be dropped"

    q1 = ebitda_df[ebitda_df["quarter_label"] == "2025Q1"].iloc[0]
    assert q1["ebitda"] == -100_000_000 + 150_000_000  # 50,000,000
    q2 = ebitda_df[ebitda_df["quarter_label"] == "2025Q2"].iloc[0]
    assert q2["ebitda"] == -50_000_000 + 160_000_000  # 110,000,000

    assert ebitda_df["period_end"].is_monotonic_increasing

    # None/empty inputs should return an empty df with the right columns, not crash.
    empty = compute_ebitda_df(None, da_df)
    assert empty.empty and list(empty.columns) == list(ebitda_df.columns)
    empty2 = compute_ebitda_df(oi_df, pd.DataFrame())
    assert empty2.empty

    print("compute_ebitda_df checks passed.")
    print(ebitda_df.to_string(index=False))


def test_compute_enterprise_value():
    # Normal case: EV = market cap + total debt - cash.
    assert compute_enterprise_value(100.0, 40.0, 10.0) == 130.0
    # Net-cash company (cash > debt) should be able to produce a lower EV
    # than market cap, not get clamped to zero or flipped in sign.
    assert compute_enterprise_value(100.0, 5.0, 20.0) == 85.0
    # Any missing input -> None, not a partial/misleading figure.
    assert compute_enterprise_value(None, 40.0, 10.0) is None
    assert compute_enterprise_value(100.0, None, 10.0) is None
    assert compute_enterprise_value(100.0, 40.0, None) is None
    print("compute_enterprise_value checks passed.")


def test_compute_forward_multiple():
    # Normal case.
    assert compute_forward_multiple(300.0, 100.0) == 3.0
    # Missing numerator or forward revenue -> None.
    assert compute_forward_multiple(None, 100.0) is None
    assert compute_forward_multiple(300.0, None) is None
    # Zero forward revenue -> None, not a divide-by-zero into +/-inf.
    assert compute_forward_multiple(300.0, 0.0) is None
    print("compute_forward_multiple checks passed.")


def test_compute_margin_df_handles_empty_and_zero_revenue():
    empty = pd.DataFrame(columns=["period_end", "quarter_label", "value"])
    non_empty = pd.DataFrame(
        {"period_end": pd.to_datetime(["2025-03-31"]), "quarter_label": ["2025Q1"], "value": [10.0]}
    )

    assert compute_margin_df(None, non_empty).empty
    assert compute_margin_df(non_empty, None).empty
    assert compute_margin_df(empty, non_empty).empty
    assert compute_margin_df(non_empty, empty).empty

    # Zero revenue for the only overlapping quarter should be dropped
    # (would otherwise divide-by-zero into +/-inf), not crash or leak an
    # infinite value into the result.
    zero_revenue = pd.DataFrame({"period_end": pd.to_datetime(["2025-03-31"]), "value": [0.0]})
    result = compute_margin_df(non_empty, zero_revenue, margin_col="margin")
    assert result.empty, f"zero-revenue quarter should be dropped, got:\n{result}"

    print("compute_margin_df empty/zero-revenue checks passed.")


if __name__ == "__main__":
    test_revenue()
    test_total_assets()
    test_point_in_time_ad_hoc_disclosure_collapsed_to_quarter_end()
    test_total_debt_stack()
    test_missing_segment_becomes_all_zero_column()
    test_debt_segment_primary_with_component_fallback()
    test_rpo_falls_back_to_contract_liability_when_primary_tag_goes_stale()
    test_fcf()
    test_net_income()
    test_shares_outstanding_uses_shares_unit()
    test_eps()
    test_merge_quarterly_series_handles_mismatched_quarters()
    test_remaining_performance_obligation()
    test_net_margin()
    test_compute_ebitda_df()
    test_compute_enterprise_value()
    test_compute_forward_multiple()
    test_compute_margin_df_handles_empty_and_zero_revenue()
