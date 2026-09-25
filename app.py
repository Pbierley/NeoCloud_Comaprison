"""
Neocloud Research dashboard - Streamlit app.

Pulls quarterly revenue directly from SEC EDGAR's XBRL API for tracked
neocloud companies and charts it. Run locally with:

    streamlit run app.py

Deploy as-is to Streamlit Community Cloud (share.streamlit.io) once you're
ready - no code changes needed, just push this folder to a GitHub repo.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import (
    COMPANIES,
    COMPANY_DESCRIPTIONS,
    DATA_START_DATE,
    DEBT_SEGMENTS,
    JPMORGAN_MW_RATE_RANGES,
    MW_BY_PURPOSE,
    POWER_CAPACITY,
    RPO_FALLBACK_TAGS,
    RPO_WEB_SOURCED,
    SEGMENT_REVENUE_SPLIT,
    TICKERS,
)
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
from sec_edgar import fetch_concept_by_name, fetch_debt_segments, fetch_revenue_concept, fetch_tagged_concept
from yfinance_data import (
    fetch_capex_yf,
    fetch_ebitda_yf,
    fetch_eps_basic_yf,
    fetch_eps_diluted_yf,
    fetch_forward_revenue_estimate_yf,
    fetch_market_cap_yf,
    fetch_net_income_yf,
    fetch_operating_cash_flow_yf,
    fetch_price_history_yf,
    fetch_revenue_yf,
    fetch_shares_outstanding_yf,
    fetch_ttm_revenue_yf,
)

st.set_page_config(page_title="NeoCloud Research", page_icon="\U0001F4CA", layout="wide")

# Accent colors kept consistent across the app: blue for flow metrics
# (revenue - a bar per quarter), green for stock/balance-sheet metrics (total
# assets - a line, since it's a running snapshot rather than a per-quarter
# amount). Grid kept light so the data reads clearly in both a light and
# dark Streamlit theme.
ACCENT = "#5B8DEF"
ACCENT_SECONDARY = "#2FB380"
GRID = "rgba(128,128,128,0.15)"

# Debt segment colors: paired by obligation type (blue = long-term debt,
# amber = finance leases), light shade = current / short-term, dark shade =
# non-current / long-term, so the stack reads as "how much is due soon" at a
# glance regardless of company.
DEBT_SEGMENT_COLORS = {
    "Long-term debt (current)": "#AFC8F7",
    "Long-term debt (non-current)": "#5B8DEF",
    "Finance lease liabilities (current)": "#F7D9A8",
    "Finance lease liabilities (non-current)": "#DD8A3A",
}


def _is_ai_segment_label(label: str) -> bool:
    """
    config.SEGMENT_REVENUE_SPLIT uses a different exact label per company
    for its AI/HPC-hosting revenue line (Core Scientific: "AI/HPC Hosting
    (Colocation)"; TeraWulf: "AI/HPC Hosting (HPC Lease)"; IREN: "AI Cloud
    Services" - IREN's own income-statement line name, which doesn't
    contain "AI/HPC" or "HPC" at all) - centralized here as one keyword
    check so every "which segment is AI vs. mining" decision in the app
    (chart coloring, AI-hosting-share calculations) stays in sync as new
    companies/label spellings are added, rather than drifting across
    several ad-hoc substring checks.
    """
    return any(kw in label for kw in ("AI/HPC", "Colocation", "HPC", "AI Cloud"))


def _segment_revenue_color(label: str) -> str:
    """
    Color by keyword via _is_ai_segment_label: blue family for any AI/HPC
    hosting line, amber/orange family for any bitcoin-mining line, so the
    stacked chart reads the same way (blue = AI, amber = mining) across
    every company that has this data.

    Hut 8 is the one exception with a genuinely 3-way (not AI-vs-mining)
    segment split - see config.SEGMENT_REVENUE_SPLIT's doc-comment for why
    its "Compute" line is deliberately NOT colored as either pure AI or pure
    mining (it's an undisclosed blend of both) - "Digital Infrastructure"
    and "Power" get their own distinct colors instead of falling into the
    binary blue/amber scheme.
    """
    if _is_ai_segment_label(label):
        return "#5B8DEF"
    if "Hosted Mining" in label:
        return "#F7D9A8"
    if "Digital Infrastructure" in label:
        return "#4FBDBA"
    if "Power (Generation" in label:
        return "#9AA5B1"
    return "#DD8A3A"


# config.POWER_CAPACITY colors: green = live today (most certain), blue =
# signed contract not yet live, light gray = pipeline/diligence-stage (least
# certain) - a traffic-light-style gradient from "real today" to "aspirational".
POWER_CAPACITY_COLORS = {
    "Current (operational)": "#2FB380",
    "Contracted (not yet live)": "#5B8DEF",
    "Pipeline (planned/diligence)": "#C9C9C9",
}

# FCF is commonly negative for capex-heavy neoclouds mid-buildout, so its bar
# is colored by sign rather than a single flat color - green for
# cash-generative quarters, red for cash-burning ones. Net income reuses the
# same two colors/logic for the same reason (also frequently negative here).
FCF_POSITIVE_COLOR = "#2FB380"
FCF_NEGATIVE_COLOR = "#E0584F"

# Shares outstanding gets its own line color, distinct from the green already
# used for total assets, so the two "line chart" panels don't read as the
# same series at a glance.
SHARES_LINE_COLOR = "#9B7EDE"

# Actual shares outstanding (Yahoo Finance) reuses the same purple family as
# the SEC weighted-average shares line but lighter/dashed, since it's the
# same underlying concept (share count) shown from a second source rather
# than something semantically different.
SHARES_YF_LINE_COLOR = "#C9B6EE"

# EPS gets its own two-tone pair (basic/diluted), distinct from every other
# chart's palette so it doesn't get visually confused with net income, which
# sits directly above it and is also frequently negative.
EPS_BASIC_COLOR = "#3AA0C9"
EPS_DILUTED_COLOR = "#1D5F7A"

# Margins (net margin, FCF margin) get their own two-tone pair, echoing the
# EPS basic/diluted pairing convention - a lighter shade for the "narrower"
# metric (FCF margin, which nets out capex on top of everything else) and a
# darker shade for net margin, distinct from every other line/bar color used
# on the same screen (net income, FCF, revenue) since margins sit alongside
# those raw-dollar charts rather than replacing them.
NET_MARGIN_COLOR = "#B45AC2"
FCF_MARGIN_COLOR = "#E3A6ED"

# Forward EBITDA (a PROXY, not real consensus EBITDA guidance - see
# config.py's long note) reuses the RPO green family, since like RPO it's a
# forward-looking / not-yet-realized figure rather than a trailing-quarter
# actual - but a distinct shade so it doesn't read as literally the same
# series when both sit in the same Valuation-style snapshot section.
FORWARD_EBITDA_COLOR = "#2FA88A"

# Forward EV/Sales and Forward P/S - real valuation multiples, not a proxy -
# get their own two-tone pair, echoing the EPS/margins pairing convention:
# EV/Sales (the "cleaner" multiple, capital-structure neutral) in the darker
# shade, P/S (skewed by how much debt/cash a company carries) in the lighter
# one.
FORWARD_EV_SALES_COLOR = "#1D6E8C"
FORWARD_PS_COLOR = "#6FB8D6"

# Stock price chart in the Single Company summary header - a plain accent
# color distinct from every other chart's palette, since price is a totally
# different kind of series (a live market quote) from anything else on the
# page.
PRICE_LINE_COLOR = "#4A7CE0"

# Timeframe options for the adjustable price chart, in display order -
# label shown in the UI -> the yfinance `period` string it maps to.
PRICE_HISTORY_PERIODS = {
    "1M": "1mo",
    "3M": "3mo",
    "6M": "6mo",
    "YTD": "ytd",
    "1Y": "1y",
    "2Y": "2y",
    "5Y": "5y",
    "Max": "max",
}

# Comparison-view palette: one fixed color per company, assigned by that
# company's position in config.COMPANIES, so a given company is always the
# same color across every comparison chart (revenue, net income, etc.) -
# rather than colors shifting around depending on which companies happen to
# have data for a given metric. Sized to cover the full current roster (9
# companies) with no repeats - extend this list (not just the modulo
# wraparound in company_color) before adding a 10th company, or two
# companies will start sharing a color.
COMPANY_COLOR_PALETTE = [
    "#5B8DEF",  # CoreWeave
    "#2FB380",  # Nebius
    "#DD8A3A",  # IREN
    "#E0584F",  # Core Scientific
    "#3AA0C9",  # Hut 8
    "#C9A227",  # TeraWulf
    "#7A6FF0",  # Cipher Mining
    "#4FB8A8",  # CleanSpark
]


def company_color(name: str) -> str:
    names = list(COMPANIES.keys())
    idx = names.index(name) if name in names else 0
    return COMPANY_COLOR_PALETTE[idx % len(COMPANY_COLOR_PALETTE)]


def filter_since_data_start(df: pd.DataFrame, company: str, date_col: str = "period_end") -> pd.DataFrame:
    """
    Drop rows dated before config.DATA_START_DATE[company], if that company
    has an entry there. See config.py's long comment on DATA_START_DATE -
    this exists specifically so Nebius's ~13 years of legacy Yandex-business
    data (still tagged under the same CIK/concepts) doesn't show up
    alongside its actual, much smaller, current AI-cloud business. A no-op
    for any company without an entry, or an empty/None df.
    """
    cutoff = DATA_START_DATE.get(company)
    if not cutoff or df is None or df.empty:
        return df
    return df[df[date_col] >= pd.Timestamp(cutoff)].reset_index(drop=True)


# Remaining performance obligations ("backlog") reuses the same green family
# as total assets - both are "how big is this company" stock metrics that
# only ever grow (or shrink on a big contract change), as opposed to the
# volatile per-quarter flow metrics (revenue, FCF, net income).
RPO_LINE_COLOR = "#1F8F5F"

# Distinct color/style for RPO series sourced from config.RPO_WEB_SOURCED
# (manually researched via web search, not SEC XBRL) - a muted amber,
# dashed, so it never reads as an ordinary filed data line at a glance.
RPO_WEB_SOURCED_COLOR = "#B8860B"


def _augment_yf_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pad a yfinance-derived quarterly df with the extra columns the SEC path
    always produces (period_start, fy, fp, form, filed) so the rest of the
    app - which was written against the SEC shape - can display either
    source through the same code (metrics, chart, "Underlying data" table)
    without a second branch everywhere. Values are placeholders: yfinance's
    quarterly statements don't carry an exact period start date or SEC
    filing metadata the way XBRL does.
    """
    df = df.copy()
    df["period_start"] = pd.NaT
    df["fy"] = None
    df["fp"] = None
    df["form"] = "Yahoo Finance"
    df["filed"] = pd.NaT
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_revenue(cik: str, ticker: str | None = None):
    """
    Returns (df, tag_used, source) where source is "sec", "yfinance", or
    None (nothing found anywhere). Falls back to Yahoo Finance's quarterly
    income statement only when SEC has NO usable quarterly revenue at all -
    e.g. a foreign private issuer filing 20-F/6-K instead of 10-K/10-Q
    (Nebius), which never gets a discrete-quarter XBRL tag through SEC's
    API. See yfinance_data.fetch_quarterly_metric_yf for the caveat that
    yfinance only covers the trailing several quarters, not deep history.
    """
    concept_json, tag_used = fetch_revenue_concept(cik)
    if concept_json is not None:
        df = concept_to_quarterly_df(concept_json)
        if not df.empty:
            return add_yoy_growth(df), tag_used, "sec"

    yf_df = fetch_revenue_yf(ticker) if ticker else None
    if yf_df is None or yf_df.empty:
        return None, tag_used, None
    yf_df = add_yoy_growth(_augment_yf_df(yf_df))
    return yf_df, None, "yfinance"


@st.cache_data(ttl=3600, show_spinner=False)
def load_net_income(cik: str, ticker: str | None = None):
    """Same SEC-first, Yahoo-Finance-fallback pattern as load_revenue - see
    its docstring for why this fallback exists."""
    concept_json, tag_used = fetch_concept_by_name(cik, "net_income")
    if concept_json is not None:
        df = concept_to_quarterly_df(concept_json)
        if not df.empty:
            return add_yoy_growth(df), tag_used, "sec"

    yf_df = fetch_net_income_yf(ticker) if ticker else None
    if yf_df is None or yf_df.empty:
        return None, tag_used, None
    yf_df = add_yoy_growth(_augment_yf_df(yf_df))
    return yf_df, None, "yfinance"


@st.cache_data(ttl=3600, show_spinner=False)
def load_shares_outstanding(cik: str):
    concept_json, tag_used = fetch_concept_by_name(cik, "shares_outstanding")
    if concept_json is None:
        return None, None
    # "shares_outstanding" is actually weighted-average BASIC shares (a share
    # count, not a dollar figure) - see config.py's note on why - so it's
    # keyed under the "shares" unit bucket, not the "USD" default.
    df = concept_to_quarterly_df(concept_json, units_key="shares")
    if df.empty:
        return None, tag_used
    return df, tag_used


@st.cache_data(ttl=3600, show_spinner=False)
def load_eps(cik: str, ticker: str | None = None):
    """
    Earnings per share - basic and diluted, both standard discrete-quarter
    us-gaap tags (same pattern as net_income/revenue, no YTD decomposition).
    Fetched and merged separately rather than treated as one "first match
    wins" fallback list, since a company reports both figures side by side
    every quarter, not one-or-the-other.

    Falls back to Yahoo Finance (same as load_revenue/load_net_income) only
    when SEC has neither figure at all for any quarter.

    Returns (merged_df, tags_used, source) where merged_df has columns
    period_end, quarter_label, eps_basic, eps_diluted (either can be NaN for
    a quarter the other wasn't reported for - see merge_quarterly_series),
    tags_used = {"eps_basic": tag_or_None, "eps_diluted": tag_or_None} (SEC
    tags tried, regardless of which source ultimately supplied the data),
    and source is "sec", "yfinance", or None.
    """
    basic_json, basic_tag = fetch_concept_by_name(cik, "eps_basic")
    diluted_json, diluted_tag = fetch_concept_by_name(cik, "eps_diluted")
    tags_used = {"eps_basic": basic_tag, "eps_diluted": diluted_tag}

    basic_df = (
        concept_to_quarterly_df(basic_json, units_key="USD/shares") if basic_json else pd.DataFrame()
    )
    diluted_df = (
        concept_to_quarterly_df(diluted_json, units_key="USD/shares") if diluted_json else pd.DataFrame()
    )
    if not basic_df.empty or not diluted_df.empty:
        merged = merge_quarterly_series({"eps_basic": basic_df, "eps_diluted": diluted_df})
        if not merged.empty:
            return merged, tags_used, "sec"

    if not ticker:
        return None, tags_used, None

    yf_basic = fetch_eps_basic_yf(ticker)
    yf_basic = yf_basic if yf_basic is not None else pd.DataFrame()
    yf_diluted = fetch_eps_diluted_yf(ticker)
    yf_diluted = yf_diluted if yf_diluted is not None else pd.DataFrame()
    if yf_basic.empty and yf_diluted.empty:
        return None, tags_used, None

    merged = merge_quarterly_series({"eps_basic": yf_basic, "eps_diluted": yf_diluted})
    if merged.empty:
        return None, tags_used, None
    return merged, tags_used, "yfinance"


@st.cache_data(ttl=3600, show_spinner=False)
def load_shares_outstanding_yf(ticker: str):
    """
    Actual point-in-time shares outstanding from Yahoo Finance, cached
    separately from the SEC-derived weighted-average series (see
    yfinance_data.py). Returns None if unavailable for any reason - the
    caller shows the SEC line regardless and just skips this overlay.
    """
    return fetch_shares_outstanding_yf(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def load_rpo(cik: str, company: str | None = None):
    """
    Remaining performance obligations ("backlog") - contracted, not-yet-
    recognized future revenue. Point-in-time like total_assets (an "as of"
    balance/footnote figure, not a per-quarter flow), so same base parsing
    path.

    Some filers stop reporting RevenueRemainingPerformanceObligation at some
    point while continuing to disclose a related but NARROWER figure
    (contract liability/deferred revenue) under a different tag - see
    config.RPO_FALLBACK_TAGS's long caveat on why these aren't truly
    scope-equivalent. When `company` has an entry there, this merges in
    that fallback tag for any date the primary RPO tag doesn't cover, using
    the same "primary value where reported, else fallback" rule
    build_debt_segment_df already implements for DEBT_SEGMENTS - reused
    here with a single fallback "component" rather than summed ones.

    Returns (df, primary_tag_used, fallback_tag_used) where fallback_tag_used
    is the "taxonomy:tag" string if ANY returned date came from the
    fallback, else None (so callers can caption it when relevant).
    """
    concept_json, tag_used = fetch_concept_by_name(cik, "remaining_performance_obligation")

    fallback_specs = RPO_FALLBACK_TAGS.get(company, []) if company else []
    if not fallback_specs:
        if concept_json is None:
            return None, None, None
        df = concept_to_point_in_time_df(concept_json)
        if df.empty:
            return None, tag_used, None
        return df, tag_used, None

    component_jsons = [
        (fetch_tagged_concept(cik, taxonomy, tag), f"{taxonomy}:{tag}") for taxonomy, tag in fallback_specs
    ]
    merged = build_debt_segment_df(concept_json, component_jsons)
    if merged.empty:
        return None, tag_used, None

    used_fallback = (merged["source"] == "components").any()
    fallback_tag_used = component_jsons[0][1] if used_fallback else None
    return merged[["period_end", "quarter_label", "value"]], tag_used, fallback_tag_used


def load_web_sourced_rpo_df(company: str):
    """
    Builds a DataFrame from config.RPO_WEB_SOURCED - manually researched
    "total contract value" disclosures pulled from press releases/8-K
    exhibits/investor decks via web search, for companies (Core Scientific,
    Hut 8, TeraWulf, Cipher Mining, CleanSpark) that have NO usable SEC XBRL
    RPO data at all. See config.py's long caveat before trusting this as a
    real quarterly RPO series: these are irregularly-dated, sometimes
    cumulative-across-different-counterparties figures, not a standardized
    filing. Not cached with st.cache_data since it's just an in-memory
    literal - no network call to cache. Returns None if nothing configured
    for this company.
    """
    points = RPO_WEB_SOURCED.get(company)
    if not points:
        return None
    df = pd.DataFrame(points)
    df["period_end"] = pd.to_datetime(df["date"])
    df = df.sort_values("period_end").reset_index(drop=True)
    df["quarter_label"] = df["period_end"].dt.year.astype(str) + "Q" + df["period_end"].dt.quarter.astype(str)
    return df[["period_end", "quarter_label", "value", "note", "source"]]


def load_segment_revenue_df(company: str):
    """
    Builds a wide per-quarter DataFrame from config.SEGMENT_REVENUE_SPLIT -
    manually compiled from each company's own FILED income-statement revenue
    line items (bitcoin mining vs. AI/HPC hosting), not a live SEC XBRL pull.
    See config.py's long caveat: this breakdown is DIMENSIONAL XBRL data
    (a ProductOrServiceAxis-style member), which SEC's companyconcept/
    companyfacts endpoints never return regardless of tag name, and
    xbrl_instance.py's raw-instance parser explicitly skips dimensional
    facts too - so this is sourced by hand from each company's own earnings
    -release income statement instead. Unlike RPO_WEB_SOURCED, these ARE
    real filed GAAP figures, just not ones this app can fetch automatically.

    Returns (df, segment_cols) where df has period_end/quarter_label/total
    plus one column per segment label, or (None, []) if nothing configured
    for this company. Not cached with st.cache_data - no network call, just
    reshaping an in-memory literal.
    """
    points = SEGMENT_REVENUE_SPLIT.get(company)
    if not points:
        return None, []
    rows = []
    for p in points:
        row = {"period_end": pd.to_datetime(p["period_end"]), "source": p["source"]}
        row.update(p["segments"])
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("period_end").reset_index(drop=True)
    df["quarter_label"] = df["period_end"].dt.year.astype(str) + "Q" + df["period_end"].dt.quarter.astype(str)
    segment_cols = [c for c in df.columns if c not in ("period_end", "quarter_label", "source")]
    df["total"] = df[segment_cols].sum(axis=1)
    return df, segment_cols


def get_power_capacity(company: str):
    """
    Returns config.POWER_CAPACITY's entry for `company` (a dict) or None.
    Not a @st.cache_data loader - no network call, just an in-memory
    literal lookup, same as load_web_sourced_rpo_df/load_segment_revenue_df.
    See config.py's long caveat: this is a manually-researched SNAPSHOT (as
    of each company's most recent disclosure), not a live pull or a time
    series - power capacity in MW isn't a GAAP/XBRL concept at all.
    """
    return POWER_CAPACITY.get(company)


def load_rpo_with_web_fallback(cik: str, company: str):
    """
    Wraps load_rpo with config.RPO_WEB_SOURCED as a LAST-RESORT fallback,
    used only when SEC XBRL has nothing usable at all for this company (not
    even a narrower RPO_FALLBACK_TAGS figure) - AFTER filter_since_data_start
    is applied, not before. Applying the DATA_START_DATE cutoff here (rather
    than letting the caller apply it afterward) matters: Core Scientific's
    only XBRL RevenueRemainingPerformanceObligation data is pre-fresh-start
    (2021-2022, all before its 2024-01-23 DATA_START_DATE cutoff), so it's
    technically "non-empty" before filtering - if the fallback decision were
    made on the unfiltered df, it would wrongly commit to that stale SEC data
    and then filter it down to nothing, never reaching the web series. This
    is now the single place that decides AND filters, so callers should NOT
    call filter_since_data_start on the result again (harmless if they do -
    idempotent - but unnecessary).

    Returns (df, tag_used, fallback_tag_used, web_sourced) where web_sourced
    is True only when the returned df came entirely from the manually-
    researched web series rather than any SEC filing - callers must flag
    that distinctly (different line style/color, its own caption) rather
    than presenting it as if it were XBRL data.
    """
    df, tag_used, fallback_tag_used = load_rpo(cik, company)
    df = filter_since_data_start(df, company)
    if df is not None and not df.empty:
        return df, tag_used, fallback_tag_used, False
    web_df = load_web_sourced_rpo_df(company)
    web_df = filter_since_data_start(web_df, company)
    if web_df is None or web_df.empty:
        return df, tag_used, fallback_tag_used, False
    return web_df, None, None, True


@st.cache_data(ttl=900, show_spinner=False)
def load_market_cap(ticker: str):
    """
    Current market cap from Yahoo Finance. Cached for a much shorter TTL
    (15 min) than every other loader in this app (1 hour) - unlike a
    quarterly SEC figure, this genuinely changes throughout the trading day,
    so a long cache would make the Valuation Snapshot noticeably stale.
    Returns None (never raises) if unavailable - see fetch_market_cap_yf.
    """
    return fetch_market_cap_yf(ticker)


@st.cache_data(ttl=900, show_spinner=False)
def load_price_history(ticker: str, period: str):
    """
    Daily closing price history for `ticker` over `period` (a yfinance
    period string - see PRICE_HISTORY_PERIODS for the UI-facing options),
    used for the Single Company summary header's adjustable-timeframe price
    chart. Same short 15-min TTL as load_market_cap, since this is a live
    daily-updating series, not a quarterly SEC figure.
    """
    return fetch_price_history_yf(ticker, period)


@st.cache_data(ttl=3600, show_spinner=False)
def load_total_assets(cik: str):
    concept_json, tag_used = fetch_concept_by_name(cik, "total_assets")
    if concept_json is None:
        return None, None
    df = concept_to_point_in_time_df(concept_json)
    if df.empty:
        return None, tag_used
    return df, tag_used


@st.cache_data(ttl=3600, show_spinner=False)
def load_cash(cik: str):
    """
    Cash & cash equivalents, latest balance-sheet snapshot - used only to
    compute Enterprise Value (EV = market cap + total debt - cash) for the
    Forward EV/Sales metric. Point-in-time, same parsing path as
    total_assets/RPO.
    """
    concept_json, tag_used = fetch_concept_by_name(cik, "cash")
    if concept_json is None:
        return None, None
    df = concept_to_point_in_time_df(concept_json)
    if df.empty:
        return None, tag_used
    return df, tag_used


@st.cache_data(ttl=3600, show_spinner=False)
def load_debt_segments(cik: str):
    """
    Returns (stacked_df, info) where info[label] describes how that segment's
    values were sourced:
        {
            "primary_tag": "us-gaap:LongTermDebtCurrent" or None,
            "component_tags_used": [tags that actually had data],
            "used_components_for": [quarter_labels where the primary tag had
                                     no value and components were summed instead],
        }
    See config.DEBT_SEGMENTS for why a segment can need both a primary tag
    and a components fallback (CoreWeave's recourse/non-recourse debt split).
    """
    raw = fetch_debt_segments(cik)
    segment_dfs = {}
    info = {}
    for label, seg in raw.items():
        primary_json, primary_tag = seg["primary"]
        component_jsons = seg["components"]
        merged = build_debt_segment_df(primary_json, component_jsons)

        segment_dfs[label] = merged[["period_end", "value"]] if not merged.empty else pd.DataFrame()
        info[label] = {
            "primary_tag": primary_tag,
            "component_tags_used": [
                lbl for cj, lbl in component_jsons if cj is not None
            ],
            "used_components_for": (
                merged.loc[merged["source"] == "components", "quarter_label"].tolist()
                if not merged.empty
                else []
            ),
        }

    stacked = build_stacked_segment_df(segment_dfs)
    if stacked.empty:
        return None, info
    return stacked, info


@st.cache_data(ttl=3600, show_spinner=False)
def load_fcf(cik: str, ticker: str | None = None):
    """
    Free cash flow = operating cash flow - capex, reconstructed per quarter.
    Both SEC inputs are reported as year-to-date cumulative figures (not
    discrete quarters like revenue), so this goes through the
    concept_to_ytd_cumulative_df -> cumulative_ytd_to_discrete_quarterly
    pipeline for each before subtracting - see config.py's note on
    "operating_cash_flow" / "capex" for why.

    Falls back to Yahoo Finance's quarterly cash flow statement (same
    pattern as load_revenue/load_net_income/load_eps) only when SEC has no
    usable quarterly cash-flow data at all - e.g. Nebius, which files an
    annual 20-F rather than 10-Qs, so there's no YTD-cumulative figures to
    decompose in the first place. FCF is still computed the same way
    (OCF - capex) from the two yfinance rows, for consistency with the
    SEC-derived path.

    Returns (fcf_df, tags_used, source) where tags_used =
    {"operating_cash_flow": tag_or_None, "capex": tag_or_None} (the SEC tags
    tried, regardless of which source ultimately supplied the data), and
    source is "sec", "yfinance", or None.
    """
    ocf_json, ocf_tag = fetch_concept_by_name(cik, "operating_cash_flow")
    capex_json, capex_tag = fetch_concept_by_name(cik, "capex")
    tags_used = {"operating_cash_flow": ocf_tag, "capex": capex_tag}

    if ocf_json is not None and capex_json is not None:
        ocf_quarterly = cumulative_ytd_to_discrete_quarterly(concept_to_ytd_cumulative_df(ocf_json))
        capex_quarterly = cumulative_ytd_to_discrete_quarterly(concept_to_ytd_cumulative_df(capex_json))
        fcf_df = compute_fcf_df(ocf_quarterly, capex_quarterly)
        if not fcf_df.empty:
            return fcf_df, tags_used, "sec"

    if not ticker:
        return None, tags_used, None

    yf_ocf = fetch_operating_cash_flow_yf(ticker)
    yf_capex = fetch_capex_yf(ticker)
    if yf_ocf is None or yf_ocf.empty or yf_capex is None or yf_capex.empty:
        return None, tags_used, None

    fcf_df = compute_fcf_df(yf_ocf, yf_capex)
    if fcf_df.empty:
        return None, tags_used, None
    return fcf_df, tags_used, "yfinance"


@st.cache_data(ttl=3600, show_spinner=False)
def load_ttm_ebitda_margin(cik: str, ticker: str | None = None):
    """
    Trailing EBITDA margin - the multiplier half of the Forward EBITDA proxy
    (see config.py's long note on why this whole feature is a proxy, not
    real consensus EBITDA guidance). Trailing EBITDA = operating income +
    D&A, summed over the most recent up-to-4 discrete quarters; trailing
    margin = that sum / the matching quarters' summed revenue.

    Tries SEC first: operating income (a plain discrete-quarter tag, like
    net income) + D&A (a YTD-cumulative cash-flow-statement tag, decomposed
    the same way operating_cash_flow/capex are for FCF) via compute_ebitda_
    df, joined against load_revenue's already-cached SEC/yfinance revenue.

    Falls back to Yahoo Finance's trailing `info['ebitda']` /
    `info['totalRevenue']` only when SEC has no usable operating income/D&A
    data to build a quarterly EBITDA series from at all (e.g. Nebius, same
    underlying "no quarterly XBRL" gap as everywhere else in this app for
    that company) - in that case there's no quarters_used to report, since
    Yahoo's TTM figure isn't quarter-decomposed.

    Returns {"margin": pct_or_None, "ttm_ebitda": float_or_None,
    "ttm_revenue": float_or_None, "quarters_used": int_or_None,
    "source": "sec" | "yfinance" | None}.
    """
    empty = {"margin": None, "ttm_ebitda": None, "ttm_revenue": None, "quarters_used": None, "source": None}

    oi_json, _oi_tag = fetch_concept_by_name(cik, "operating_income")
    da_json, _da_tag = fetch_concept_by_name(cik, "depreciation_amortization")

    if oi_json is not None and da_json is not None:
        oi_df = concept_to_quarterly_df(oi_json)
        da_df = cumulative_ytd_to_discrete_quarterly(concept_to_ytd_cumulative_df(da_json))
        ebitda_df = compute_ebitda_df(oi_df, da_df)
        if not ebitda_df.empty:
            revenue_df, _rev_tag, _rev_source = load_revenue(cik, ticker)
            if revenue_df is not None and not revenue_df.empty:
                merged = ebitda_df[["period_end", "quarter_label", "ebitda"]].merge(
                    revenue_df[["period_end", "value"]], on="period_end", how="inner"
                )
                merged = merged.rename(columns={"value": "revenue"}).sort_values("period_end").tail(4)
                ttm_revenue = merged["revenue"].sum()
                if not merged.empty and ttm_revenue != 0:
                    ttm_ebitda = merged["ebitda"].sum()
                    return {
                        "margin": ttm_ebitda / ttm_revenue * 100,
                        "ttm_ebitda": ttm_ebitda,
                        "ttm_revenue": ttm_revenue,
                        "quarters_used": len(merged),
                        "source": "sec",
                    }

    if not ticker:
        return empty

    yf_ebitda = fetch_ebitda_yf(ticker)
    yf_revenue = fetch_ttm_revenue_yf(ticker)
    if yf_ebitda is None or not yf_revenue:
        return empty

    return {
        "margin": yf_ebitda / yf_revenue * 100,
        "ttm_ebitda": yf_ebitda,
        "ttm_revenue": yf_revenue,
        "quarters_used": None,
        "source": "yfinance",
    }


@st.cache_data(ttl=3600, show_spinner=False)
def load_forward_revenue_estimate(ticker: str | None):
    """
    Next-fiscal-year consensus analyst revenue estimate from Yahoo Finance -
    shared by Forward EBITDA and Forward EV/Sales / P/S so it's only fetched
    once per ticker rather than once per feature. Returns
    fetch_forward_revenue_estimate_yf's dict ({"avg", "low", "high",
    "analysts"}) or None.
    """
    if not ticker:
        return None
    return fetch_forward_revenue_estimate_yf(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def load_forward_ebitda(cik: str, ticker: str | None = None):
    """
    Forward EBITDA PROXY = next-fiscal-year consensus analyst revenue
    estimate (yfinance) x trailing EBITDA margin (load_ttm_ebitda_margin).
    See config.py's note on why this is a proxy, not real analyst EBITDA
    guidance - yfinance has no EBITDA-specific consensus estimate at all.

    Returns load_ttm_ebitda_margin's dict plus: "forward_revenue_avg" /
    "forward_revenue_low" / "forward_revenue_high" (next-FY consensus
    revenue estimate), "analysts" (how many analysts feed that estimate),
    and "forward_ebitda" (None unless BOTH the margin and the forward
    revenue estimate are available).
    """
    result = dict(load_ttm_ebitda_margin(cik, ticker))
    result.update(
        {
            "forward_revenue_avg": None,
            "forward_revenue_low": None,
            "forward_revenue_high": None,
            "analysts": None,
            "forward_ebitda": None,
        }
    )

    est = load_forward_revenue_estimate(ticker)
    if est is None:
        return result

    result["forward_revenue_avg"] = est["avg"]
    result["forward_revenue_low"] = est["low"]
    result["forward_revenue_high"] = est["high"]
    result["analysts"] = est["analysts"]
    if result["margin"] is not None:
        result["forward_ebitda"] = est["avg"] * result["margin"] / 100
    return result


@st.cache_data(ttl=900, show_spinner=False)
def load_enterprise_value(cik: str, ticker: str | None):
    """
    Enterprise Value = market cap + total debt - cash & equivalents, each
    the latest available figure. Cached at the same short 15-min TTL as
    load_market_cap, since EV moves every trading day right along with
    market cap even though the debt/cash inputs only update once a quarter.

    Returns {"market_cap": float_or_None, "total_debt": float_or_None,
    "debt_as_of": Timestamp_or_None, "cash": float_or_None,
    "cash_as_of": Timestamp_or_None, "ev": float_or_None (None unless all
    three of market cap, total debt, and cash are available)}.
    """
    market_cap = load_market_cap(ticker) if ticker else None

    stacked, _debt_info = load_debt_segments(cik)
    total_debt, debt_as_of = None, None
    if stacked is not None and not stacked.empty:
        latest_debt = stacked.iloc[-1]
        total_debt = latest_debt["total"]
        debt_as_of = latest_debt["period_end"]

    cash_df, _cash_tag = load_cash(cik)
    cash, cash_as_of = None, None
    if cash_df is not None and not cash_df.empty:
        latest_cash = cash_df.iloc[-1]
        cash = latest_cash["value"]
        cash_as_of = latest_cash["period_end"]

    ev = compute_enterprise_value(market_cap, total_debt, cash)

    return {
        "market_cap": market_cap,
        "total_debt": total_debt,
        "debt_as_of": debt_as_of,
        "cash": cash,
        "cash_as_of": cash_as_of,
        "ev": ev,
    }


@st.cache_data(ttl=900, show_spinner=False)
def load_forward_sales_multiples(cik: str, ticker: str | None):
    """
    Forward EV/Sales and Forward P/S - real valuation multiples (not a
    proxy like Forward EBITDA), built from Enterprise Value / market cap
    (load_enterprise_value) and the next-fiscal-year consensus revenue
    estimate (load_forward_revenue_estimate, shared with Forward EBITDA).
    See config.py's note for the "as of different dates" caveat (live
    market cap vs. lagged filed debt/cash).

    Returns load_enterprise_value's dict plus: "forward_revenue_avg" /
    "analysts", "forward_ev_sales" (EV / forward revenue, None unless both
    are available), and "forward_ps" (market cap / forward revenue, None
    unless both are available).
    """
    result = dict(load_enterprise_value(cik, ticker))
    result.update({"forward_revenue_avg": None, "analysts": None, "forward_ev_sales": None, "forward_ps": None})

    est = load_forward_revenue_estimate(ticker)
    if est is None or not est.get("avg"):
        return result

    result["forward_revenue_avg"] = est["avg"]
    result["analysts"] = est["analysts"]
    result["forward_ev_sales"] = compute_forward_multiple(result["ev"], est["avg"])
    result["forward_ps"] = compute_forward_multiple(result["market_cap"], est["avg"])
    return result


def format_usd(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.1f}M"
    return f"${value:,.0f}"


def format_pct(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.1f}%"


def format_multiple(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{value:,.1f}x"


def format_mw(value: float) -> str:
    if value is None or pd.isna(value):
        return "-"
    if abs(value) >= 1000:
        return f"{value / 1000:,.2f} GW"
    return f"{value:,.0f} MW"


@st.cache_data(ttl=3600, show_spinner=False)
def load_net_margin(cik: str, ticker: str | None = None):
    """
    Net margin = net income / revenue * 100, per quarter. Reuses the same
    cached revenue/net-income loaders as their own sections above (whichever
    source - SEC or yfinance - they resolved to), then inner-joins the two
    on period_end via compute_margin_df, so a quarter only shows a margin
    once both figures are actually on file for it.

    Returns margin_df with columns period_end, quarter_label, numerator
    (net income), revenue, net_margin - or an empty df if either input is
    missing.
    """
    revenue_df, _rev_tag, _rev_source = load_revenue(cik, ticker)
    ni_df, _ni_tag, _ni_source = load_net_income(cik, ticker)
    return compute_margin_df(ni_df, revenue_df, margin_col="net_margin")


@st.cache_data(ttl=3600, show_spinner=False)
def load_fcf_margin(cik: str, ticker: str | None = None):
    """
    FCF margin = free cash flow / revenue * 100, per quarter. Same pattern
    as load_net_margin, but against the "fcf" column of load_fcf's output
    (operating cash flow minus capex) instead of net income.

    Returns margin_df with columns period_end, quarter_label, numerator
    (FCF), revenue, fcf_margin - or an empty df if either input is missing.
    """
    revenue_df, _rev_tag, _rev_source = load_revenue(cik, ticker)
    fcf_df, _fcf_tags, _fcf_source = load_fcf(cik, ticker)
    if fcf_df is not None and not fcf_df.empty:
        fcf_df = fcf_df.rename(columns={"fcf": "value"})
    return compute_margin_df(fcf_df, revenue_df, margin_col="fcf_margin")


def render_comparison_chart(
    title: str,
    y_title: str,
    company_dfs: dict,
    formatter,
    chart_type: str = "bar",
    tickprefix: str = "",
    ticksuffix: str = "",
):
    """
    One chart with one trace per tracked company (color from company_color),
    used throughout the Compare Companies view.

    company_dfs: {company_name: df_or_None}, where each df has at least
    quarter_label and value columns - shaped exactly like the per-company
    loaders already used in the Single Company view (see render_comparison_
    view, which builds these dicts from those same cached loaders). A
    company with no data for this metric (None or empty df) is simply
    skipped rather than erroring - its name is listed in a caption below the
    chart instead, so it's clear the gap is a real data gap, not a bug.
    """
    available = {name: df for name, df in company_dfs.items() if df is not None and not df.empty}
    missing = [name for name in company_dfs if name not in available]

    if not available:
        st.info("No data available for any tracked company yet.")
        return

    fig = go.Figure()
    for name, df in available.items():
        color = company_color(name)
        common = dict(
            x=df["quarter_label"],
            y=df["value"],
            name=name,
            hovertemplate=f"%{{x}}<br>{name}: %{{customdata}}<extra></extra>",
            customdata=[formatter(v) for v in df["value"]],
        )
        if chart_type == "bar":
            fig.add_trace(go.Bar(marker_color=color, **common))
        else:
            fig.add_trace(
                go.Scatter(mode="lines+markers", line=dict(color=color, width=2), marker=dict(size=6), **common)
            )

    fig.update_layout(
        title=title,
        xaxis_title=None,
        yaxis_title=y_title,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(
            gridcolor=GRID,
            tickprefix=tickprefix,
            ticksuffix=ticksuffix,
            zeroline=True,
            zerolinecolor=GRID,
            zerolinewidth=1,
        ),
        # Force chronological x-axis order explicitly - quarter_label strings
        # sort correctly as plain text ("2011Q4" < "2024Q1" < "2026Q2"), but
        # Plotly's categorical-axis default is "first appearance across
        # traces", not sorted. Without this, a company added in a later
        # trace whose quarters weren't already seen (e.g. Nebius's much
        # older data) gets its new categories tacked onto the END of the
        # axis instead of interleaved chronologically - exactly the "jumps
        # back to 2011 after 2026" bug this fixes.
        xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
        barmode="group",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=60, l=10, r=10, b=10),
        height=420,
    )
    st.plotly_chart(fig, use_container_width=True)
    if missing:
        st.caption(f"No data available for: {', '.join(missing)}.")


def render_valuation_snapshot():
    """
    Market Cap vs. RPO ("backlog") - a valuation SNAPSHOT, not a time
    series like every other chart in this app. Market cap is a LIVE number
    (Yahoo Finance, as of whenever this page happens to load); RPO is each
    company's most recently FILED quarter with SEC - these two numbers are
    "as of" different dates by construction, since one updates every
    trading day and the other updates once a quarter. That's flagged
    explicitly in the caption rather than hidden, since silently combining
    a live price-based number with a lagged filing number is exactly the
    kind of thing that looks precise but isn't.

    The ratio (Market Cap / RPO) is a rough read on how many dollars of
    market value the market is assigning per dollar of already-signed,
    not-yet-delivered revenue - conceptually similar to a forward-looking
    Price/Sales multiple, using contracted backlog instead of trailing
    sales. A lower ratio isn't "cheaper" or "better" on its own - it just
    means more of the current market cap is already covered by signed
    contracts rather than by expectations of future, unsigned business.
    """
    st.subheader("Valuation Snapshot: Market Cap vs. RPO")
    st.caption(
        "Market cap is a LIVE snapshot from Yahoo Finance (as of whenever this page "
        "loaded); RPO is each company's most recently filed quarter with SEC - these "
        "are 'as of' different dates by design, since one moves every trading day and "
        "the other only updates once a quarter. The multiple is market cap ÷ RPO: "
        "roughly, how many dollars of market value per dollar of already-signed, "
        "not-yet-delivered revenue. Lower isn't inherently 'cheaper' - it just means "
        "more of the market cap is already backed by contracted backlog."
    )

    rows = []
    fallback_companies = []
    web_sourced_companies = []
    for name in COMPANIES.keys():
        cik = COMPANIES[name]
        ticker = TICKERS.get(name)
        market_cap = load_market_cap(ticker) if ticker else None

        rpo_df, _rpo_tag, rpo_fallback_tag, rpo_web_sourced = load_rpo_with_web_fallback(cik, name)
        rpo_df = filter_since_data_start(rpo_df, name)
        latest_rpo = None
        rpo_as_of = None
        if rpo_df is not None and not rpo_df.empty:
            latest_row = rpo_df.iloc[-1]
            latest_rpo = latest_row["value"]
            rpo_as_of = latest_row["period_end"]

        # config.RPO_FALLBACK_TAGS (e.g. Applied Digital) can make "RPO" here
        # actually be a much NARROWER fallback figure (contract liability /
        # deferred revenue, not total remaining contracted revenue) once the
        # real RPO tag goes stale - see load_rpo's docstring. That can make
        # Market Cap / RPO balloon to a huge, non-comparable multiple purely
        # because the denominator is measuring something smaller, not
        # because the company is actually "more expensive" on this basis -
        # flagged with an asterisk rather than silently shown as a normal
        # multiple.
        is_fallback = bool(rpo_fallback_tag) and rpo_as_of is not None
        if is_fallback:
            fallback_companies.append(name)
        if rpo_web_sourced:
            web_sourced_companies.append(name)

        rows.append(
            {
                "Company": name,
                "RPO fallback": is_fallback,
                "RPO web-sourced": rpo_web_sourced,
                "Market Cap": market_cap,
                "RPO": latest_rpo,
                "RPO as of": rpo_as_of,
                "Market Cap / RPO": (market_cap / latest_rpo) if (market_cap and latest_rpo) else None,
            }
        )

    snap_df = pd.DataFrame(rows)
    if snap_df["Market Cap"].isna().all() and snap_df["RPO"].isna().all():
        st.info("No market cap or RPO data available for any tracked company yet.")
        return

    # "Company" stays a clean name throughout snap_df (used for chart x-axis
    # joins and the "missing" check below) - the " *"/" †" flags are added
    # only in the displayed table, so they never break a `== company name`
    # comparison elsewhere.
    display_df = snap_df.drop(columns=["RPO fallback", "RPO web-sourced"]).copy()
    display_df["Company"] = (
        snap_df["Company"]
        + snap_df["RPO fallback"].map({True: " *", False: ""})
        + snap_df["RPO web-sourced"].map({True: " †", False: ""})
    )
    display_df["Market Cap"] = display_df["Market Cap"].apply(format_usd)
    display_df["RPO"] = display_df["RPO"].apply(format_usd)
    display_df["RPO as of"] = display_df["RPO as of"].apply(lambda d: d.date() if pd.notna(d) else "-")
    display_df["Market Cap / RPO"] = display_df["Market Cap / RPO"].apply(
        lambda v: f"{v:,.1f}x" if pd.notna(v) else "-"
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    if fallback_companies:
        st.caption(
            f"⚠️ * {', '.join(fallback_companies)}: SEC has no current RPO tag for this "
            "company (see the Single Company view for details), so the figure above is a "
            "narrower fallback metric (contract liability / deferred revenue - billed-but-"
            "undelivered only, not total remaining contracted revenue). Its Market Cap ÷ RPO "
            "multiple is measuring against a much smaller denominator than the other "
            "companies' true RPO, so it isn't comparable to theirs and can look artificially "
            "huge - that's a definition mismatch, not a real valuation signal."
        )
    if web_sourced_companies:
        st.caption(
            f"📰 † {', '.join(web_sourced_companies)}: SEC XBRL has no RPO data at all, so "
            "the figure above is instead the latest disclosed total contract value, "
            "manually researched from press releases/8-K exhibits (not a filed fact - see "
            "config.py's RPO_WEB_SOURCED). This is a much BROADER figure than the other "
            "companies' RPO (total value across a multi-year deal, not a single balance-"
            "sheet-date backlog), so its Market Cap ÷ RPO multiple is measuring against a "
            "differently-scoped denominator and isn't comparable to the XBRL-based figures "
            "either - it will tend to run lower, not higher, than a true apples-to-apples "
            "multiple would."
        )

    chart_rows = snap_df.dropna(subset=["Market Cap", "RPO"])
    if not chart_rows.empty:
        cap_rpo_fig = go.Figure()
        cap_rpo_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Market Cap"],
                name="Market Cap",
                marker_color=ACCENT,
                hovertemplate="%{x}<br>Market Cap: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in chart_rows["Market Cap"]],
            )
        )
        cap_rpo_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["RPO"],
                name="RPO",
                marker_color=RPO_LINE_COLOR,
                hovertemplate="%{x}<br>RPO: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in chart_rows["RPO"]],
            )
        )
        cap_rpo_fig.update_layout(
            title="Market Cap vs. RPO",
            xaxis_title=None,
            yaxis_title="USD",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False),
            barmode="group",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=60, l=10, r=10, b=10),
            height=420,
        )
        st.plotly_chart(cap_rpo_fig, use_container_width=True)

    missing = [name for name in COMPANIES if name not in set(chart_rows["Company"])]
    if missing:
        st.caption(f"No market cap and/or RPO data available for: {', '.join(missing)}.")

    web_sourced_history = {name: RPO_WEB_SOURCED[name] for name in COMPANIES if name in RPO_WEB_SOURCED}
    if web_sourced_history:
        with st.expander("Disclosed (non-XBRL) contracted backlog history - for reference only"):
            st.caption(
                "Full history of disclosed total contract value figures for companies (marked "
                "† above) whose SEC XBRL has no RPO data at all - pulled from press releases, "
                "8-K exhibits, or investor presentations via web search, not a structured SEC "
                "fact. Only the latest point per company feeds the table/chart above. Manually "
                "researched, not kept live - re-verify against the cited source before relying "
                "on these, and see config.py's RPO_WEB_SOURCED for the full caveat (irregular "
                "dates, and some points are cumulative running totals while others are "
                "separate one-off deals - noted per point below)."
            )
            for name, points in web_sourced_history.items():
                st.markdown(f"**{name}**")
                for p in points:
                    st.markdown(f"- {p['date']}: {format_usd(p['value'])} - {p['note']}  \n  _Source: {p['source']}_")


def render_forward_ebitda_snapshot():
    """
    Forward EBITDA (Estimate) - a SNAPSHOT like Market Cap vs. RPO above,
    not a time series: it's built from a live analyst consensus revenue
    estimate, not a filed historical figure.

    IMPORTANT: this is an explicit PROXY, not real consensus EBITDA
    guidance - there is no free source of actual analyst EBITDA estimates.
    See config.py's long note for the full explanation. Formula: next-fiscal
    -year consensus revenue estimate (Yahoo Finance analysts) x trailing
    EBITDA margin (operating income + D&A over the trailing ~4 quarters,
    from SEC where available, Yahoo's trailing figures otherwise). This
    assumes the trailing margin holds into the forward year, which is a
    simplification real analyst EBITDA estimates wouldn't make uncritically.
    """
    st.subheader("Forward EBITDA (Estimate)")
    st.caption(
        "PROXY, not real consensus EBITDA guidance - there's no free source of actual "
        "analyst EBITDA estimates (see config.py). Computed as next-fiscal-year consensus "
        "REVENUE estimate (Yahoo Finance analysts) × trailing EBITDA margin (operating "
        "income + D&A over the trailing ~4 quarters). This assumes the trailing margin "
        "holds into the forward year - treat it as a back-of-envelope figure, not a "
        "substitute for real sell-side EBITDA estimates. The chart below shows it scaled "
        "by market cap (Forward EBITDA ÷ Market Cap) rather than the raw dollar figure, "
        "so companies of very different sizes are comparable on one axis - conceptually the "
        "inverse of a forward EV/EBITDA multiple, using market cap in place of enterprise "
        "value."
    )

    rows = []
    for name in COMPANIES.keys():
        cik = COMPANIES[name]
        ticker = TICKERS.get(name)
        info = load_forward_ebitda(cik, ticker)
        market_cap = load_market_cap(ticker) if ticker else None
        ebitda_to_mcap = compute_forward_multiple(info["forward_ebitda"], market_cap)
        rows.append(
            {
                "Company": name,
                "Market Cap": market_cap,
                "Forward Revenue Est. (FY+1)": info["forward_revenue_avg"],
                "Analysts": info["analysts"],
                "Trailing EBITDA Margin": info["margin"],
                "Forward EBITDA (Est.)": info["forward_ebitda"],
                "Forward EBITDA / Market Cap": ebitda_to_mcap * 100 if ebitda_to_mcap is not None else None,
                "Margin source": {"sec": "SEC", "yfinance": "Yahoo Finance"}.get(info["source"], "-"),
            }
        )

    snap_df = pd.DataFrame(rows)
    if snap_df["Forward EBITDA (Est.)"].isna().all():
        st.info("No forward EBITDA estimate available for any tracked company yet.")
        return

    display_df = snap_df.copy()
    display_df["Market Cap"] = display_df["Market Cap"].apply(format_usd)
    display_df["Forward Revenue Est. (FY+1)"] = display_df["Forward Revenue Est. (FY+1)"].apply(format_usd)
    display_df["Analysts"] = display_df["Analysts"].apply(lambda v: f"{int(v)}" if pd.notna(v) else "-")
    display_df["Trailing EBITDA Margin"] = display_df["Trailing EBITDA Margin"].apply(format_pct)
    display_df["Forward EBITDA (Est.)"] = display_df["Forward EBITDA (Est.)"].apply(format_usd)
    display_df["Forward EBITDA / Market Cap"] = display_df["Forward EBITDA / Market Cap"].apply(format_pct)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    chart_rows = snap_df.dropna(subset=["Forward EBITDA / Market Cap"])
    if not chart_rows.empty:
        fwd_ebitda_fig = go.Figure()
        fwd_ebitda_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Forward EBITDA / Market Cap"],
                marker_color=FORWARD_EBITDA_COLOR,
                hovertemplate="%{x}<br>Forward EBITDA / Market Cap: %{customdata}<extra></extra>",
                customdata=[format_pct(v) for v in chart_rows["Forward EBITDA / Market Cap"]],
            )
        )
        fwd_ebitda_fig.update_layout(
            title="Forward EBITDA / Market Cap by Company",
            xaxis_title=None,
            yaxis_title="Forward EBITDA ÷ Market Cap (%)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, ticksuffix="%", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False),
            margin=dict(t=60, l=10, r=10, b=10),
            height=420,
        )
        st.plotly_chart(fwd_ebitda_fig, use_container_width=True)

    missing = [name for name in COMPANIES if name not in set(chart_rows["Company"])]
    if missing:
        st.caption(f"No forward EBITDA / market cap ratio available for: {', '.join(missing)}.")


def render_forward_sales_multiples_snapshot():
    """
    Forward EV/Sales and Forward P/S - a SNAPSHOT like Market Cap vs. RPO and
    Forward EBITDA above, not a time series. Unlike Forward EBITDA, these
    are standard valuation multiples built from real inputs, not a made-up
    proxy: Forward P/S = market cap / forward revenue estimate; Enterprise
    Value = market cap + total debt - cash; Forward EV/Sales = EV / forward
    revenue estimate. See config.py's note for the full explanation and the
    "as of different dates" caveat shared with Market Cap vs. RPO.
    """
    st.subheader("Forward EV/Sales & P/S (Estimate)")
    st.caption(
        "Forward P/S = market cap ÷ next-fiscal-year consensus revenue estimate (Yahoo "
        "Finance analysts). Enterprise Value (EV) = market cap + total debt - cash & "
        "equivalents. Forward EV/Sales = EV ÷ that same forward revenue estimate. Market "
        "cap is a LIVE snapshot; total debt and cash are each only as fresh as the most "
        "recently filed quarter - same 'as of different dates' caveat as Market Cap vs. RPO "
        "above, so EV mixes a live number with a lagged one by construction."
    )

    rows = []
    for name in COMPANIES.keys():
        cik = COMPANIES[name]
        ticker = TICKERS.get(name)
        info = load_forward_sales_multiples(cik, ticker)
        rows.append(
            {
                "Company": name,
                "Market Cap": info["market_cap"],
                "Total Debt": info["total_debt"],
                "Cash": info["cash"],
                "Enterprise Value": info["ev"],
                "Forward Revenue Est. (FY+1)": info["forward_revenue_avg"],
                "Forward EV/Sales": info["forward_ev_sales"],
                "Forward P/S": info["forward_ps"],
            }
        )

    snap_df = pd.DataFrame(rows)
    if snap_df["Forward EV/Sales"].isna().all() and snap_df["Forward P/S"].isna().all():
        st.info("No forward EV/Sales or P/S data available for any tracked company yet.")
        return

    display_df = snap_df.copy()
    for col in ["Market Cap", "Total Debt", "Cash", "Enterprise Value", "Forward Revenue Est. (FY+1)"]:
        display_df[col] = display_df[col].apply(format_usd)
    display_df["Forward EV/Sales"] = display_df["Forward EV/Sales"].apply(format_multiple)
    display_df["Forward P/S"] = display_df["Forward P/S"].apply(format_multiple)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    chart_rows = snap_df.dropna(subset=["Forward EV/Sales", "Forward P/S"], how="all")
    if not chart_rows.empty:
        mult_fig = go.Figure()
        mult_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Forward EV/Sales"],
                name="Forward EV/Sales",
                marker_color=FORWARD_EV_SALES_COLOR,
                hovertemplate="%{x}<br>Forward EV/Sales: %{customdata}<extra></extra>",
                customdata=[format_multiple(v) for v in chart_rows["Forward EV/Sales"]],
            )
        )
        mult_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Forward P/S"],
                name="Forward P/S",
                marker_color=FORWARD_PS_COLOR,
                hovertemplate="%{x}<br>Forward P/S: %{customdata}<extra></extra>",
                customdata=[format_multiple(v) for v in chart_rows["Forward P/S"]],
            )
        )
        mult_fig.update_layout(
            title="Forward EV/Sales & P/S by Company",
            xaxis_title=None,
            yaxis_title="Multiple (x)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, ticksuffix="x", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False),
            barmode="group",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=60, l=10, r=10, b=10),
            height=420,
        )
        st.plotly_chart(mult_fig, use_container_width=True)

    missing = [name for name in COMPANIES if name not in set(chart_rows["Company"])]
    if missing:
        st.caption(f"No forward EV/Sales or P/S data available for: {', '.join(missing)}.")


def render_comparison_view():
    """
    Side-by-side comparison across every tracked company (config.COMPANIES),
    one chart per metric. Reuses the exact same @st.cache_data loaders as
    the Single Company view below - no separate fetch path - so a number
    shown here always matches what you'd see drilling into that company,
    and switching between the two views doesn't trigger a refetch once
    both are cached.

    EPS and shares outstanding are deliberately left out here - both depend
    on each company's own capital structure (share classes, IPO share
    pricing) rather than being a meaningful like-for-like comparison across
    companies. They're still available in the Single Company view.
    """
    render_valuation_snapshot()
    st.divider()
    render_forward_ebitda_snapshot()
    st.divider()
    render_forward_sales_multiples_snapshot()
    st.divider()

    companies = list(COMPANIES.keys())

    def _load_metric(fetch_one):
        """Run fetch_one(company_name) -> df_or_None for every tracked
        company. A failure fetching one company (network hiccup, etc.)
        shouldn't blank out the whole comparison chart - it's caught and
        that company just shows as having no data, same as "SEC has
        nothing for this metric" would."""
        out = {}
        for name in companies:
            try:
                out[name] = fetch_one(name)
            except Exception:
                out[name] = None
        return out

    st.subheader("Revenue")
    st.caption(
        "Shown as Revenue ÷ Market Cap (%) rather than raw revenue, so companies of very "
        "different sizes are comparable on one axis. Market cap is a LIVE snapshot from "
        "Yahoo Finance (as of whenever this page loaded) - the SAME current figure is used "
        "as the denominator for every historical quarter, not that quarter's own market cap "
        "at the time - so a rising line here mostly reflects revenue growth, not the stock "
        "getting cheaper relative to sales back then. Conceptually the inverse of a trailing "
        "Price/Sales multiple, one quarter at a time."
    )

    def _revenue_over_mcap(name):
        rev_df = load_revenue(COMPANIES[name], TICKERS.get(name))[0]
        if rev_df is None or rev_df.empty:
            return None
        ticker = TICKERS.get(name)
        market_cap = load_market_cap(ticker) if ticker else None
        if not market_cap:
            return None
        out = rev_df[["quarter_label", "period_end", "value"]].copy()
        out["value"] = out["value"] / market_cap * 100
        return out

    render_comparison_chart(
        "Quarterly Revenue ÷ Market Cap",
        "Revenue ÷ Market Cap (%)",
        _load_metric(_revenue_over_mcap),
        format_pct,
        chart_type="bar",
        ticksuffix="%",
    )

    segment_companies = [name for name in COMPANIES if name in SEGMENT_REVENUE_SPLIT]
    if segment_companies:
        st.subheader("Revenue Mix: Share from AI/HPC Hosting")
        st.caption(
            "For the bitcoin-miner-pivoting-to-AI cohort that discloses a clean revenue "
            "split (see the Single Company view for the dollar breakdown and sources): "
            "AI/HPC hosting revenue as a % of that company's total revenue each quarter. "
            "Not shown for Cipher Mining/CleanSpark (still 100% bitcoin mining revenue as "
            "of the last check). Hut 8 has a real 3-segment $ breakdown on its own Single "
            "Company page, but is excluded from this specific chart too, since its "
            "'Compute' segment blends bitcoin mining and AI/GPU cloud without a disclosed "
            "dollar split - there's no clean AI-only number to compute a share from. See "
            "config.py's SEGMENT_REVENUE_SPLIT for the full explanation."
        )

        def _ai_hosting_share(name):
            seg_df, seg_cols = load_segment_revenue_df(name)
            if seg_df is None or seg_df.empty:
                return None
            ai_cols = [c for c in seg_cols if _is_ai_segment_label(c)]
            if not ai_cols:
                return None
            out = seg_df[["quarter_label", "period_end"]].copy()
            out["value"] = seg_df[ai_cols].sum(axis=1) / seg_df["total"] * 100
            return out

        render_comparison_chart(
            "AI/HPC Hosting Revenue ÷ Total Revenue",
            "AI/HPC Hosting Share (%)",
            {name: _ai_hosting_share(name) for name in segment_companies},
            format_pct,
            chart_type="line",
            ticksuffix="%",
        )

    power_companies = [
        name for name in COMPANIES if name in POWER_CAPACITY and not POWER_CAPACITY[name].get("single_disclosed_mw")
    ]
    if power_companies:
        st.subheader("Power Capacity: Current vs. Future Planned (MW)")
        st.caption(
            "Manually researched SNAPSHOT (each company as of its own most recent "
            "disclosure - dates vary, see the Single Company view or config.py's "
            "POWER_CAPACITY for each one) - not SEC XBRL, not a time series. Professional "
            "analysts covering this sector increasingly value these companies on a "
            "$/MW-of-capacity basis rather than pure revenue/EBITDA multiples, since "
            "capacity is the real constraint and leading indicator of future revenue. "
            "Green = live/operational today, blue = signed contract not yet live, gray = "
            "planned/diligence-stage (least certain - closer to ambition than commitment). "
            "Nebius isn't shown here - it only discloses a single blended forward target "
            "(5GW by end of 2026), not a current-vs-planned split; see its Single Company "
            "page."
        )

        power_fig = go.Figure()
        for label, key in [
            ("Current (operational)", "current_mw"),
            ("Contracted (not yet live)", "contracted_future_mw"),
            ("Pipeline (planned/diligence)", "pipeline_mw"),
        ]:
            values = [POWER_CAPACITY[name].get(key) or 0 for name in power_companies]
            power_fig.add_trace(
                go.Bar(
                    x=power_companies,
                    y=values,
                    name=label,
                    marker_color=POWER_CAPACITY_COLORS[label],
                    hovertemplate=f"%{{x}}<br>{label}: %{{customdata}}<extra></extra>",
                    customdata=[format_mw(v) for v in values],
                )
            )
        power_fig.update_layout(
            title="Power Capacity by Stage",
            barmode="stack",
            xaxis_title=None,
            yaxis_title="Power capacity (MW)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID),
            xaxis=dict(showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=460,
        )
        st.plotly_chart(power_fig, use_container_width=True)

        mw_rows = []
        for name in power_companies:
            entry = POWER_CAPACITY[name]
            live_and_contracted = (entry.get("current_mw") or 0) + (entry.get("contracted_future_mw") or 0)
            ticker = TICKERS.get(name)
            market_cap = load_market_cap(ticker) if ticker else None
            mw_rows.append(
                {
                    "Company": name,
                    "Current + Contracted (MW)": live_and_contracted or None,
                    "Market Cap": market_cap,
                    "Market Cap ÷ MW": (market_cap / live_and_contracted)
                    if (market_cap and live_and_contracted)
                    else None,
                    "As of": entry["as_of"],
                }
            )
        mw_df = pd.DataFrame(mw_rows)
        display_mw_df = mw_df.copy()
        display_mw_df["Current + Contracted (MW)"] = display_mw_df["Current + Contracted (MW)"].apply(format_mw)
        display_mw_df["Market Cap"] = display_mw_df["Market Cap"].apply(format_usd)
        display_mw_df["Market Cap ÷ MW"] = display_mw_df["Market Cap ÷ MW"].apply(
            lambda v: f"${v:,.1f}M/MW" if pd.notna(v) else "-"
        )
        st.dataframe(display_mw_df, use_container_width=True, hide_index=True)
        st.caption(
            "Market Cap ÷ MW uses live market cap over CURRENT + CONTRACTED (not "
            "pipeline) MW - the closest read on how the market is pricing each dollar of "
            "committed capacity, conceptually the inverse of the $/MW figures sell-side "
            "analysts assign when setting price targets (e.g. JPMorgan's 2026 framework "
            "cited $8-19M/MW depending on capacity quality). Not a precise apples-to-"
            "apples multiple - market cap is live, MW figures are each company's own "
            "as-of date above, and capacity quality/counterparty credit varies a lot "
            "across these names (see the earlier valuation-methodology discussion)."
        )

    st.subheader("Net Income / Loss")
    render_comparison_chart(
        "Net Income / Loss by Quarter",
        "Net income / loss (USD)",
        _load_metric(lambda name: load_net_income(COMPANIES[name], TICKERS.get(name))[0]),
        format_usd,
        chart_type="bar",
        tickprefix="$",
    )

    st.subheader("Total Assets")

    def _total_assets(name):
        df = load_total_assets(COMPANIES[name])[0]
        return filter_since_data_start(df, name)

    render_comparison_chart(
        "Total Assets Over Time",
        "Total assets (USD)",
        _load_metric(_total_assets),
        format_usd,
        chart_type="line",
        tickprefix="$",
    )

    st.subheader("Total Debt")

    def _total_debt(name):
        stacked, _info = load_debt_segments(COMPANIES[name])
        if stacked is None or stacked.empty:
            return None
        stacked = filter_since_data_start(stacked, name)
        if stacked is None or stacked.empty:
            return None
        return stacked[["quarter_label", "period_end", "total"]].rename(columns={"total": "value"})

    render_comparison_chart(
        "Total Debt Over Time",
        "Total debt (USD)",
        _load_metric(_total_debt),
        format_usd,
        chart_type="line",
        tickprefix="$",
    )
    st.caption("Total across all tracked debt categories - see the Single Company view for the category breakdown.")

    st.subheader("Remaining Performance Obligations (Backlog)")

    rpo_fallback_companies = []

    def _rpo(name):
        df, _tag, fallback_tag = load_rpo(COMPANIES[name], name)
        if fallback_tag:
            rpo_fallback_companies.append(name)
        return filter_since_data_start(df, name)

    render_comparison_chart(
        "RPO Over Time",
        "RPO (USD)",
        _load_metric(_rpo),
        format_usd,
        chart_type="line",
        tickprefix="$",
    )
    if rpo_fallback_companies:
        st.caption(
            f"⚠️ {', '.join(rpo_fallback_companies)}: SEC has no current RPO tag for "
            "this company (see the Single Company view), so its more recent points here are a "
            "narrower fallback metric (contract liability / deferred revenue), not true RPO - "
            "don't read a level change at the switchover as a real change in backlog."
        )

    _cmp_disclosed = [name for name in COMPANIES if name in RPO_WEB_SOURCED]
    if _cmp_disclosed:
        st.caption(
            f"📰 {', '.join(_cmp_disclosed)}: SEC XBRL has no RPO data at all, so they're "
            "excluded from the chart above (which is XBRL-only, so the quarterly cadence "
            "stays comparable across companies) - each has its own web-sourced total "
            "contract value chart in the Single Company view instead. See the Valuation "
            "Snapshot's expander for the latest figures and sources."
        )

    st.subheader("Free Cash Flow")

    def _fcf(name):
        fcf_df, _tags, _source = load_fcf(COMPANIES[name], TICKERS.get(name))
        if fcf_df is None or fcf_df.empty:
            return None
        return fcf_df[["quarter_label", "fcf"]].rename(columns={"fcf": "value"})

    render_comparison_chart(
        "Free Cash Flow by Quarter",
        "FCF (USD)",
        _load_metric(_fcf),
        format_usd,
        chart_type="bar",
        tickprefix="$",
    )

    st.subheader("Margins")

    def _net_margin(name):
        df = load_net_margin(COMPANIES[name], TICKERS.get(name))
        if df is None or df.empty:
            return None
        return df[["quarter_label", "net_margin"]].rename(columns={"net_margin": "value"})

    render_comparison_chart(
        "Net Margin by Quarter",
        "Net margin (%)",
        _load_metric(_net_margin),
        format_pct,
        chart_type="line",
        ticksuffix="%",
    )

    def _fcf_margin(name):
        df = load_fcf_margin(COMPANIES[name], TICKERS.get(name))
        if df is None or df.empty:
            return None
        return df[["quarter_label", "fcf_margin"]].rename(columns={"fcf_margin": "value"})

    render_comparison_chart(
        "FCF Margin by Quarter",
        "FCF margin (%)",
        _load_metric(_fcf_margin),
        format_pct,
        chart_type="line",
        ticksuffix="%",
    )
    st.caption(
        "Net margin = net income ÷ revenue; FCF margin = free cash flow ÷ revenue. Each "
        "quarter shown here requires both figures to be on file for that quarter (an inner "
        "join), so this can show fewer quarters than the standalone Revenue/Net Income/FCF "
        "charts above."
    )

    st.caption(
        "EPS and shares outstanding aren't shown in this comparison - EPS and share "
        "count depend on each company's own capital structure (IPO share pricing, share "
        "classes) rather than being a meaningful apples-to-apples measure across "
        "companies. Both are still available per-company in the Single Company view."
    )


def _hosted_mining_annualized_revenue(company: str):
    """
    Looks up `company`'s most recent quarter of hosted-bitcoin-mining
    revenue from config.SEGMENT_REVENUE_SPLIT (the segment label containing
    "Hosted Mining" - currently only Core Scientific's "Bitcoin Hosted
    Mining" line matches) and annualizes it (x4) as a simple run-rate.

    Returns None if this company has no such segment line at all, so the
    calculator can tell "no hosted-mining revenue disclosed" apart from "$0
    of hosted-mining revenue this quarter" - same "None means unknown, not
    zero" convention used everywhere else in this app.

    Why annualize a single quarter rather than trailing-4-quarters: hosted-
    mining revenue at Core Scientific has been on a mild downtrend as
    capacity converts to AI hosting (see SEGMENT_REVENUE_SPLIT), so a
    trailing-4-quarter sum would overweight older, larger quarters relative
    to the run-rate this MW figure reflects today. A single latest-quarter
    run-rate is more consistent with the point-in-time MW snapshot it's
    being divided by, at the cost of more quarter-to-quarter noise.
    """
    seg_df, seg_cols = load_segment_revenue_df(company)
    if seg_df is None or seg_df.empty:
        return None
    hosted_cols = [c for c in seg_cols if "Hosted Mining" in c]
    if not hosted_cols:
        return None
    latest = seg_df.iloc[-1]
    return sum(latest[c] for c in hosted_cols) * 4


def render_fair_value_calculator():
    """
    Fair Value Calculator page - lets the user set their own $/MW
    assumptions (defaulted from JPMorgan's 2026 sector re-basing, per
    config.JPMORGAN_MW_RATE_RANGES) and computes an implied fair value per
    company as (AI/HPC MW x AI rate) + (owned bitcoin-mining MW x mining
    rate) + (hosted-mining revenue-multiple value), compared against live
    market cap.

    Deliberately a TWO-bucket $/MW simplification of JPM's actual
    THREE-tier framework (see JPMORGAN_MW_RATE_RANGES's docstring) PLUS a
    third bucket priced a completely different way - see below - and
    deliberately NOT a recommendation - see the extensive caveats rendered
    below the table before reading too much into any single number here.

    On the third bucket (hosted bitcoin mining, e.g. Core Scientific's
    ~400MW of third-party ASIC hosting): this is NOT priced with a $/MW
    rate like the other two buckets. Direct research (Sep 2026) confirmed
    no analyst report or comparable transaction publishes a $/MW benchmark
    for bitcoin-mining hosting/colocation specifically - JPMorgan's
    framework has a tier for AI/HPC hosting and a tier for OWNED bitcoin
    mining, but nothing for "hosting bitcoin miners you don't own." Rather
    than invent a $/MW figure with no basis, this bucket is priced off each
    company's own disclosed hosted-mining REVENUE (see
    _hosted_mining_annualized_revenue) times a user-adjustable revenue
    multiple - a completely different (and much more directly evidenced)
    method than the capacity-based $/MW approach used for the other two
    buckets, appropriate for what's economically a small, fee-based
    service business rather than a capacity-ownership bet.
    """
    st.subheader("Fair Value Calculator: $/MW Valuation Framework")
    st.caption(
        "Modeled on how professional analysts increasingly value this sector, e.g. "
        "JPMorgan's 2026 re-based framework, which prices contracted or deployed "
        "critical-IT (AI/HPC) capacity at roughly $8 to $17M per MW depending on "
        "quality, cloud-conversion capacity up to about $19M per MW, and pure owned "
        "bitcoin-mining capacity at just $1 to $2M per MW. This calculator simplifies "
        "JPMorgan's first two tiers into one AI/HPC band ($8 to $19M per MW), since "
        "the underlying MW data here doesn't cleanly distinguish an already-"
        "electrified mining site being converted from purpose-built AI capacity the "
        "way JPMorgan's own per-site analysis presumably does. Adjust the two rate "
        "sliders below to your own view; the defaults are just the midpoint of "
        "JPMorgan's reported ranges, not this app's recommendation. A third bucket, "
        "hosted bitcoin mining (a company hosting third-party-owned ASIC miners for "
        "a fee, e.g. about 400MW of Core Scientific's portfolio), is priced "
        "separately below, since it's a fee-based service business rather than a "
        "capacity-ownership bet, and no $/MW benchmark exists for it anywhere."
    )

    ai_range = JPMORGAN_MW_RATE_RANGES["ai_hosting"]
    btc_range = JPMORGAN_MW_RATE_RANGES["btc_mining"]
    r_col1, r_col2 = st.columns(2)
    ai_rate = r_col1.slider(
        "AI/HPC capacity ($M per MW)",
        min_value=ai_range["low"],
        max_value=ai_range["high"],
        value=ai_range["default"],
        step=0.5,
        key="fvc_ai_rate",
    )
    btc_rate = r_col2.slider(
        "Owned bitcoin mining capacity ($M per MW)",
        min_value=btc_range["low"],
        max_value=btc_range["high"],
        value=btc_range["default"],
        step=0.1,
        key="fvc_btc_rate",
    )

    st.markdown("**Hosted bitcoin mining (revenue-multiple method, not $/MW)**")
    st.caption(
        "No analyst report or comparable transaction publishes a $/MW rate for "
        "bitcoin-ASIC-hosting-as-a-service (confirmed by direct research, Sep 2026) - "
        "it's a real but unbenchmarked category, so instead of a $/MW slider, this "
        "prices it off the company's own disclosed hosted-mining revenue (annualized "
        "from its most recent quarter - see config.SEGMENT_REVENUE_SPLIT) times a "
        "revenue multiple YOU choose. There's no market comp behind the default below - "
        "it's a placeholder, not a benchmark. Treat any number this produces as much "
        "softer than the two $/MW buckets above."
    )
    hosted_multiple = st.slider(
        "Hosted-mining revenue multiple (x annualized revenue)",
        min_value=1.0,
        max_value=10.0,
        value=4.0,
        step=0.5,
        key="fvc_hosted_multiple",
    )

    rows = []
    missing_mining_data = []
    missing_hosted_data = []
    for name in COMPANIES:
        purpose = MW_BY_PURPOSE.get(name)
        if not purpose:
            continue
        ai_mw = purpose.get("ai_hosting_mw") or 0
        owned_btc_mw = purpose.get("owned_btc_mining_mw")
        if owned_btc_mw is None:
            missing_mining_data.append(name)
            owned_btc_mw = 0
        hosted_btc_mw = purpose.get("hosted_btc_mining_mw") or 0

        ai_value = ai_mw * ai_rate * 1_000_000
        owned_btc_value = owned_btc_mw * btc_rate * 1_000_000

        hosted_value = 0.0
        hosted_revenue = None
        if hosted_btc_mw:
            hosted_revenue = _hosted_mining_annualized_revenue(name)
            if hosted_revenue is None:
                missing_hosted_data.append(name)
            else:
                hosted_value = hosted_revenue * hosted_multiple

        fair_value = ai_value + owned_btc_value + hosted_value
        ticker = TICKERS.get(name)
        market_cap = load_market_cap(ticker) if ticker else None
        upside = ((fair_value / market_cap) - 1) * 100 if market_cap else None
        rows.append(
            {
                "Company": name,
                "AI/HPC MW": ai_mw,
                "Owned BTC Mining MW": purpose.get("owned_btc_mining_mw"),
                "Hosted BTC Mining MW": hosted_btc_mw if hosted_btc_mw else None,
                "Implied AI Value": ai_value,
                "Implied Owned Mining Value": owned_btc_value,
                "Implied Hosted Mining Value": hosted_value,
                "Implied Fair Value": fair_value,
                "Market Cap": market_cap,
                "Implied Upside/Downside": upside,
            }
        )

    calc_df = pd.DataFrame(rows)
    display_df = calc_df.copy()
    display_df["AI/HPC MW"] = display_df["AI/HPC MW"].apply(format_mw)
    display_df["Owned BTC Mining MW"] = display_df["Owned BTC Mining MW"].apply(
        lambda v: format_mw(v) if pd.notna(v) else "not disclosed"
    )
    display_df["Hosted BTC Mining MW"] = display_df["Hosted BTC Mining MW"].apply(
        lambda v: format_mw(v) if pd.notna(v) else "-"
    )
    display_df["Implied AI Value"] = display_df["Implied AI Value"].apply(format_usd)
    display_df["Implied Owned Mining Value"] = display_df["Implied Owned Mining Value"].apply(format_usd)
    display_df["Implied Hosted Mining Value"] = display_df["Implied Hosted Mining Value"].apply(format_usd)
    display_df["Implied Fair Value"] = display_df["Implied Fair Value"].apply(format_usd)
    display_df["Market Cap"] = display_df["Market Cap"].apply(format_usd)
    display_df["Implied Upside/Downside"] = display_df["Implied Upside/Downside"].apply(
        lambda v: f"{v:+,.0f}%" if pd.notna(v) else "-"
    )
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    if missing_mining_data:
        st.caption(
            f"⚠️ {', '.join(missing_mining_data)}: no standalone OWNED bitcoin-mining MW "
            "figure is disclosed anywhere for this company (confirmed by direct "
            "research, not just missing here) - its owned-mining contribution is "
            "treated as $0 above, which UNDERSTATES its implied fair value by whatever "
            "residual owned mining capacity it still has. See config.py's MW_BY_PURPOSE "
            "for what IS captured for each one."
        )
    if missing_hosted_data:
        st.caption(
            f"⚠️ {', '.join(missing_hosted_data)}: has disclosed hosted-mining MW but no "
            "matching hosted-mining revenue line in config.SEGMENT_REVENUE_SPLIT yet, so "
            "its hosted-mining contribution is treated as $0 above - an inconsistency to "
            "fix in config.py rather than a real data gap."
        )

    chart_rows = calc_df.dropna(subset=["Market Cap"])
    if not chart_rows.empty:
        fv_fig = go.Figure()
        fv_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Implied Fair Value"],
                name="Implied Fair Value",
                marker_color=ACCENT,
                hovertemplate="%{x}<br>Implied Fair Value: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in chart_rows["Implied Fair Value"]],
            )
        )
        fv_fig.add_trace(
            go.Bar(
                x=chart_rows["Company"],
                y=chart_rows["Market Cap"],
                name="Market Cap",
                marker_color="#C9C9C9",
                hovertemplate="%{x}<br>Market Cap: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in chart_rows["Market Cap"]],
            )
        )
        fv_fig.update_layout(
            title="Implied Fair Value vs. Market Cap",
            xaxis_title=None,
            yaxis_title="USD",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$"),
            xaxis=dict(showgrid=False),
            barmode="group",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=60, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(fv_fig, use_container_width=True)

    with st.expander("Read before using this for anything real"):
        st.markdown(
            "- **Not investment advice, and not JPMorgan's own model** - the AI/HPC and "
            "owned-mining buckets are a simplified reconstruction built from press "
            "coverage of JPMorgan's framework, using MW figures this app manually "
            "compiled from public disclosures. It is not their proprietary per-site "
            "analysis, and the hosted-mining bucket isn't from JPMorgan or anyone else's "
            "framework at all - it's this app's own revenue-multiple construction, "
            "because no external $/MW benchmark for that category exists (see above).\n"
            "- **MW figures are a dated snapshot** (see the Power Capacity section - "
            "each company as of its own most recent disclosure, dates vary) using only "
            "current + contracted-future MW, deliberately excluding pipeline/diligence-"
            "stage capacity as too speculative to price.\n"
            "- **Several companies' OWNED mining MW is unknown, not zero** - flagged "
            "above; their implied fair value is a floor, not a full picture.\n"
            "- **The hosted-mining revenue multiple is a placeholder, not a benchmark** "
            "- unlike the two $/MW sliders (anchored to JPMorgan's published ranges), "
            "there is no market comp behind the hosted-mining multiple slider's default "
            "or range at all. It exists so you can see the shape of the calculation and "
            "substitute your own view, not because 4x (or 1-10x) is defensible from any "
            "outside source.\n"
            "- **Uses Market Cap, not Enterprise Value** - ignores each company's net "
            "debt/cash entirely, unlike a true EV-based $/MW comparison.\n"
            "- **A single blended rate per bucket, applied to every company** - JPM's "
            "own framework applies COMPANY-SPECIFIC rates within each range based on "
            "capacity quality and counterparty credit (e.g. an investment-grade "
            "hyperscaler lease vs. an unrated private AI lab) - this calculator can't "
            "replicate that judgment, it only lets you pick one rate per bucket for "
            "everyone.\n"
            "- **Nebius's AI/HPC MW is a company-stated TARGET**, not a current+"
            "contracted breakdown like the other companies - softer than the rest.\n"
            "- Cross-reference with the Compare Companies page's Valuation Snapshot, "
            "RPO, and Forward EBITDA sections before drawing any conclusion - this is "
            "one lens among several, not a replacement for them."
        )


def render_company_summary(company: str, ticker: str | None):
    """
    Header at the top of each company's Single Company page: a short
    description, current market cap, and an adjustable-timeframe stock
    price chart. Market cap and price are both LIVE/daily-updating figures
    from Yahoo Finance - SEC has no equivalent of either (its "market cap"
    isn't a thing it reports, and it has no price data at all). See
    config.COMPANY_DESCRIPTIONS for the static description text and its
    caveats (hand-written, periodically-reviewed summaries, not pulled from
    any filing or API).
    """
    st.markdown(COMPANY_DESCRIPTIONS.get(company, "_No description available yet._"))

    market_cap = load_market_cap(ticker) if ticker else None
    st.metric("Market Cap", format_usd(market_cap) if market_cap is not None else "-")

    if not ticker:
        st.info(f"No ticker configured for {company} - can't show a price chart.")
        return

    period_label = st.radio(
        "Price chart timeframe",
        options=list(PRICE_HISTORY_PERIODS.keys()),
        index=list(PRICE_HISTORY_PERIODS.keys()).index("1Y"),
        horizontal=True,
        key=f"price_period_{company}",
    )

    with st.spinner(f"Fetching {ticker} price history from Yahoo Finance..."):
        try:
            price_df = load_price_history(ticker, PRICE_HISTORY_PERIODS[period_label])
        except Exception as exc:
            st.error(f"Couldn't fetch price history: {exc}")
            price_df = None

    if price_df is None or price_df.empty:
        st.info(f"No price history available for {ticker} ({period_label}) from Yahoo Finance.")
        return

    price_fig = go.Figure()
    price_fig.add_trace(
        go.Scatter(
            x=price_df["date"],
            y=price_df["close"],
            mode="lines",
            line=dict(color=PRICE_LINE_COLOR, width=2),
            name=f"{ticker} close",
            hovertemplate="%{x|%Y-%m-%d}<br>Close: $%{y:,.2f}<extra></extra>",
        )
    )
    price_fig.update_layout(
        title=f"{ticker} - {period_label} Price",
        xaxis_title=None,
        yaxis_title="Price (USD)",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(gridcolor=GRID, tickprefix="$", zeroline=False),
        xaxis=dict(showgrid=False),
        margin=dict(t=60, l=10, r=10, b=10),
        height=360,
    )
    st.plotly_chart(price_fig, use_container_width=True)

    latest = price_df.iloc[-1]
    first = price_df.iloc[0]
    change_pct = (latest["close"] / first["close"] - 1) * 100 if first["close"] else None
    caption_bits = [
        f"Latest close: ${latest['close']:,.2f} (as of {latest['date'].date()})",
    ]
    if change_pct is not None:
        caption_bits.append(f"{period_label} change: {change_pct:+,.1f}%")
    caption_bits.append("Source: Yahoo Finance (live/daily - not an SEC-filed figure).")
    st.caption("  ·  ".join(caption_bits))


st.title("NeoCloud Research")

view = st.sidebar.radio(
    "View", ["Compare Companies", "Single Company", "Fair Value Calculator"], index=0
)
st.sidebar.markdown("---")
st.sidebar.caption(
    "Private neoclouds (Crusoe, Together AI, etc.) don't file with the SEC, "
    "so they won't have data here until they IPO or disclose financials "
    "another way - see config.py to add newly public tickers."
)

if view == "Compare Companies":
    st.caption("Side-by-side comparison across every tracked neocloud, metric by metric.")
    render_comparison_view()
elif view == "Fair Value Calculator":
    st.caption("A $/MW-based valuation model, adjustable to your own assumptions.")
    render_fair_value_calculator()
else:
    company = st.sidebar.selectbox("Company", options=list(COMPANIES.keys()))
    cik = COMPANIES[company]
    st.sidebar.caption(f"SEC CIK: {cik}")

    ticker = TICKERS.get(company)

    st.header(company)
    render_company_summary(company, ticker)
    st.divider()

    st.caption("Quarterly revenue pulled live from SEC EDGAR XBRL filings.")

    with st.spinner(f"Fetching {company} filings from SEC EDGAR..."):
        try:
            df, tag_used, revenue_source = load_revenue(cik, ticker)
        except Exception as exc:  # network/SEC hiccups - fail visibly, not silently
            st.error(f"Couldn't fetch data from SEC EDGAR: {exc}")
            st.stop()

    if df is None:
        st.warning(
            f"No quarterly revenue data found for {company} (CIK {cik}) from SEC or Yahoo Finance. "
            "It may not be SEC-reporting, or none of the known revenue tags/fields matched."
        )
        st.stop()

    if revenue_source == "yfinance":
        st.caption(
            f"SEC EDGAR has no quarterly revenue data for {company} (likely a foreign private "
            "issuer filing 20-F/6-K instead of 10-K/10-Q) - showing Yahoo Finance's quarterly "
            f"income statement instead  ·  {len(df)} quarters found (Yahoo typically only "
            "covers the trailing several, unlike SEC's fuller history)."
        )
    else:
        st.caption(f"XBRL tag used: `{tag_used}`  ·  {len(df)} quarters found")

    latest = df.iloc[-1]
    col1, col2, col3 = st.columns(3)
    col1.metric(f"Latest quarter ({latest['quarter_label']})", format_usd(latest["value"]))
    prior_year = df[df["quarter_label"] == latest["quarter_label"]]
    yoy = latest.get("yoy_growth")
    col2.metric("YoY growth", f"{yoy:,.0f}%" if pd.notna(yoy) else "-")
    col3.metric("Quarters on file", len(df))

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=df["quarter_label"],
            y=df["value"],
            marker_color=ACCENT,
            hovertemplate="%{x}<br>Revenue: %{customdata}<extra></extra>",
            customdata=[format_usd(v) for v in df["value"]],
        )
    )
    fig.update_layout(
        title=f"{company} - Quarterly Revenue",
        xaxis_title=None,
        yaxis_title="Revenue (USD)",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        yaxis=dict(gridcolor=GRID, tickprefix="$"),
        xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
        margin=dict(t=60, l=10, r=10, b=10),
        height=440,
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("Underlying data"):
        show_cols = ["quarter_label", "period_start", "period_end", "value", "yoy_growth", "form", "filed"]
        st.dataframe(
            df[show_cols].rename(
                columns={
                    "quarter_label": "Quarter",
                    "period_start": "Start",
                    "period_end": "End",
                    "value": "Revenue (USD)",
                    "yoy_growth": "YoY %",
                    "form": "Filing",
                    "filed": "Filed date",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    seg_df, seg_cols = load_segment_revenue_df(company)
    if seg_df is not None and not seg_df.empty:
        st.divider()
        ai_cols = [c for c in seg_cols if _is_ai_segment_label(c)]
        has_clean_split = bool(ai_cols)
        header = (
            f"{company} - Revenue Mix: Bitcoin Mining vs. AI/HPC Hosting"
            if has_clean_split
            else f"{company} - Revenue by Segment"
        )
        st.subheader(header)
        caption = (
            "Manually compiled from this company's own filed income-statement revenue "
            "line items (earnings-release exhibits / 10-Q face financials), NOT a live "
            "SEC XBRL pull - revenue broken out by line of business is dimensional XBRL "
            "data (a product/segment-axis member), which SEC's standard companyconcept "
            "API never returns regardless of tag name. These ARE real filed GAAP figures "
            "though, not press-release deal totals - see config.py's SEGMENT_REVENUE_SPLIT "
            "for the exact source of each quarter and which companies were deliberately "
            "left out (no clean split exists for them yet)."
        )
        if not has_clean_split:
            caption += (
                f" {company} does NOT disclose a clean bitcoin-mining-vs-AI dollar split "
                "- the segments below are its own real reported business lines, but at "
                "least one blends bitcoin mining and AI/HPC revenue together without "
                "breaking out the dollar amounts (see config.py for exactly which line "
                "and why)."
            )
        st.caption(caption)

        latest_seg = seg_df.iloc[-1]
        ai_total = sum(latest_seg[c] for c in ai_cols) if ai_cols else 0
        ai_share = (ai_total / latest_seg["total"] * 100) if latest_seg["total"] else float("nan")
        s_col1, s_col2 = st.columns(2)
        s_col1.metric(f"Total revenue ({latest_seg['quarter_label']})", format_usd(latest_seg["total"]))
        if has_clean_split:
            s_col2.metric("Share from AI/HPC hosting", f"{ai_share:,.0f}%" if pd.notna(ai_share) else "-")
        else:
            s_col2.metric("Share from AI/HPC hosting", "Not disclosed")

        seg_fig = go.Figure()
        for label in seg_cols:
            seg_fig.add_trace(
                go.Bar(
                    x=seg_df["quarter_label"],
                    y=seg_df[label],
                    name=label,
                    marker_color=_segment_revenue_color(label),
                    hovertemplate=f"%{{x}}<br>{label}: %{{customdata}}<extra></extra>",
                    customdata=[format_usd(v) for v in seg_df[label]],
                )
            )
        seg_fig.update_layout(
            title=f"{company} - Revenue by Line of Business",
            barmode="stack",
            xaxis_title=None,
            yaxis_title="Revenue (USD)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(seg_fig, use_container_width=True)

        with st.expander("Underlying data"):
            seg_show_cols = ["quarter_label", "period_end", *seg_cols, "total", "source"]
            st.dataframe(
                seg_df[seg_show_cols].rename(columns={"quarter_label": "Quarter", "period_end": "As of"}),
                use_container_width=True,
                hide_index=True,
            )

    power = get_power_capacity(company)
    if power:
        st.divider()
        st.subheader(f"{company} - Power Capacity (Current vs. Future Planned)")
        st.caption(
            "Manually researched from investor presentations/earnings releases, NOT SEC "
            "XBRL - power capacity in MW isn't a GAAP or financial-statement concept at "
            "all, only ever disclosed in prose/slides. This is a SNAPSHOT as of the date "
            f"below (**as of {power['as_of']}**), not a live figure or a time series - "
            "see config.py's POWER_CAPACITY for the full caveat, including where figures "
            "from different company disclosures don't perfectly reconcile with each other."
        )

        single_mw = power.get("single_disclosed_mw")
        if single_mw is not None:
            st.metric(f"Disclosed contracted power target (as of {power['as_of']})", format_mw(single_mw))
            st.caption(power["note"])
        else:
            stages = [
                ("Current (operational)", power.get("current_mw")),
                ("Contracted (not yet live)", power.get("contracted_future_mw")),
                ("Pipeline (planned/diligence)", power.get("pipeline_mw")),
            ]
            known_stages = [(label, mw) for label, mw in stages if mw is not None]
            if not known_stages:
                st.info(f"No power capacity breakdown available for {company} yet.")
            else:
                p_cols = st.columns(len(known_stages))
                for col, (label, mw) in zip(p_cols, known_stages):
                    col.metric(label, format_mw(mw))

                power_fig = go.Figure()
                power_fig.add_trace(
                    go.Bar(
                        x=[label for label, _ in known_stages],
                        y=[mw for _, mw in known_stages],
                        marker_color=[POWER_CAPACITY_COLORS[label] for label, _ in known_stages],
                        hovertemplate="%{x}: %{customdata}<extra></extra>",
                        customdata=[format_mw(mw) for _, mw in known_stages],
                    )
                )
                power_fig.update_layout(
                    title=f"{company} - Power Capacity by Stage",
                    xaxis_title=None,
                    yaxis_title="Power capacity (MW)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(gridcolor=GRID),
                    xaxis=dict(showgrid=False),
                    margin=dict(t=60, l=10, r=10, b=10),
                    height=400,
                )
                st.plotly_chart(power_fig, use_container_width=True)
            st.caption(power["note"])
        st.caption(f"Source: {power['source']}")

    st.divider()
    st.subheader(f"{company} - Total Assets")

    with st.spinner(f"Fetching {company} balance sheet data from SEC EDGAR..."):
        try:
            assets_df, assets_tag = load_total_assets(cik)
            assets_df = filter_since_data_start(assets_df, company)
        except Exception as exc:
            st.error(f"Couldn't fetch total assets from SEC EDGAR: {exc}")
            assets_df = None

    if assets_df is None or assets_df.empty:
        st.info(
            f"No total assets data found for {company} (CIK {cik}). "
            "It may not be SEC-reporting, or the 'Assets' tag wasn't used in its filings."
        )
    else:
        st.caption(f"XBRL tag used: `{assets_tag}`  ·  {len(assets_df)} balance-sheet dates found")
        if company in DATA_START_DATE:
            st.caption(
                f"Balance-sheet dates before {DATA_START_DATE[company]} are excluded - see "
                "config.py's note on why (a divested legacy business reported under the "
                "same filer)."
            )

        latest_assets = assets_df.iloc[-1]
        a_col1, a_col2 = st.columns(2)
        a_col1.metric(f"Total assets (as of {latest_assets['period_end'].date()})", format_usd(latest_assets["value"]))
        if len(assets_df) > 1:
            prior_assets = assets_df.iloc[-2]
            change = (latest_assets["value"] / prior_assets["value"] - 1) * 100
            a_col2.metric("Change vs. prior filing", f"{change:,.0f}%")

        assets_fig = go.Figure()
        assets_fig.add_trace(
            go.Scatter(
                x=assets_df["period_end"],
                y=assets_df["value"],
                mode="lines+markers",
                line=dict(color=ACCENT_SECONDARY, width=2),
                marker=dict(size=6),
                hovertemplate="%{x|%b %d, %Y}<br>Total assets: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in assets_df["value"]],
            )
        )
        assets_fig.update_layout(
            title=f"{company} - Total Assets Over Time",
            xaxis_title=None,
            yaxis_title="Total assets (USD)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", rangemode="tozero"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            margin=dict(t=60, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(assets_fig, use_container_width=True)

        with st.expander("Underlying data"):
            a_show_cols = ["quarter_label", "period_end", "value", "form", "filed"]
            st.dataframe(
                assets_df[a_show_cols].rename(
                    columns={
                        "quarter_label": "Quarter",
                        "period_end": "As of",
                        "value": "Total assets (USD)",
                        "form": "Filing",
                        "filed": "Filed date",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader(f"{company} - Remaining Performance Obligations (Backlog)")

    with st.spinner(f"Fetching {company} backlog data from SEC EDGAR..."):
        try:
            rpo_df, rpo_tag, rpo_fallback_tag, rpo_web_sourced = load_rpo_with_web_fallback(cik, company)
            rpo_df = filter_since_data_start(rpo_df, company)
        except Exception as exc:
            st.error(f"Couldn't fetch backlog data from SEC EDGAR: {exc}")
            rpo_df, rpo_fallback_tag, rpo_web_sourced = None, None, False

    if rpo_df is None or rpo_df.empty:
        st.info(
            f"No remaining performance obligation data found for {company} (CIK {cik}), "
            "and no web-sourced disclosure is on file either. It may not be SEC-reporting, "
            "or doesn't disclose RPO/backlog as a standalone figure anywhere."
        )
    else:
        if rpo_web_sourced:
            st.warning(
                f"⚠️ SEC XBRL has no usable RPO/backlog data at all for {company} - the "
                "series below is instead compiled from web search (press releases, 8-K "
                "exhibits, investor presentations), NOT a structured SEC filing. It's "
                f"irregularly dated (only as often as {company} announces a new deal, not "
                "every quarter), may mix cumulative running totals with separate one-off "
                "deals, and isn't kept live - see the note/source on each point below and "
                "config.py's RPO_WEB_SOURCED caveat before relying on it."
            )
        else:
            st.caption(
                f"XBRL tag used: `{rpo_tag}`  ·  {len(rpo_df)} balance-sheet dates found  ·  "
                "contracted, not-yet-recognized future revenue - a forward-looking demand/"
                "capacity signal, not historical revenue already booked."
            )
            if rpo_fallback_tag:
                st.caption(
                    f"⚠️ {company} stopped reporting `{rpo_tag}` at some point and hasn't "
                    f"resumed - dates after that gap are filled in from `{rpo_fallback_tag}` "
                    "(contract liability / deferred revenue) instead. This is a NARROWER, related "
                    "figure (billed-but-undelivered only, not total remaining contracted revenue), "
                    "not a true continuation of the series above - treat any jump at the switchover "
                    "as a definition change, not necessarily a real change in backlog. See config.py "
                    "for the full explanation."
                )

        latest_rpo = rpo_df.iloc[-1]
        r_col1, r_col2 = st.columns(2)
        label = "Total contract value" if rpo_web_sourced else "RPO"
        r_col1.metric(f"{label} (as of {latest_rpo['period_end'].date()})", format_usd(latest_rpo["value"]))
        if len(rpo_df) > 1:
            prior_rpo = rpo_df.iloc[-2]
            change = (latest_rpo["value"] / prior_rpo["value"] - 1) * 100
            r_col2.metric("Change vs. prior disclosure", f"{change:,.0f}%")

        rpo_fig = go.Figure()
        rpo_fig.add_trace(
            go.Scatter(
                x=rpo_df["period_end"],
                y=rpo_df["value"],
                mode="lines+markers",
                line=dict(
                    color=RPO_WEB_SOURCED_COLOR if rpo_web_sourced else RPO_LINE_COLOR,
                    width=2,
                    dash="dash" if rpo_web_sourced else "solid",
                ),
                marker=dict(size=6),
                hovertemplate="%{x|%b %d, %Y}<br>"
                + ("Total contract value" if rpo_web_sourced else "RPO")
                + ": %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in rpo_df["value"]],
            )
        )
        rpo_fig.update_layout(
            title=f"{company} - "
            + ("Disclosed Total Contract Value Over Time (web-sourced)" if rpo_web_sourced else "Remaining Performance Obligations Over Time"),
            xaxis_title=None,
            yaxis_title="USD",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", rangemode="tozero"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            margin=dict(t=60, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(rpo_fig, use_container_width=True)
        if rpo_web_sourced:
            st.caption(
                f"Each point is {company}'s running/disclosed total contract value as of a "
                "specific deal announcement, not a standardized quarterly backlog figure - "
                "see 'Underlying data' below for what each point covers and its source."
            )
        else:
            st.caption(
                f"This is {company}'s backlog of signed, contracted revenue not yet delivered - "
                "not to be confused with its own purchase/capacity commitments (GPU purchases, "
                "data center buildout) owed BY the company, which aren't tagged as a single "
                "figure in its filings and aren't pulled here yet."
            )

        with st.expander("Underlying data"):
            if rpo_web_sourced:
                st.dataframe(
                    rpo_df[["quarter_label", "period_end", "value", "note", "source"]].rename(
                        columns={
                            "quarter_label": "Quarter",
                            "period_end": "Disclosure date",
                            "value": "Total contract value (USD)",
                            "note": "What it covers",
                            "source": "Source",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                r_show_cols = ["quarter_label", "period_end", "value", "form", "filed"]
                st.dataframe(
                    rpo_df[r_show_cols].rename(
                        columns={
                            "quarter_label": "Quarter",
                            "period_end": "As of",
                            "value": "Remaining performance obligations (USD)",
                            "form": "Filing",
                            "filed": "Filed date",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    st.divider()
    st.subheader(f"{company} - Total Debt")

    with st.spinner(f"Fetching {company} debt breakdown from SEC EDGAR..."):
        try:
            debt_df, debt_info = load_debt_segments(cik)
            debt_df = filter_since_data_start(debt_df, company)
        except Exception as exc:
            st.error(f"Couldn't fetch debt data from SEC EDGAR: {exc}")
            debt_df, debt_info = None, {}

    if debt_df is None or debt_df.empty:
        st.info(
            f"No debt data found for {company} (CIK {cik}) across the tracked categories "
            f"({', '.join(DEBT_SEGMENTS.keys())}). It may not be SEC-reporting, or reports "
            "debt under different XBRL tags not yet in config.DEBT_SEGMENTS."
        )
    else:
        st.caption(f"{len(debt_df)} balance-sheet dates found")
        for label, meta in debt_info.items():
            bits = []
            if meta["primary_tag"]:
                bits.append(f"primary: `{meta['primary_tag']}`")
            if meta["component_tags_used"]:
                bits.append(f"fallback components: {', '.join(f'`{t}`' for t in meta['component_tags_used'])}")
            if meta["used_components_for"]:
                bits.append(f"components used for: {', '.join(meta['used_components_for'])}")
            if not bits:
                bits.append("no data reported for this category (treated as $0)")
            st.caption(f"**{label}** - " + "  ·  ".join(bits))

        latest_debt = debt_df.iloc[-1]
        d_col1, d_col2 = st.columns(2)
        d_col1.metric(
            f"Total debt (as of {latest_debt['period_end'].date()})",
            format_usd(latest_debt["total"]),
        )
        if len(debt_df) > 1:
            prior_debt = debt_df.iloc[-2]
            change = (
                (latest_debt["total"] / prior_debt["total"] - 1) * 100
                if prior_debt["total"]
                else float("nan")
            )
            d_col2.metric("Change vs. prior filing", f"{change:,.0f}%" if pd.notna(change) else "-")

        debt_fig = go.Figure()
        for label in DEBT_SEGMENTS.keys():
            debt_fig.add_trace(
                go.Bar(
                    x=debt_df["quarter_label"],
                    y=debt_df[label],
                    name=label,
                    marker_color=DEBT_SEGMENT_COLORS.get(label),
                    hovertemplate=f"%{{x}}<br>{label}: %{{customdata}}<extra></extra>",
                    customdata=[format_usd(v) for v in debt_df[label]],
                )
            )
        debt_fig.update_layout(
            title=f"{company} - Total Debt by Category",
            barmode="stack",
            xaxis_title=None,
            yaxis_title="Debt (USD)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=460,
        )
        st.plotly_chart(debt_fig, use_container_width=True)

        with st.expander("Underlying data"):
            d_show_cols = ["quarter_label", "period_end", *DEBT_SEGMENTS.keys(), "total"]
            st.dataframe(
                debt_df[d_show_cols].rename(
                    columns={"quarter_label": "Quarter", "period_end": "As of", "total": "Total debt"}
                ),
                use_container_width=True,
                hide_index=True,
            )
        st.caption(
            "Breakdown is current vs. non-current for long-term debt and finance lease "
            "liabilities, as reported at the top level of the balance sheet. Where a "
            "company has changed how it labels debt mid-history (e.g. CoreWeave split "
            "\"long-term debt\" into \"recourse\"/\"non-recourse debt\" starting Q2 2026), "
            "the chart reconstructs the equivalent total by summing the new categories for "
            "the periods the old tag stopped covering - see the per-category notes above. "
            "It does not split out individual instruments (term loans, revolver, DDTL, "
            "etc.) - that detail lives in the debt footnote's dimensional XBRL tags, not a "
            "standalone concept, so it isn't pulled by this simple API-based approach."
        )

    st.divider()
    st.subheader(f"{company} - Free Cash Flow")

    with st.spinner(f"Fetching {company} cash flow data from SEC EDGAR..."):
        try:
            fcf_df, fcf_tags, fcf_source = load_fcf(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't fetch cash flow data from SEC EDGAR: {exc}")
            fcf_df, fcf_tags, fcf_source = None, {}, None

    if fcf_df is None:
        missing = [k for k, v in fcf_tags.items() if not v]
        st.info(
            f"No free cash flow data found for {company} (CIK {cik}) from SEC or Yahoo Finance."
            + (f" Missing SEC tags: {', '.join(missing)}." if missing else "")
        )
    elif fcf_source == "yfinance":
        st.caption(
            f"SEC EDGAR has no quarterly cash-flow data for {company} (likely a foreign "
            "private issuer filing 20-F/6-K instead of 10-K/10-Q, with no year-to-date "
            "figures to decompose) - showing Yahoo Finance's quarterly cash flow "
            f"statement instead  ·  {len(fcf_df)} quarters found (typically only the "
            "trailing several). FCF is still computed as operating cash flow minus capex, "
            "same as the SEC-derived path."
        )
    else:
        st.caption(
            f"{len(fcf_df)} quarters found  ·  operating cash flow: `{fcf_tags['operating_cash_flow']}`"
            f"  ·  capex: `{fcf_tags['capex']}`"
        )
        st.caption(
            "Both figures are reported year-to-date by SEC filers (e.g. \"six months "
            "ended\"), not as discrete quarters - each quarter here is reconstructed by "
            "subtracting consecutive cumulative totals, so a quarter only appears once "
            "its prior cumulative figure is also on file (see data_processing.py)."
        )

    if fcf_df is not None:

        exclude_capex = st.toggle(
            "Exclude capex (show operating cash flow only)",
            value=False,
            key="fcf_exclude_capex",
            help=(
                "Off: chart shows free cash flow (operating cash flow minus capex), the "
                "cash actually left over after infrastructure spend. On: chart shows "
                "operating cash flow alone, ignoring capex entirely - useful for judging "
                "the core business's cash generation separately from how much it's "
                "currently plowing into GPUs/data centers."
            ),
        )
        chart_col = "operating_cash_flow" if exclude_capex else "fcf"
        chart_title = "Operating Cash Flow" if exclude_capex else "Free Cash Flow"
        metric_label = "Operating cash flow" if exclude_capex else "FCF"

        latest_fcf = fcf_df.iloc[-1]
        f_col1, f_col2, f_col3 = st.columns(3)
        f_col1.metric(f"{metric_label} ({latest_fcf['quarter_label']})", format_usd(latest_fcf[chart_col]))
        f_col2.metric("Operating cash flow", format_usd(latest_fcf["operating_cash_flow"]))
        f_col3.metric("Capex", format_usd(latest_fcf["capex"]))

        fcf_fig = go.Figure()
        fcf_fig.add_trace(
            go.Bar(
                x=fcf_df["quarter_label"],
                y=fcf_df[chart_col],
                name=chart_title,
                marker_color=[
                    FCF_POSITIVE_COLOR if v >= 0 else FCF_NEGATIVE_COLOR for v in fcf_df[chart_col]
                ],
                hovertemplate=f"%{{x}}<br>{chart_title}: %{{customdata}}<extra></extra>",
                customdata=[format_usd(v) for v in fcf_df[chart_col]],
            )
        )
        # The two components behind the bar, always shown as lines so it's clear
        # what's driving FCF (or OCF, if capex is toggled out of the bar) each
        # quarter - e.g. a quarter where FCF drops can be read as "OCF fell" vs.
        # "capex spiked" at a glance instead of only from the data table.
        fcf_fig.add_trace(
            go.Scatter(
                x=fcf_df["quarter_label"],
                y=fcf_df["operating_cash_flow"],
                name="Operating cash flow",
                mode="lines+markers",
                line=dict(color=ACCENT, width=2),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Operating cash flow: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in fcf_df["operating_cash_flow"]],
            )
        )
        fcf_fig.add_trace(
            go.Scatter(
                x=fcf_df["quarter_label"],
                y=fcf_df["capex"],
                name="Capex",
                mode="lines+markers",
                line=dict(color="#DD8A3A", width=2),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Capex: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in fcf_df["capex"]],
            )
        )
        fcf_fig.update_layout(
            title=f"{company} - {chart_title} by Quarter",
            xaxis_title=None,
            yaxis_title="USD",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=460,
        )
        st.plotly_chart(fcf_fig, use_container_width=True)
        st.caption(
            f"Bars show {chart_title.lower()}; the two lines show operating cash flow and "
            "capex separately underneath it, so you can see which one moved."
            + (
                " With the toggle on, the bar matches the operating cash flow line exactly "
                "- capex is shown for reference but isn't netted out of the bar."
                if exclude_capex
                else ""
            )
        )

        with st.expander("Underlying data"):
            f_show_cols = ["quarter_label", "period_end", "operating_cash_flow", "capex", "fcf"]
            st.dataframe(
                fcf_df[f_show_cols].rename(
                    columns={
                        "quarter_label": "Quarter",
                        "period_end": "As of",
                        "operating_cash_flow": "Operating cash flow",
                        "capex": "Capex",
                        "fcf": "Free cash flow",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader(f"{company} - Net Income / Loss")

    with st.spinner(f"Fetching {company} net income data from SEC EDGAR..."):
        try:
            ni_df, ni_tag, ni_source = load_net_income(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't fetch net income data from SEC EDGAR: {exc}")
            ni_df, ni_tag, ni_source = None, None, None

    if ni_df is None:
        st.info(
            f"No net income/loss data found for {company} (CIK {cik}) from SEC or Yahoo Finance. "
            "It may not be SEC-reporting, or the 'NetIncomeLoss' tag wasn't used in its filings."
        )
    elif ni_source == "yfinance":
        st.caption(
            f"SEC EDGAR has no quarterly net income data for {company} - showing Yahoo "
            f"Finance's quarterly income statement instead  ·  {len(ni_df)} quarters found "
            "(typically only the trailing several)."
        )
    else:
        st.caption(
            f"XBRL tag used: `{ni_tag}`  ·  {len(ni_df)} quarters found  ·  Q4 is never "
            "available as a standalone figure (10-Ks report the full year, not a discrete "
            "three-months-ended-Dec-31 column) - same gap the revenue chart has."
        )

    if ni_df is not None:

        latest_ni = ni_df.iloc[-1]
        n_col1, n_col2 = st.columns(2)
        n_col1.metric(f"Net income/loss ({latest_ni['quarter_label']})", format_usd(latest_ni["value"]))
        ni_yoy = latest_ni.get("yoy_growth")
        n_col2.metric("YoY change", f"{ni_yoy:,.0f}%" if pd.notna(ni_yoy) else "-")

        ni_fig = go.Figure()
        ni_fig.add_trace(
            go.Bar(
                x=ni_df["quarter_label"],
                y=ni_df["value"],
                marker_color=[FCF_POSITIVE_COLOR if v >= 0 else FCF_NEGATIVE_COLOR for v in ni_df["value"]],
                hovertemplate="%{x}<br>Net income/loss: %{customdata}<extra></extra>",
                customdata=[format_usd(v) for v in ni_df["value"]],
            )
        )
        ni_fig.update_layout(
            title=f"{company} - Net Income / Loss by Quarter",
            xaxis_title=None,
            yaxis_title="Net income / loss (USD)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            margin=dict(t=60, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(ni_fig, use_container_width=True)

        with st.expander("Underlying data"):
            n_show_cols = ["quarter_label", "period_start", "period_end", "value", "yoy_growth", "form", "filed"]
            st.dataframe(
                ni_df[n_show_cols].rename(
                    columns={
                        "quarter_label": "Quarter",
                        "period_start": "Start",
                        "period_end": "End",
                        "value": "Net income / loss (USD)",
                        "yoy_growth": "YoY %",
                        "form": "Filing",
                        "filed": "Filed date",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader(f"{company} - Margins")
    st.caption(
        "Net margin = net income ÷ revenue; FCF margin = free cash flow ÷ revenue, each as "
        "a percentage per quarter. A quarter only appears once both figures behind it are "
        "on file (an inner join on the reporting period), so this can lag behind the "
        "standalone Revenue/Net Income/Free Cash Flow charts above by a quarter or two."
    )

    with st.spinner(f"Computing {company} margins..."):
        try:
            net_margin_df = load_net_margin(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't compute net margin: {exc}")
            net_margin_df = pd.DataFrame()
        try:
            fcf_margin_df = load_fcf_margin(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't compute FCF margin: {exc}")
            fcf_margin_df = pd.DataFrame()

    if (net_margin_df is None or net_margin_df.empty) and (fcf_margin_df is None or fcf_margin_df.empty):
        st.info(f"No margin data available for {company} yet - needs both revenue and net income/FCF on file for at least one common quarter.")
    else:
        m_col1, m_col2 = st.columns(2)
        if net_margin_df is not None and not net_margin_df.empty:
            latest_nm = net_margin_df.iloc[-1]
            m_col1.metric(f"Net margin ({latest_nm['quarter_label']})", format_pct(latest_nm["net_margin"]))
        else:
            m_col1.metric("Net margin", "-")
        if fcf_margin_df is not None and not fcf_margin_df.empty:
            latest_fm = fcf_margin_df.iloc[-1]
            m_col2.metric(f"FCF margin ({latest_fm['quarter_label']})", format_pct(latest_fm["fcf_margin"]))
        else:
            m_col2.metric("FCF margin", "-")

        margin_fig = go.Figure()
        if net_margin_df is not None and not net_margin_df.empty:
            margin_fig.add_trace(
                go.Scatter(
                    x=net_margin_df["quarter_label"],
                    y=net_margin_df["net_margin"],
                    name="Net margin",
                    mode="lines+markers",
                    line=dict(color=NET_MARGIN_COLOR, width=2),
                    marker=dict(size=6),
                    hovertemplate="%{x}<br>Net margin: %{customdata}<extra></extra>",
                    customdata=[format_pct(v) for v in net_margin_df["net_margin"]],
                )
            )
        if fcf_margin_df is not None and not fcf_margin_df.empty:
            margin_fig.add_trace(
                go.Scatter(
                    x=fcf_margin_df["quarter_label"],
                    y=fcf_margin_df["fcf_margin"],
                    name="FCF margin",
                    mode="lines+markers",
                    line=dict(color=FCF_MARGIN_COLOR, width=2),
                    marker=dict(size=6),
                    hovertemplate="%{x}<br>FCF margin: %{customdata}<extra></extra>",
                    customdata=[format_pct(v) for v in fcf_margin_df["fcf_margin"]],
                )
            )
        margin_fig.update_layout(
            title=f"{company} - Net Margin & FCF Margin by Quarter",
            xaxis_title=None,
            yaxis_title="Margin (%)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, ticksuffix="%", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=60, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(margin_fig, use_container_width=True)

        with st.expander("Underlying data"):
            if net_margin_df is not None and not net_margin_df.empty:
                st.caption("Net margin")
                st.dataframe(
                    net_margin_df.rename(
                        columns={
                            "quarter_label": "Quarter",
                            "period_end": "As of",
                            "numerator": "Net income / loss (USD)",
                            "revenue": "Revenue (USD)",
                            "net_margin": "Net margin (%)",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            if fcf_margin_df is not None and not fcf_margin_df.empty:
                st.caption("FCF margin")
                st.dataframe(
                    fcf_margin_df.rename(
                        columns={
                            "quarter_label": "Quarter",
                            "period_end": "As of",
                            "numerator": "Free cash flow (USD)",
                            "revenue": "Revenue (USD)",
                            "fcf_margin": "FCF margin (%)",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    st.divider()
    st.subheader(f"{company} - Forward EBITDA (Estimate)")
    st.caption(
        "PROXY, not real consensus EBITDA guidance - yfinance (and SEC) have no actual "
        "analyst EBITDA estimate to pull, so this is computed as next-fiscal-year consensus "
        "REVENUE estimate (Yahoo Finance analysts) × trailing EBITDA margin (operating "
        "income + D&A over the trailing ~4 quarters). See config.py for the full "
        "explanation of why, and its limitations."
    )

    with st.spinner(f"Computing {company} forward EBITDA estimate..."):
        try:
            fwd_ebitda_info = load_forward_ebitda(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't compute forward EBITDA estimate: {exc}")
            fwd_ebitda_info = None

    if fwd_ebitda_info is None or fwd_ebitda_info["forward_ebitda"] is None:
        reasons = []
        if fwd_ebitda_info is None or fwd_ebitda_info["margin"] is None:
            reasons.append("no trailing EBITDA margin available (SEC has no operating income/D&A, "
                            "and Yahoo Finance has no trailing EBITDA figure)")
        if fwd_ebitda_info is None or fwd_ebitda_info.get("forward_revenue_avg") is None:
            reasons.append("no analyst revenue estimate available from Yahoo Finance (likely thin/no "
                            "analyst coverage)")
        st.info(
            f"No forward EBITDA estimate available for {company}"
            + (f" - {', and '.join(reasons)}." if reasons else ".")
        )
    else:
        e_col1, e_col2, e_col3 = st.columns(3)
        e_col1.metric("Forward EBITDA (Est.)", format_usd(fwd_ebitda_info["forward_ebitda"]))
        e_col2.metric("Forward revenue est. (FY+1)", format_usd(fwd_ebitda_info["forward_revenue_avg"]))
        e_col3.metric("Trailing EBITDA margin", format_pct(fwd_ebitda_info["margin"]))

        margin_source_label = {"sec": "SEC filings", "yfinance": "Yahoo Finance (trailing)"}.get(
            fwd_ebitda_info["source"], "unknown"
        )
        detail_bits = [f"Trailing EBITDA margin source: {margin_source_label}"]
        if fwd_ebitda_info["quarters_used"]:
            detail_bits.append(f"{fwd_ebitda_info['quarters_used']} quarters used")
        if fwd_ebitda_info["analysts"]:
            detail_bits.append(f"{fwd_ebitda_info['analysts']} analysts covering the revenue estimate")
        st.caption("  ·  ".join(detail_bits))

    st.divider()
    st.subheader(f"{company} - Forward EV/Sales & P/S (Estimate)")
    st.caption(
        "Real valuation multiples, not a proxy like Forward EBITDA above. Forward P/S = "
        "market cap ÷ next-fiscal-year consensus revenue estimate. Enterprise Value (EV) "
        "= market cap + total debt - cash & equivalents. Forward EV/Sales = EV ÷ that same "
        "forward revenue estimate. Market cap is LIVE; total debt/cash are only as fresh as "
        "the most recently filed quarter - EV mixes a live number with a lagged one by "
        "construction, same caveat as Market Cap vs. RPO."
    )

    with st.spinner(f"Computing {company} forward EV/Sales & P/S..."):
        try:
            fwd_mult_info = load_forward_sales_multiples(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't compute forward EV/Sales & P/S: {exc}")
            fwd_mult_info = None

    if fwd_mult_info is None or (fwd_mult_info["forward_ev_sales"] is None and fwd_mult_info["forward_ps"] is None):
        reasons = []
        if fwd_mult_info is None or fwd_mult_info.get("market_cap") is None:
            reasons.append("no market cap available from Yahoo Finance")
        if fwd_mult_info is None or fwd_mult_info.get("ev") is None:
            reasons.append("no enterprise value available (needs market cap, total debt, and cash all on file)")
        if fwd_mult_info is None or fwd_mult_info.get("forward_revenue_avg") is None:
            reasons.append("no analyst revenue estimate available from Yahoo Finance (likely thin/no "
                            "analyst coverage)")
        st.info(
            f"No forward EV/Sales or P/S data available for {company}"
            + (f" - {', and '.join(reasons)}." if reasons else ".")
        )
    else:
        v_col1, v_col2, v_col3, v_col4 = st.columns(4)
        v_col1.metric("Forward EV/Sales", format_multiple(fwd_mult_info["forward_ev_sales"]))
        v_col2.metric("Forward P/S", format_multiple(fwd_mult_info["forward_ps"]))
        v_col3.metric("Enterprise value", format_usd(fwd_mult_info["ev"]))
        v_col4.metric("Market cap", format_usd(fwd_mult_info["market_cap"]))

        detail_bits = [
            f"Total debt: {format_usd(fwd_mult_info['total_debt'])}"
            + (f" (as of {fwd_mult_info['debt_as_of'].date()})" if fwd_mult_info.get("debt_as_of") is not None else ""),
            f"Cash: {format_usd(fwd_mult_info['cash'])}"
            + (f" (as of {fwd_mult_info['cash_as_of'].date()})" if fwd_mult_info.get("cash_as_of") is not None else ""),
            f"Forward revenue est. (FY+1): {format_usd(fwd_mult_info['forward_revenue_avg'])}",
        ]
        if fwd_mult_info.get("analysts"):
            detail_bits.append(f"{fwd_mult_info['analysts']} analysts covering the revenue estimate")
        st.caption("  ·  ".join(detail_bits))

    st.divider()
    st.subheader(f"{company} - Earnings Per Share")

    with st.spinner(f"Fetching {company} EPS data from SEC EDGAR..."):
        try:
            eps_df, eps_tags, eps_source = load_eps(cik, ticker)
        except Exception as exc:
            st.error(f"Couldn't fetch EPS data from SEC EDGAR: {exc}")
            eps_df, eps_tags, eps_source = None, {}, None

    if eps_df is None:
        missing = [k for k, v in eps_tags.items() if not v]
        st.info(
            f"No EPS data found for {company} (CIK {cik}) from SEC or Yahoo Finance."
            + (f" Missing SEC tags: {', '.join(missing)}." if missing else "")
        )
    else:
        if eps_source == "yfinance":
            st.caption(
                f"SEC EDGAR has no quarterly EPS data for {company} - showing Yahoo Finance's "
                f"quarterly income statement instead  ·  {len(eps_df)} quarters found "
                "(typically only the trailing several)."
            )
        else:
            st.caption(
                f"XBRL tags used: basic `{eps_tags['eps_basic']}`, diluted `{eps_tags['eps_diluted']}`  ·  "
                f"{len(eps_df)} quarters found  ·  Q4 is never available as a standalone figure, same gap "
                "as revenue/net income."
            )

        def format_eps(value: float) -> str:
            if value is None or pd.isna(value):
                return "-"
            return f"${value:,.2f}"

        latest_eps = eps_df.iloc[-1]
        e_col1, e_col2 = st.columns(2)
        e_col1.metric(f"Basic EPS ({latest_eps['quarter_label']})", format_eps(latest_eps["eps_basic"]))
        e_col2.metric(f"Diluted EPS ({latest_eps['quarter_label']})", format_eps(latest_eps["eps_diluted"]))

        eps_fig = go.Figure()
        eps_fig.add_trace(
            go.Scatter(
                x=eps_df["quarter_label"],
                y=eps_df["eps_basic"],
                name="Basic EPS",
                mode="lines+markers",
                line=dict(color=EPS_BASIC_COLOR, width=2),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Basic EPS: %{customdata}<extra></extra>",
                customdata=[format_eps(v) for v in eps_df["eps_basic"]],
                connectgaps=False,
            )
        )
        eps_fig.add_trace(
            go.Scatter(
                x=eps_df["quarter_label"],
                y=eps_df["eps_diluted"],
                name="Diluted EPS",
                mode="lines+markers",
                line=dict(color=EPS_DILUTED_COLOR, width=2, dash="dot"),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Diluted EPS: %{customdata}<extra></extra>",
                customdata=[format_eps(v) for v in eps_df["eps_diluted"]],
                connectgaps=False,
            )
        )
        eps_fig.update_layout(
            title=f"{company} - EPS by Quarter",
            xaxis_title=None,
            yaxis_title="EPS (USD/share)",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, tickprefix="$", zeroline=True, zerolinecolor=GRID, zerolinewidth=1),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(eps_fig, use_container_width=True)

        with st.expander("Underlying data"):
            e_show_cols = ["quarter_label", "period_end", "eps_basic", "eps_diluted"]
            st.dataframe(
                eps_df[e_show_cols].rename(
                    columns={
                        "quarter_label": "Quarter",
                        "period_end": "As of",
                        "eps_basic": "Basic EPS",
                        "eps_diluted": "Diluted EPS",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader(f"{company} - Shares Outstanding")

    with st.spinner(f"Fetching {company} shares outstanding data from SEC EDGAR..."):
        try:
            shares_df, shares_tag = load_shares_outstanding(cik)
        except Exception as exc:
            st.error(f"Couldn't fetch shares outstanding data from SEC EDGAR: {exc}")
            shares_df, shares_tag = None, None

    # Yahoo Finance's actual point-in-time share count, fetched up front
    # regardless of whether SEC has anything - it's used two different ways
    # below depending on what SEC returned: as a secondary overlay line when
    # SEC's weighted-average series exists, or as the ENTIRE chart (promoted
    # to primary) when SEC has nothing at all - e.g. Nebius, a foreign
    # private issuer filing 20-F/6-K instead of 10-K/10-Q, so SEC's simple
    # companyconcept API never has a discrete-quarter share count for it,
    # same underlying gap as revenue/net income/EPS/FCF for that company.
    ticker = TICKERS.get(company)
    yf_shares_df = None
    if ticker:
        try:
            yf_shares_df = load_shares_outstanding_yf(ticker)
        except Exception:
            yf_shares_df = None

    if shares_df is None and (yf_shares_df is None or yf_shares_df.empty):
        st.info(
            f"No shares outstanding data found for {company} (CIK {cik}) from SEC or Yahoo Finance. "
            "It may not be SEC-reporting, or none of the known share-count tags matched."
        )
    elif shares_df is None:
        st.caption(
            f"SEC EDGAR has no quarterly share-count data for {company} (likely a foreign "
            "private issuer filing 20-F/6-K instead of 10-K/10-Q) - showing Yahoo Finance's "
            f"actual shares outstanding instead  ·  {len(yf_shares_df)} quarters found "
            "(Yahoo typically only covers the trailing several, unlike SEC's fuller history "
            "for filers that do report it)."
        )
        st.caption(
            "Note this is a different figure from the 'weighted average basic shares' shown "
            "for other companies below - it's an actual point-in-time share count rather "
            "than a period average - since that's what Yahoo Finance provides and SEC has "
            "nothing at all to fall back on here."
        )

        def format_shares(value: float) -> str:
            if value is None or pd.isna(value):
                return "-"
            if abs(value) >= 1e9:
                return f"{value / 1e9:,.2f}B"
            if abs(value) >= 1e6:
                return f"{value / 1e6:,.1f}M"
            return f"{value:,.0f}"

        latest_shares = yf_shares_df.iloc[-1]
        s_col1, s_col2 = st.columns(2)
        s_col1.metric(f"Shares ({latest_shares['quarter_label']})", format_shares(latest_shares["value"]))
        if len(yf_shares_df) > 1:
            prior_shares = yf_shares_df.iloc[-2]
            change = (latest_shares["value"] / prior_shares["value"] - 1) * 100
            s_col2.metric("Change vs. prior quarter", f"{change:,.1f}%")

        shares_fig = go.Figure()
        shares_fig.add_trace(
            go.Scatter(
                x=yf_shares_df["quarter_label"],
                y=yf_shares_df["value"],
                name="Actual shares outstanding (Yahoo Finance)",
                mode="lines+markers",
                line=dict(color=SHARES_YF_LINE_COLOR, width=2),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Actual shares outstanding: %{customdata}<extra></extra>",
                customdata=[format_shares(v) for v in yf_shares_df["value"]],
            )
        )
        shares_fig.update_layout(
            title=f"{company} - Shares Outstanding",
            xaxis_title=None,
            yaxis_title="Shares",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, rangemode="tozero"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(shares_fig, use_container_width=True)

        with st.expander("Underlying data"):
            st.dataframe(
                yf_shares_df.rename(columns={"quarter_label": "Quarter", "period_end": "As of", "value": "Shares outstanding"}),
                use_container_width=True,
                hide_index=True,
            )
    else:
        st.caption(
            f"XBRL tag used: `{shares_tag}`  ·  {len(shares_df)} quarters found"
        )
        st.caption(
            "This is weighted-average BASIC shares outstanding (the EPS denominator), not "
            "a point-in-time share count - a single combined \"shares outstanding as of this "
            "date\" figure often isn't available through SEC's simple API (e.g. CoreWeave's "
            "multiple stock classes are each tagged separately with a dimensional member "
            "rather than as one non-dimensional total). The weighted average tracks dilution "
            "over time just as well and is reported consistently across filers - see "
            "config.py for the full explanation."
        )

        def format_shares(value: float) -> str:
            if value is None or pd.isna(value):
                return "-"
            if abs(value) >= 1e9:
                return f"{value / 1e9:,.2f}B"
            if abs(value) >= 1e6:
                return f"{value / 1e6:,.1f}M"
            return f"{value:,.0f}"

        latest_shares = shares_df.iloc[-1]
        s_col1, s_col2 = st.columns(2)
        s_col1.metric(f"Shares ({latest_shares['quarter_label']})", format_shares(latest_shares["value"]))
        if len(shares_df) > 1:
            prior_shares = shares_df.iloc[-2]
            change = (latest_shares["value"] / prior_shares["value"] - 1) * 100
            s_col2.metric("Change vs. prior quarter", f"{change:,.1f}%")

        # yf_shares_df (Yahoo's actual point-in-time share count) was already
        # fetched above - reused here as a second line alongside the SEC
        # weighted-average series. See config.TICKERS / yfinance_data.py for
        # why this fills a real gap in what SEC's simple API can expose for
        # CoreWeave. A missing/failed fetch is silent by design - the SEC
        # line above is unaffected either way.
        shares_fig = go.Figure()
        shares_fig.add_trace(
            go.Scatter(
                x=shares_df["quarter_label"],
                y=shares_df["value"],
                name="Weighted avg. basic shares (SEC)",
                mode="lines+markers",
                line=dict(color=SHARES_LINE_COLOR, width=2),
                marker=dict(size=6),
                hovertemplate="%{x}<br>Weighted avg. basic shares: %{customdata}<extra></extra>",
                customdata=[format_shares(v) for v in shares_df["value"]],
            )
        )
        if yf_shares_df is not None and not yf_shares_df.empty:
            shares_fig.add_trace(
                go.Scatter(
                    x=yf_shares_df["quarter_label"],
                    y=yf_shares_df["value"],
                    name="Actual shares outstanding (Yahoo Finance)",
                    mode="lines+markers",
                    line=dict(color=SHARES_YF_LINE_COLOR, width=2, dash="dash"),
                    marker=dict(size=6),
                    hovertemplate="%{x}<br>Actual shares outstanding: %{customdata}<extra></extra>",
                    customdata=[format_shares(v) for v in yf_shares_df["value"]],
                )
            )
        shares_fig.update_layout(
            title=f"{company} - Shares Outstanding",
            xaxis_title=None,
            yaxis_title="Shares",
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor=GRID, rangemode="tozero"),
            xaxis=dict(showgrid=False, type="category", categoryorder="category ascending"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(t=80, l=10, r=10, b=10),
            height=440,
        )
        st.plotly_chart(shares_fig, use_container_width=True)
        if yf_shares_df is None:
            st.caption(
                "Actual shares-outstanding overlay (Yahoo Finance) isn't available right now "
                "- showing the SEC weighted-average series only. This can happen if the "
                "`yfinance` package isn't installed, the ticker isn't set in config.TICKERS, "
                "or the request failed (e.g. no network access in this environment)."
            )
        else:
            st.caption(
                "The dashed line is an actual historical shares-outstanding snapshot from "
                "Yahoo Finance (a point-in-time count), shown alongside the SEC line (a "
                "period-average count) - see the note above for why they're not the same thing."
            )

        with st.expander("Underlying data"):
            s_show_cols = ["quarter_label", "period_start", "period_end", "value", "form", "filed"]
            st.dataframe(
                shares_df[s_show_cols].rename(
                    columns={
                        "quarter_label": "Quarter",
                        "period_start": "Start",
                        "period_end": "End",
                        "value": "Weighted avg. basic shares",
                        "form": "Filing",
                        "filed": "Filed date",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
            if yf_shares_df is not None and not yf_shares_df.empty:
                st.caption("Actual shares outstanding (Yahoo Finance)")
                st.dataframe(
                    yf_shares_df.rename(
                        columns={
                            "quarter_label": "Quarter",
                            "period_end": "As of",
                            "value": "Actual shares outstanding",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    st.caption(
        "Source: SEC EDGAR XBRL Frames API (data.sec.gov). Figures are as reported "
        "in 10-Q/10-K filings and may be restated in later filings."
    )
