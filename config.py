"""
Configuration for the Neocloud Research dashboard.

Add new companies here as you expand coverage beyond CoreWeave.
CIK = SEC's Central Index Key (10-digit, zero-padded when used in URLs).
You can look up a CIK at https://www.sec.gov/cgi-bin/browse-edgar or via
the SEC's company_tickers.json (see sec_edgar.lookup_cik_by_ticker).
"""

# --- SEC EDGAR access ---
# The SEC requires a descriptive User-Agent with a real contact so they can
# reach you if your traffic causes problems. Replace with your own name/email
# before deploying (SEC will rate-limit or block generic/missing UAs).
SEC_USER_AGENT = "CGteam NeoCloud Research (contact: set-your-email@castellangroup.com)"

# --- Companies tracked ---
# name -> SEC CIK (as a string, no leading zeros needed; we zero-pad in code)
COMPANIES = {
    "CoreWeave": "1769628",
    "Nebius": "1513845",
    "IREN": "1878848",
    # Bitcoin miners pivoting toward AI/GPU hosting - see the notes below for
    # each on legacy-CIK/accounting-reset caveats before trusting a chart
    # that spans their whole history. Included alongside IREN as the
    # broader "miner pivoting to AI" cohort, even though (per the earlier
    # CoreWeave/Nebius/IREN peer discussion) they lean more toward hosting/
    # power landlords than direct GPU-cloud sellers.
    "Core Scientific": "1839341",
    "Hut 8": "1964789",
    "TeraWulf": "1083301",
    "Cipher Mining": "1819989",
    "CleanSpark": "827876",
    # "Lambda": "...",
    # "Crusoe": "...",       # private - not SEC-reporting, will need alt source
    # "Together AI": "...", # private - not SEC-reporting, will need alt source
}

# NOTE on IREN specifically - much cleaner than Nebius, but one real quirk:
# IREN Ltd (formerly Iris Energy) is incorporated in Australia but files as
# an ordinary DOMESTIC filer - 10-K/10-Q, not 20-F/6-K - and tags revenue
# under RevenueFromContractWithCustomerExcludingAssessedTax (the first tag
# in REVENUE_TAGS) with real discrete-quarter data, same as CoreWeave. No
# special-casing needed for revenue/net income/EPS/total assets/debt.
#
# The one thing that WILL likely come back empty via SEC: Free Cash Flow.
# IREN's fiscal year runs JULY 1 - JUNE 30, not calendar-year, and
# concept_to_ytd_cumulative_df (see its docstring / the operating_cash_flow
# note below) only recognizes a YTD-cumulative period if it starts exactly
# on Jan 1 of its end year - a hard assumption that happens to hold for
# CoreWeave but doesn't here. So IREN's OCF/capex will likely fail that
# filter entirely via SEC, same symptom as Nebius's FCF gap, but for a
# totally different underlying reason (fiscal year mismatch here, no
# quarterly data at all for Nebius) - the existing yfinance fallback in
# load_fcf should still cover it. Originally a Bitcoin miner, IREN has been
# pivoting into GPU/AI cloud (e.g. a large capacity deal with Microsoft).

# NOTE on Nebius specifically - its filings behave very differently from
# CoreWeave's, and most of this app's charts will look sparse or empty for
# it as a result:
#
# 1. Nebius Group N.V. is a Dutch foreign private issuer. It files an annual
#    20-F (not a 10-K) and furnishes 6-Ks for interim updates (not 10-Qs).
#    Checked directly against data.sec.gov: every us-gaap XBRL fact on file
#    for this CIK - revenue, assets, etc. - comes from 20-F filings only,
#    one point per FISCAL YEAR. There is no quarterly XBRL data available
#    through SEC's API at all (6-K interim reports aren't tagged the same
#    way 10-Qs are). Concretely: every chart built on concept_to_quarterly_df
#    (revenue, net income, EPS, shares outstanding) or the YTD-cumulative
#    path (FCF) will show "no data found" for Nebius, since those all filter
#    for quarter-length (or YTD-from-Jan-1) periods that simply don't exist
#    here. Only the point-in-time charts (total assets, total debt, RPO)
#    will show anything, and even those are just 1-2 annual snapshots.
#
# 2. This CIK is a continuation of the SAME LEGAL ENTITY that was formerly
#    Yandex N.V. - Nebius Group is what's left after Yandex sold off its
#    Russian search/ads/etc. businesses in 2024 and rebranded around its AI
#    cloud/infrastructure business. That means SEC's XBRL history for this
#    CIK going back to 2011 is almost entirely legacy Yandex consolidated
#    financials (billions in revenue from an entirely different, now-
#    divested business) - NOT comparable to CoreWeave or to Nebius's current
#    AI-cloud business. The one clean discontinuity: reported FY2024 revenue
#    drops from $8.9B (FY2023, old Yandex) to $117.5M (FY2024, continuing
#    operations only), which is the real "start" of the comparable Nebius
#    AI-cloud numbers. Any chart/table spanning pre-2024 data for Nebius
#    should be treated as showing a different company in substance, even
#    though the CIK/CommonStock ticker history is continuous.
#
# Bottom line: Nebius is included because it's a legitimate, requested
# neocloud comparison, but don't be surprised if most quarterly charts come
# back empty for it - that's the real shape of what SEC exposes here, not a
# bug. yfinance (see TICKERS below) is likely a better source for Nebius's
# more granular figures if that's needed later.

# NOTE on Core Scientific specifically - emerged from CHAPTER 11 bankruptcy
# with the reorganization plan effective 2024-01-23 (confirmed via its own
# 8-K), applying FRESH-START ACCOUNTING on that date: the balance sheet was
# reset (assets/liabilities/equity revalued) as of the emergence date, so
# pre- vs. post-2024-01-23 XBRL figures reflect two different accounting
# bases ("Predecessor" vs. "Successor" in SEC filing terminology) under the
# SAME CIK (1839341 has been Core Scientific's CIK continuously since its
# 2022 SPAC merger - this isn't a reused-shell issue like TeraWulf below,
# just an accounting-basis discontinuity). A point-in-time chart (total
# assets, debt) spanning the emergence date can show a confusing cliff/reset
# that reflects the accounting event, not necessarily a real operational
# change - see DATA_START_DATE below, which cuts point-in-time concepts to
# the post-emergence ("Successor") basis only. Revenue uses the `Revenues`
# tag, not `RevenueFromContractWithCustomerExcludingAssessedTax` (which
# 404s for this filer) - already covered by REVENUE_TAGS' fallback order.
# Also note: a proposed 2025 merger with CoreWeave was announced, then
# TERMINATED after a shareholder vote against it (~2025-10-30) - Core
# Scientific remains an independent public filer.

# NOTE on Hut 8 specifically - Canadian-headquartered but files as a
# DOMESTIC filer (10-K/10-Q), not a foreign private issuer (20-F/6-K) - so
# it behaves like CoreWeave/IREN here, not like Nebius.
# CIK 1964789 is a NEWLY CREATED entity from the Nov 2023 merger of the
# original "Hut 8 Mining Corp" and "US Bitcoin Corp" (that merger triggered
# a 10-KT transition report for the stub period ended 2023-12-31) - unlike
# TeraWulf below, this CIK was created for the merger rather than reused
# from an unrelated pre-existing shell company, so no DATA_START_DATE entry
# is needed here; there's no unrelated legacy business hiding in its
# history. Revenue uses the standard
# RevenueFromContractWithCustomerExcludingAssessedTax tag directly.

# NOTE on TeraWulf specifically - CIK 1083301 is a REUSED SHELL: it
# previously belonged to "IKONICS Corp" (and before that "Chromaline Corp"),
# an unrelated imaging/photochemical company, until a reverse merger
# completed 2021-12-13 (confirmed via TeraWulf's own 8-K/A, the "Closing
# Date"). XBRL data tagged to this CIK before that date belongs to IKONICS'
# imaging business, NOT TeraWulf's bitcoin mining/AI-hosting business - same
# category of problem as Nebius's legacy Yandex data, just from a corporate
# shell reuse rather than a business divestiture. See DATA_START_DATE below.
# Revenue uses the standard RevenueFromContractWithCustomerExcludingAssessed
# Tax tag directly (also has some stale post-merger data under the older
# `Revenues` tag - don't be surprised if that one shows a few years of
# overlap before going empty).

# NOTE on Cipher Mining specifically - a former SPAC ("Good Works
# Acquisition Corp"), standard de-SPAC pattern (CIK 1819989 unchanged
# since). Recently renamed to "Cipher Digital Inc." in SEC's own company
# records, though the ticker (CIFR) is unchanged. Pre-merger SPAC-era data
# (mostly just trust-account cash, no real operations) can still show up as
# a smaller-scale version of the same "history before the real business
# existed" issue as TeraWulf/Nebius - worth adding a DATA_START_DATE entry
# later if it turns out to look visually confusing on a chart; not added
# preemptively since a SPAC's pre-merger trust-account assets are a much
# smaller, less misleading number than IKONICS' or old Yandex's real
# operating businesses. Revenue uses the `Revenues` tag, not
# `RevenueFromContractWithCustomerExcludingAssessedTax` (404s for this
# filer) - already covered by REVENUE_TAGS' fallback order.

# NOTE on CleanSpark specifically - has a NON-CALENDAR fiscal year, FYE
# SEPTEMBER 30 (confirmed directly against its own FY2025 10-K, "Fiscal
# Year Ended September 30, 2025") - the one outlier of this whole group,
# all four of the others above use a calendar fiscal year. Same downstream
# caveat as IREN's non-calendar year: concept_to_ytd_
# cumulative_df's Jan-1-fiscal-year-start assumption means SEC-derived
# operating_cash_flow/capex (and therefore FCF) will likely come back empty
# via SEC, with load_fcf's yfinance fallback expected to cover it in
# practice. Revenue uses the `Revenues` tag, not `RevenueFromContractWith
# CustomerExcludingAssessedTax` (404s for this filer) - already covered by
# REVENUE_TAGS' fallback order.

# us-gaap XBRL tags to try, in priority order, when pulling revenue.
# Different filers tag revenue differently, so we fall back down this list.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
]

# Other XBRL concepts worth pulling later for the broader comparison doc.
OTHER_TAGS = {
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    # Used to compute Enterprise Value (EV = market cap + total debt - cash)
    # for the Forward EV/Sales metric. A couple of fallback tags included
    # since not every filer uses the plain "carrying value" tag.
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
        "Cash",
    ],
    "total_assets": ["Assets"],
    "total_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    # Used for the Free Cash Flow chart (FCF = operating cash flow - capex).
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    # Weighted-average BASIC shares, not a point-in-time share count - see the
    # note below on why.
    "shares_outstanding": [
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
    # EPS is a standard discrete-quarter income-statement tag, same pattern
    # as net_income/revenue - no YTD decomposition needed.
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    # Remaining performance obligations ("backlog") - contracted, not-yet-
    # recognized future revenue. Point-in-time like total_assets, not a
    # per-quarter flow, so it goes through concept_to_point_in_time_df.
    "remaining_performance_obligation": ["RevenueRemainingPerformanceObligation"],
    # Depreciation & amortization - used only to build a trailing EBITDA
    # proxy (EBITDA = operating income + D&A) for the Forward EBITDA
    # feature. Like operating_cash_flow/capex, this is a CASH FLOW STATEMENT
    # line reported year-to-date cumulative, not a discrete quarter - see
    # the operating_cash_flow/capex note below, same decomposition applies.
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
}

# Which "units" bucket (as SEC's XBRL JSON keys it) to read for each
# OTHER_TAGS concept. Anything not listed here defaults to "USD" - only
# share-count and per-share concepts need to be called out.
UNITS_KEY_OVERRIDES = {
    "shares_outstanding": "shares",
    "eps_basic": "USD/shares",
    "eps_diluted": "USD/shares",
}

# --- Company descriptions (Single Company page summary header) ---
# Short, factual, Claude-authored summaries - not pulled from any filing or
# API, since there's no clean structured source for "what does this company
# do" (SEC's own "business description" is free-text buried inside Item 1
# of the 10-K, not a queryable field). These are a snapshot as of when they
# were written and should be periodically re-checked/updated by hand,
# especially for IREN, which is actively pivoting its business mix (bitcoin
# mining -> GPU/AI cloud) and could describe it differently within a year
# or two.
COMPANY_DESCRIPTIONS = {
    "CoreWeave": (
        "CoreWeave is a specialized cloud infrastructure provider built around large-scale "
        "NVIDIA GPU compute for AI training and inference. Originally an Ethereum mining "
        "operation, it pivoted into GPU cloud services and has grown into one of the "
        "largest independent providers of AI compute capacity, serving AI labs, model "
        "developers, and large enterprises under multi-year contracts."
    ),
    "Nebius": (
        "Nebius Group is an Amsterdam-headquartered AI infrastructure company built around "
        "GPU cloud services, formed after Yandex N.V. divested its Russian search/ads "
        "businesses in 2024 and rebranded the remaining entity around its AI-cloud "
        "business. It offers GPU compute, managed AI/ML tooling, and related "
        "infrastructure to AI developers, primarily across Europe and the US."
    ),
    "IREN": (
        "IREN Ltd (formerly Iris Energy) is an Australian-founded, Nasdaq-listed operator "
        "of vertically integrated, renewable-powered data centers, originally built for "
        "Bitcoin mining. It has been repositioning toward GPU cloud and AI compute, "
        "including large capacity agreements with major AI/cloud customers, while "
        "continuing to operate its Bitcoin mining business alongside the buildout."
    ),
    "Core Scientific": (
        "Core Scientific is one of the largest Bitcoin miners in North America, now also "
        "hosting AI/HPC infrastructure for third parties (including CoreWeave) at its data "
        "centers. It emerged from Chapter 11 bankruptcy in January 2024 and, in late 2025, "
        "saw its shareholders reject a proposed acquisition by CoreWeave, remaining an "
        "independent public company."
    ),
    "Hut 8": (
        "Hut 8 is a vertically integrated digital infrastructure company spanning Bitcoin "
        "mining, AI/HPC data center hosting, and power generation, formed by the 2023 "
        "merger of Hut 8 Mining and US Bitcoin Corp. It has signed large AI infrastructure "
        "agreements, including a Google-backed data center deal announced in 2026."
    ),
    "TeraWulf": (
        "TeraWulf operates low-carbon Bitcoin mining data centers in the US, powered "
        "substantially by nuclear and hydroelectric energy, and has been expanding into "
        "AI/HPC hosting for third parties alongside its mining operations."
    ),
    "Cipher Mining": (
        "Cipher Mining is a US-based Bitcoin mining company that went public via SPAC "
        "merger, operating a fleet of mining data centers and pursuing AI/HPC hosting "
        "contracts as an extension of its infrastructure."
    ),
    "CleanSpark": (
        "CleanSpark is a US-based Bitcoin miner focused on efficient, vertically integrated "
        "mining operations across its own data centers, with a fiscal year ending September "
        "30 rather than the calendar year."
    ),
}

# --- Yahoo Finance tickers (optional supplemental data source) ---
# Used by yfinance_data.py to pull an ACTUAL point-in-time shares-outstanding
# history, which fills a real gap in the SEC data: see the shares_outstanding
# note above - CoreWeave's multi-class stock structure means SEC's simple API
# never exposes one combined "shares outstanding as of this date" figure, only
# the weighted-average count used for EPS. Yahoo Finance publishes a genuine
# historical snapshot series instead, so it's shown alongside (not instead of)
# the SEC weighted-average line. This is intentionally a separate, optional
# source: yfinance scrapes/derives data outside SEC's structured filings, so
# it's treated as a supplement, and every call into it is wrapped so a
# failure (ticker not found, network hiccup, yfinance API change) just means
# that one extra line doesn't show up - never a crash.
TICKERS = {
    "CoreWeave": "CRWV",
    "Nebius": "NBIS",
    "IREN": "IREN",
    "Core Scientific": "CORZ",
    "Hut 8": "HUT",
    "TeraWulf": "WULF",
    "Cipher Mining": "CIFR",
    "CleanSpark": "CLSK",
}

# --- Per-company data start date (drops anything reported before it) ---
# Exists specifically for Nebius: its point-in-time SEC concepts (total
# assets, debt) pull EVERY entry ever filed under the tag, with no filtering
# by date or period length the way concept_to_quarterly_df has (see the
# Nebius/Yandex note above) - so without this, those charts include ~13
# years of the LEGACY YANDEX business (tens of billions in assets) sitting
# right next to the current, much smaller Nebius AI-cloud business under the
# same CIK. That's not just visually confusing (a huge cliff-drop with no
# explanation) - mixed into a cross-company comparison chart, it also used
# to break the x-axis's chronological ordering entirely, since Plotly orders
# categorical axis labels by first-appearance-across-traces, not
# chronologically, unless told otherwise.
# Applied to total_assets/debt/RPO (the point-in-time concepts) in app.py's
# single-company and comparison views alike - NOT to revenue/net income/EPS/
# FCF, which are already immune (concept_to_quarterly_df only keeps ~91-day
# discrete periods, which Nebius's annual-only SEC filings never have any
# of; the yfinance fallback used for those only ever returns the trailing
# few quarters anyway).
DATA_START_DATE = {
    "Nebius": "2024-01-01",
    # Fresh-start accounting on Chapter 11 emergence - see the long note
    # above. Cuts point-in-time charts (total assets, debt) to the
    # post-emergence ("Successor") accounting basis only.
    "Core Scientific": "2024-01-23",
    # Reused CIK (formerly IKONICS Corp, an unrelated imaging company) -
    # see the long note above. Cuts point-in-time charts to dates on/after
    # the reverse merger that actually made this TeraWulf's business.
    "TeraWulf": "2021-12-13",
}

# NOTE on shares_outstanding: the obvious tag ("how many shares exist as of
# this date", us-gaap:CommonStockSharesOutstanding, or the cover-page
# dei:EntityCommonStockSharesOutstanding) 404s for CoreWeave - it has
# multiple share classes (Class A/B/C), and each class's count is tagged
# separately with a dimensional member rather than as one combined
# non-dimensional total, which (like the debt footnote breakdown) isn't
# reachable through the simple companyconcept API. Weighted-average basic
# shares outstanding - the EPS denominator - IS a single combined-across-
# classes figure and IS a plain (non-dimensional) tag, so it's used here as
# the practical stand-in. It's not exactly the same thing (a period average
# vs. a point-in-time count), but it tracks dilution over time just as well
# and is what most public dilution-comparison metrics use anyway.
# It's also a discrete-quarter income-statement-style item, like revenue and
# net income (see the OTHER note below) - NOT year-to-date cumulative like
# operating_cash_flow/capex - so it goes through concept_to_quarterly_df,
# not the YTD-decomposition path.

# NOTE on net_income and shares_outstanding sharing a quirk with revenue:
# CoreWeave's 10-Qs tag income-statement items TWICE per quarter - once as
# the discrete "three/six months ended" comparative column that's actually
# needed here, alongside the always-present year-to-date column. Unlike
# operating_cash_flow/capex, the discrete-quarter figure IS directly
# available (confirmed for NetIncomeLoss and WeightedAverageNumberOf­
# SharesOutstandingBasic), so no YTD decomposition is needed - straight
# concept_to_quarterly_df, same as revenue. The one gap: Q4 never gets a
# standalone discrete tag (10-Ks report the full year, not a "three months
# ended Dec 31" column), so Q4 is simply absent from these charts, same
# pre-existing gap the revenue chart already has.

# NOTE on remaining_performance_obligation ("backlog"): this is contracted,
# not-yet-recognized revenue - CoreWeave's own commentary calls it "RPO" and
# it's the closest thing to a forward-looking capacity/demand signal in the
# filings. It's tagged cleanly and non-dimensionally, so it's a simple
# companyconcept pull like total_assets/cash - no footnote parsing needed.
# This is NOT the same as CoreWeave's own purchase/capacity commitments (GPU
# purchases, data center buildout obligations owed by CoreWeave rather than
# to it) - those are disclosed only inside the "Commitments and
# Contingencies" footnote as a maturity schedule (checked: the standard
# us-gaap purchase-commitment tags 404 for CoreWeave), which would need the
# same dimensional instance-document parsing as DEBT_SEGMENTS below, not a
# simple tag lookup. Not implemented (yet).
#
# RPO_FALLBACK_TAGS below handles a related gap: a filer that simply STOPS
# reporting RevenueRemainingPerformanceObligation at some point while
# continuing to file (formerly used for Applied Digital, since removed from
# COMPANIES - kept as a worked example: its last value under the primary tag
# was its FY2023 10-K, period end 2023-05-31 - nothing since, even though it
# was an active, current filer). Per-company, optional list of (taxonomy,
# tag) fallbacks merged in via the same "primary value where reported, else
# fallback" rule DEBT_SEGMENTS/build_debt_segment_df already use elsewhere -
# for any balance-sheet date the primary RPO tag doesn't cover.
#
# IMPORTANT CAVEAT - read before adding a company here: the fallback tag is
# NOT scope-equivalent to RPO. RPO is the FULL remaining contracted revenue
# (billed AND unbilled) - the "backlog" number companies tout in earnings
# calls. ContractWithCustomerLiability is standard contract-liability/
# deferred-revenue: only the BILLED-but-undelivered portion, a much
# narrower balance-sheet figure. Confirmed for Applied Digital (before it
# was removed): RPO peaked at $48.7M (FY2023 10-K) while
# ContractWithCustomerLiability ran $0-$39M across FY2024-FY2026 - broadly
# similar order of magnitude here, but there's no guarantee that holds for a
# company with a very different billing-vs-delivery cadence. A chart
# splicing the two together at the switchover date reflects a DEFINITION
# change, not necessarily a real change in the underlying business -
# flagged in the UI caption wherever shown, but worth remembering when
# reading the number itself.
RPO_FALLBACK_TAGS = {}

# RPO_WEB_SOURCED - manually researched from web search, NOT pulled from any
# API, and NOT XBRL.
#
# For the bitcoin-miner-pivoting-to-AI-hosting cohort (Core Scientific, Hut 8,
# TeraWulf, Cipher Mining, CleanSpark), SEC XBRL simply has no usable backlog
# figure at all: RevenueRemainingPerformanceObligation is either never
# reported or stops years ago, and the closest fallback tags
# (ContractWithCustomerLiability / DeferredRevenueCurrent) represent billed-
# but-undelivered deposits or deferred revenue - a much smaller and more
# volatile number than the actual multi-year, multi-billion-dollar hosting
# contracts these companies sign. Unlike Applied Digital's RPO_FALLBACK_TAGS
# case, there's no XBRL tag - conceptually related or not - worth splicing in.
#
# Per explicit user request, this dict instead holds a manually-compiled
# TIME SERIES of "total contract value" figures as each was disclosed - in
# press releases, SEC 8-K Exhibit 99.1s, or investor presentations (prose
# disclosures, not structured/taggable XBRL facts) - built by web search
# across each company's dealmaking history. app.py's load_web_sourced_rpo_df
# turns each company's list below into a chartable series, used as a LAST-
# RESORT fallback (in load_rpo_with_web_fallback) only when SEC XBRL has
# nothing at all for that company - flagged distinctly in every chart/table
# that shows it (different line style, its own caption/marker), never
# silently blended with real XBRL data.
#
# Important caveats, in addition to the standalone-caveats already covered by
# RPO_FALLBACK_TAGS above:
#   - These points are IRREGULARLY DATED press-release/earnings disclosures,
#     not standardized quarterly filings - gaps of many months are normal and
#     don't mean "no activity," just "no new disclosure."
#   - Several companies' totals are CUMULATIVE across multiple stacked deals
#     with the SAME counterparty (e.g. Core Scientific's CoreWeave deal grew
#     via several expansions - each point below is the new running total,
#     not incremental), while others are per-deal totals with DIFFERENT
#     counterparties that were never combined into one figure by the company
#     itself (e.g. TeraWulf's Fluidstack cumulative total and its separate
#     Anthropic lease) - see each company's "note" field for which case
#     applies before reading the series as one continuous trend.
#   - Some dates are approximate (flagged "date approx." in the note) where
#     the exact disclosure date couldn't be pinned down.
#   - Sourced substantially from secondary reporting (news aggregators
#     quoting press releases) rather than the primary SEC exhibit in every
#     case - treat dollar figures as approximate and re-verify against the
#     cited source before relying on them for anything high-stakes. Nothing
#     here refreshes automatically; it needs periodic manual re-research as
#     new deals are announced.
# Each entry: date (YYYY-MM-DD, disclosure date), value (USD), note (what it
# covers / whether it's cumulative), source (filing/press release citation).
RPO_WEB_SOURCED = {
    "Core Scientific": [
        {
            "date": "2024-06-03",
            "value": 3_500_000_000,
            "note": "Initial CoreWeave hosting agreement, ~200MW, 12-yr term.",
            "source": "Core Scientific press release, 'Core Scientific to Provide Approximately 200 MW...' (Jun 3, 2024)",
        },
        {
            "date": "2024-06-25",
            "value": 4_725_000_000,
            "note": "Cumulative running total after a new 70MW contract (270MW total).",
            "source": "Core Scientific press release, 'New Contract with CoreWeave for ~70MW' (Jun 25, 2024)",
        },
        {
            "date": "2024-09-01",
            "value": 6_700_000_000,
            "note": "Cumulative running total after a 112MW option exercise. Date approx. (month only confirmed).",
            "source": "Core Scientific press release re: 112MW option exercise; SEC 8-K accession 0001628280-24-029900",
        },
        {
            "date": "2024-10-22",
            "value": 8_700_000_000,
            "note": "Cumulative running total after a final +120MW option (500MW critical IT / 700MW gross across all CoreWeave deals).",
            "source": "SEC 8-K Ex-99.1, accession 0001628280-24-043341 (Oct 22, 2024)",
        },
        {
            "date": "2025-02-26",
            "value": 10_200_000_000,
            "note": "Cumulative running total after a $1.2B Denton, TX expansion (~590MW across 6 sites).",
            "source": "Core Scientific press release, 'Core Scientific and CoreWeave Announce $1.2 Billion Expansion at Denton, TX Site' (Feb 26, 2025)",
        },
        {
            "date": "2026-05-06",
            "value": 10_200_000_000,
            "note": "Reaffirmed 'over $10B' contracted revenue (~590MW/5 sites) in the Q1 FY2026 investor presentation, alongside a 3.0GW total pipeline - no new dollar figure since Feb 2025.",
            "source": "Core Scientific Q1 FY2026 investor presentation (~May 6, 2026)",
        },
    ],
    "Hut 8": [
        {
            "date": "2025-12-17",
            "value": 7_000_000_000,
            "note": "River Bend campus, Fluidstack/Google-backed lease: 245MW, 15-yr base term (up to $17.7B with renewals).",
            "source": "PR Newswire, 'Hut 8 Signs 15-Year, 245 MW AI Data Center Lease at River Bend Campus...' (Dec 17, 2025)",
        },
        {
            "date": "2026-05-01",
            "value": 9_800_000_000,
            "note": "Beacon Point Phase 2 lease, 352MW, 15-yr term. Date approx. (disclosed as signed after Q1 2026 quarter-end).",
            "source": "Referenced in Hut 8's Aug 4, 2026 Q2 earnings release",
        },
        {
            "date": "2026-07-20",
            "value": 19_600_000_000,
            "note": "Beacon Point campus fully commercialized (Phase 1+2 = 704MW of 1GW capacity); campus-level base-term value (up to $50.2B with renewals).",
            "source": "PR Newswire, 'Hut 8 Fully Commercializes 1 GW Beacon Point AI Data Center Campus...' (Jul 20, 2026)",
        },
        {
            "date": "2026-08-04",
            "value": 26_600_000_000,
            "note": (
                "Company-WIDE aggregate across 949MW of investment-grade-backed AI "
                "data center capacity - broader scope than the Beacon-Point-only "
                "figures above, not a like-for-like continuation of that campus series."
            ),
            "source": "SEC 8-K Ex-99.1, accession 0001104659-26-090041 (Q2 2026 earnings release, Aug 4, 2026)",
        },
    ],
    "TeraWulf": [
        {
            "date": "2025-08-14",
            "value": 3_700_000_000,
            "note": "Original Fluidstack/Google-backed agreement, Lake Mariner CB-3/CB-4, 200+MW, 10-yr initial term (up to $8.7B with extensions).",
            "source": "GlobeNewswire, 'TeraWulf Signs 200+ MW, 10-Year AI Hosting Agreements with Fluidstack' (Aug 14, 2025); SEC 8-K accession 0001104659-25-078084",
        },
        {
            "date": "2025-08-18",
            "value": 6_700_000_000,
            "note": "Cumulative Fluidstack total across CB-3/4/5 after a +160MW CB-5 expansion (>360MW total, up to $16B with extensions).",
            "source": "GlobeNewswire, 'TeraWulf Announces Fluidstack Expansion with 160 MW CB-5 Lease at Lake Mariner' (Aug 18, 2025); SEC 8-K accession 0001104659-25-079466",
        },
        {
            "date": "2026-07-06",
            "value": 19_000_000_000,
            "note": (
                "Anthropic 20-year lease at the Justified Data Campus (~401MW) - a "
                "SEPARATE deal/counterparty from the Fluidstack total above, never "
                "combined into one company-wide figure (no reported combined total)."
            ),
            "source": "GlobeNewswire, 'TeraWulf Announces Anthropic Lease at Justified Data Campus...' (Jul 6, 2026); SEC 8-K accession 0001104659-26-080583",
        },
    ],
    "Cipher Mining": [
        {
            "date": "2025-09-25",
            "value": 3_000_000_000,
            "note": "Original Fluidstack/Google-backstopped agreement, Barber Lake site, 168MW, 10-yr initial term (up to $7.0B with extensions).",
            "source": "GlobeNewswire, 'Cipher Mining Signs 168 MW, 10-Year AI Hosting Agreement with Fluidstack' (Sep 25, 2025); SEC 8-K accession 0000950103-25-012168",
        },
        {
            "date": "2025-11-20",
            "value": 3_830_000_000,
            "note": "Cumulative running total after a +56MW expansion at the same Barber Lake site (full 300MW site, up to ~$9B with extensions).",
            "source": "GlobeNewswire, 'Cipher Mining Signs Additional 56 MW, 10-Year AI Hosting Agreement with Fluidstack' (Nov 20, 2025)",
        },
    ],
    "CleanSpark": [
        {
            "date": "2026-08-06",
            "value": 6_600_000_000,
            "note": (
                "First disclosed AI/HPC hosting lease: 175MW, 20-yr triple-net lease "
                "at the Sandersville, GA campus. No hosting backlog existed before "
                "this - CleanSpark was purely bitcoin mining - so it's a single, "
                "very recent point with no multi-quarter track record yet."
            ),
            "source": "SEC 8-K, accession 0001193125-26-337999 (Aug 6, 2026); Benzinga coverage alongside Q3 FY2026 results",
        },
    ],
}

# SEGMENT_REVENUE_SPLIT - manually compiled from each company's own FILED
# income-statement revenue line items (8-K Ex-99.1 earnings-release exhibits
# and 10-Q/10-K face financials), NOT a live SEC XBRL pull - unlike
# RPO_WEB_SOURCED above, these dollar figures ARE real, filed GAAP facts
# (not press-release deal totals), just not ones this app can fetch
# automatically. Here's why: SEC's companyconcept/companyfacts JSON APIs
# (sec_edgar.py's normal data source) only return facts from a filing's
# DEFAULT, non-dimensional XBRL context. A single consolidated "Revenues"
# figure is usually tagged that way and IS fetchable - but a breakdown of
# revenue BY LINE OF BUSINESS (bitcoin mining vs. AI/HPC hosting) is tagged
# as a DIMENSIONAL fact (a ProductOrServiceAxis-style member on the same
# underlying concept), which those endpoints simply never return, confirmed
# directly against SEC's raw data for Core Scientific and TeraWulf below.
# xbrl_instance.py's raw-instance-document parser (built for CoreWeave's
# custom debt tags) can't help either - it explicitly skips any fact whose
# context carries a dimension (see its docstring); resolving dimension
# members properly would need a meaningfully bigger parser than the "find
# this custom tag's default-context value" one that exists today. So this
# is filled in by hand from each company's own earnings-release income
# statement instead, same manual-research posture as RPO_WEB_SOURCED, just
# on firmer ground since the underlying numbers are actual filed GAAP line
# items rather than a deal press release's own math.
#
# Only companies with a genuinely clean, comparable split are included here:
#   - Core Scientific: three lines - Colocation (AI/HPC hosting, mainly
#     CoreWeave), Digital asset self-mining, Digital asset hosted mining
#     (mining-as-a-service for other customers). All three are real face-of-
#     income-statement lines.
#   - TeraWulf: two lines - Digital asset (bitcoin mining) revenue and HPC
#     lease revenue (Fluidstack/Anthropic hosting). HPC lease revenue is
#     ACTUALLY live-pullable via the standard `us-gaap:OperatingLeaseLeaseIncome`
#     tag (confirmed default-context, non-dimensional) - only the mining
#     side needed manual sourcing - but both are kept here together for one
#     consistent chart rather than half-live/half-manual.
# Deliberately NOT included, and why:
#   - Cipher Mining and CleanSpark: as of the last check (Sep 2026), both
#     still report a SINGLE consolidated bitcoin-mining revenue line -
#     their new AI/HPC hosting deals (Cipher's Black Pearl lease started
#     Aug 2026, CleanSpark's Sandersville lease is even newer) hadn't yet
#     produced a reportable revenue split. Worth adding once they do.
#   - Hut 8: reports revenue by business line (Power / Digital
#     Infrastructure / Compute), but bitcoin SELF-mining doesn't appear as a
#     revenue line at all - its economics flow through a "gain/loss on
#     digital assets" line instead - so there's no clean "mining revenue vs.
#     AI revenue" split to show the way Core Scientific/TeraWulf have.
#
# Each entry: period_end (YYYY-MM-DD, fiscal quarter end), segments (dict of
# {line item label: USD value} - summed for the chart's "total"), source.
SEGMENT_REVENUE_SPLIT = {
    "Core Scientific": [
        {
            "period_end": "2025-06-30",
            "segments": {
                "AI/HPC Hosting (Colocation)": 10_600_000,
                "Bitcoin Self-Mining": 62_400_000,
                "Bitcoin Hosted Mining": 5_600_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2025-08-08",
        },
        {
            "period_end": "2025-09-30",
            "segments": {
                "AI/HPC Hosting (Colocation)": 15_000_000,
                "Bitcoin Self-Mining": 57_400_000,
                "Bitcoin Hosted Mining": 8_700_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2025-10-24",
        },
        {
            "period_end": "2025-12-31",
            "segments": {
                "AI/HPC Hosting (Colocation)": 31_300_000,
                "Bitcoin Self-Mining": 42_200_000,
                "Bitcoin Hosted Mining": 6_300_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2026-03-02",
        },
        {
            "period_end": "2026-03-31",
            "segments": {
                "AI/HPC Hosting (Colocation)": 77_500_000,
                "Bitcoin Self-Mining": 30_100_000,
                "Bitcoin Hosted Mining": 7_600_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2026-05-06",
        },
        {
            "period_end": "2026-06-30",
            "segments": {
                "AI/HPC Hosting (Colocation)": 136_700_000,
                "Bitcoin Self-Mining": 21_500_000,
                "Bitcoin Hosted Mining": 6_000_000,
            },
            "source": "8-K Ex-99.1 earnings release (Q2 2026 results)",
        },
    ],
    "TeraWulf": [
        {
            "period_end": "2025-06-30",
            "segments": {
                "AI/HPC Hosting (HPC Lease)": 0,
                "Bitcoin Mining": 47_600_000,
            },
            "source": (
                "8-K Ex-99.1 earnings release, filed 2025-08-08 - HPC/WULF Den revenue "
                "hadn't started yet this quarter (company stated it commenced in July, "
                "after quarter-end), so no separate HPC line was reported for Q2 itself."
            ),
        },
        {
            "period_end": "2025-09-30",
            "segments": {
                "AI/HPC Hosting (HPC Lease)": 7_200_000,
                "Bitcoin Mining": 43_400_000,
            },
            "source": "10-Q for period ended 2025-09-30, filed 2025-11-10",
        },
        {
            "period_end": "2025-12-31",
            "segments": {
                "AI/HPC Hosting (HPC Lease)": 9_700_000,
                "Bitcoin Mining": 26_100_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2026-02-26",
        },
        {
            "period_end": "2026-03-31",
            "segments": {
                "AI/HPC Hosting (HPC Lease)": 21_000_000,
                "Bitcoin Mining": 13_000_000,
            },
            "source": "8-K Ex-99.1 earnings release, filed 2026-05-08",
        },
        {
            "period_end": "2026-06-30",
            "segments": {
                "AI/HPC Hosting (HPC Lease)": 31_900_000,
                "Bitcoin Mining": 12_800_000,
            },
            "source": (
                "8-K Ex-99.1 earnings release (Q2 2026 results). The HPC lease figure "
                "matches SEC's own live us-gaap:OperatingLeaseLeaseIncome tag exactly "
                "($31,932,000) - the one segment line in this whole dict that's "
                "independently verifiable via a real API call rather than resting on "
                "the earnings-release transcription alone."
            ),
        },
    ],
}

# POWER_CAPACITY - manually researched from investor presentations, earnings
# releases, and earnings calls, NOT SEC XBRL (power capacity in MW isn't a
# GAAP or XBRL concept at all - it never appears in a financial statement,
# only in prose/slides). Per-sector context: professional analysts covering
# this space (e.g. JPMorgan's 2026 framework) now value these companies
# substantially on a $/MW-of-capacity basis rather than pure revenue/EBITDA
# multiples, since capacity - not revenue recognized so far - is the real
# constraint and the leading indicator of future revenue. This is a
# SNAPSHOT (as of each company's most recent disclosure as of Sep 2026), not
# a time series - these figures change with every earnings cycle and need
# periodic manual refresh, same posture as RPO_WEB_SOURCED and
# SEGMENT_REVENUE_SPLIT above.
#
# Three stages of certainty, deliberately kept separate rather than one
# blended number:
#   - current_mw: capacity that is LIVE today - operational, energized,
#     actually generating revenue (or, for CleanSpark, actually drawing
#     power for mining - see its note, this one is NOT AI capacity).
#   - contracted_future_mw: capacity under a SIGNED contract/lease that
#     isn't live yet (under construction, ramping, or awaiting energization)
#     - this is the most "real" of the future figures, but still zero
#       revenue today.
#   - pipeline_mw: capacity that's been announced, is under exclusivity, or
#     is in earlier-stage diligence/development - NOT yet a signed
#     contract. Materially less certain than contracted_future_mw; treat
#     as a directional indicator of ambition, not a commitment.
# A company that only discloses ONE blended forward-looking figure without
# a clean current/future split (Nebius) uses `single_disclosed_mw` instead
# of the three fields above - don't stack it with the others, it's a
# different kind of number (a company-stated TARGET, not a breakdown).
#
# Every field can be None where that company hasn't disclosed it cleanly -
# treat None as "unknown," never as zero. Numbers across different
# disclosures within the same company sometimes don't perfectly reconcile
# (e.g. Hut 8's "under construction" pipeline figure may partially overlap
# with its "contracted" figure, since they come from different slides/press
# releases) - flagged per-company in `note` where that's a known issue.
POWER_CAPACITY = {
    "CoreWeave": {
        "current_mw": 1500,
        "contracted_future_mw": 2200,
        "pipeline_mw": 5000,
        "as_of": "2026-08-11",
        "note": (
            "Active/operational power ~1.5GW (up ~500MW quarter-over-quarter). Total "
            "contracted power ~3.7GW includes that 1.5GW live plus signed-not-yet-live "
            "capacity (contracted_future_mw = 3.7GW - 1.5GW). pipeline_mw is management's "
            "stated AMBITION to add ~5GW more by 2030 - not a signed contract, a much "
            "softer figure than the other two."
        ),
        "source": "CoreWeave Q2 2026 Results (Aug 11, 2026); DataCenterDynamics, 'CoreWeave aims to add 5GW more by 2030'",
    },
    "Nebius": {
        "current_mw": None,
        "contracted_future_mw": None,
        "pipeline_mw": None,
        "single_disclosed_mw": 5000,
        "as_of": "2026-08-12",
        "note": (
            "Nebius discloses only ONE forward-looking 'contracted power' TARGET, not a "
            "current-vs-planned split - raised from >1GW (~mid-2025) to >3GW (Feb 2026) "
            "to >4GW (May 2026) to 5GW (Aug 2026 Q2 shareholder letter), all for "
            "end-of-2026. Company says it plans to bring >1GW/year online starting 2027, "
            "implying most of this 5GW is still pipeline rather than live today - but "
            "Nebius doesn't say how much of the 5GW is currently operational."
        ),
        "source": "Nebius Q2 2026 Shareholder Letter (Aug 12, 2026)",
    },
    "IREN": {
        "current_mw": None,
        "contracted_future_mw": 300,
        "pipeline_mw": 5000,
        "as_of": "2026-08-27",
        "note": (
            "No clean 'currently live' MW figure disclosed. contracted_future_mw is "
            "IREN's 2026 delivery target (~0.3GW IT, described as 'largely sold out'), "
            "which includes the $9.7B/200MW-IT Microsoft deal (four 50MW 'Horizon' "
            "tranches at Childress, TX - first tranche delivered Aug 2026, so some "
            "portion of this 300MW may already be live, just not broken out). A 2027 "
            "target of ~0.8GW IT exists but isn't captured in these fields. pipeline_mw "
            "is IREN's stated total global pipeline (over 5GW across Sweetwater TX, "
            "Kiowa OK, Mackenzie, Canal Flats, Prince George, Bundey Australia, Badajoz "
            "Spain) - very early-stage relative to the 2026/2027 targets."
        ),
        "source": "IREN FY26 Results (Aug 27, 2026)",
    },
    "Core Scientific": {
        "current_mw": 430,
        "contracted_future_mw": 160,
        "pipeline_mw": 2700,
        "as_of": "2026-07-28",
        "note": (
            "current_mw is capacity actively billing today (ramped from 120MW in 4Q25 -> "
            "225MW 1Q26 -> 395MW 2Q26 -> ~430MW). contracted_future_mw (160MW) is the "
            "remainder of the ~590MW total CoreWeave lease across 5 sites (Denton, "
            "Muskogee, Marble, Austin, Dalton - 'substantially complete') not yet billing. "
            "pipeline_mw (2.7GW) combines a newer AMD-relationship buildout (~530MW, "
            "5 sites, delivering 2027-2028), ~170MW of currently uncommitted capacity, and "
            "2GW+ of new-site opportunities still in diligence - all materially less "
            "certain than the CoreWeave lease. Note: CoreWeave's proposed acquisition of "
            "Core Scientific was rejected by shareholders and terminated Oct 30, 2025 - "
            "they remain separate host/customer companies, not merged."
        ),
        "source": "Core Scientific Q2 FY26 Earnings Deck (Jul 28, 2026)",
    },
    "Hut 8": {
        "current_mw": 0,
        "contracted_future_mw": 949,
        "pipeline_mw": 9990,
        "as_of": "2026-08-04",
        "note": (
            "current_mw is 0 at the Beacon Point AI campus specifically - not yet "
            "energized (initial energization targeted Q1 2027, first data hall Q3 2027). "
            "contracted_future_mw (949MW) is Hut 8's company-wide contracted IT capacity "
            "figure (~$26.6B aggregate base-term contract value), of which 704MW is "
            "Beacon Point Phase 1+2 specifically. pipeline_mw (9.99GW) combines 1,330MW "
            "'under construction' (including River Bend) and 8,660MW in earlier-stage "
            "diligence/exclusivity/development - NOTE: the 1,330MW under-construction "
            "figure may partially OVERLAP with the 949MW contracted figure rather than "
            "being fully additive (they come from different disclosure slides) - treat "
            "pipeline_mw as directional, not a precise incremental total."
        ),
        "source": "Hut 8 Q2 2026 Results (Aug 4, 2026)",
    },
    "TeraWulf": {
        "current_mw": 102,
        "contracted_future_mw": 737,
        "pipeline_mw": None,
        "as_of": "2026-08-05",
        "note": (
            "current_mw (102MW) is Lake Mariner's CB-3 building, completed and "
            "operational/revenue-generating since early July 2026. contracted_future_mw "
            "(737MW) = 336MW under construction at Lake Mariner (CB-4/CB-5, "
            "Fluidstack/Google-backed) + 401MW at the Justified Data Campus (Kentucky, "
            "20-year Anthropic lease signed Jul 6, 2026, initial delivery H2 2027, full "
            "by early 2028). No broader early-stage pipeline figure beyond these signed "
            "contracts was found."
        ),
        "source": "TeraWulf Q2 2026 Results (Aug 5, 2026); Anthropic lease press release (Jul 6, 2026)",
    },
    "Cipher Mining": {
        "current_mw": None,
        "contracted_future_mw": 244,
        "pipeline_mw": 256,
        "as_of": "2025-11-20",
        "note": (
            "No clean 'currently live' figure - the Barber Lake site (168MW + 56MW "
            "Fluidstack/Google-backstopped deals) was targeted operational for its base "
            "build around September 2026, right around when this data was last "
            "refreshed, so 'live' vs. 'about to go live' is genuinely ambiguous as of "
            "this snapshot. contracted_future_mw (244MW) is the site's current gross "
            "contracted capacity; 39MW of the Nov 2025 deal's critical IT load isn't "
            "expected until January 2027. pipeline_mw (256MW) is the site's disclosed "
            "expansion headroom (up to 500MW total potential across 587 acres) - not yet "
            "under contract."
        ),
        "source": "Cipher Mining press release, 'Cipher Mining Signs Additional 56 MW...' (Nov 20, 2025)",
    },
    "CleanSpark": {
        "current_mw": 808,
        "contracted_future_mw": 992,
        "pipeline_mw": 750,
        "as_of": "2026-08",
        "note": (
            "IMPORTANT: current_mw (808MW) is concurrent power CleanSpark is drawing to "
            "run its bitcoin mining fleet (50 EH/s peak hashrate, Aug 2026 operational "
            "update) - this is virtually all MINING power, not AI. contracted_future_mw "
            "(992MW = 1.8GW company-wide contracted total minus the 808MW currently "
            "utilized) includes the 175MW Sandersville, GA AI/HPC lease (20-year, $6.6B, "
            "signed Aug 6, 2026, delivery targeted Q4 2027) plus presumably further "
            "mining-infrastructure buildout not yet broken out separately - so most of "
            "this 992MW is still NOT the AI business. pipeline_mw (~750MW) is a Texas "
            "pipeline (Sealy ~300MW, Brazoria 300-600MW, midpoint used) under exclusivity "
            "but not yet contracted."
        ),
        "source": (
            "CleanSpark Sandersville lease press release (Aug 6, 2026); "
            "CleanSpark August 2026 Operational Update"
        ),
    },
}

# NOTE on operating_cash_flow / capex specifically: unlike revenue or the
# balance-sheet concepts, SEC filers report CASH FLOW STATEMENT items as
# YEAR-TO-DATE CUMULATIVE totals (e.g. "six months ended June 30"), not
# discrete quarters - CoreWeave's Q2 filing reports Jan 1 - Jun 30, not Apr 1
# - Jun 30. data_processing.concept_to_quarterly_df (built for revenue/
# income-statement items, which ARE discrete-quarter) would silently drop
# almost all of this data, since it filters to ~91-day periods only. Use
# data_processing.concept_to_ytd_cumulative_df + cumulative_ytd_to_discrete_
# quarterly instead for these two tags, which reconstructs each discrete
# quarter by subtracting consecutive YTD figures within a fiscal year. This
# also assumes a calendar-year fiscal year (Jan 1 start) - true for
# CoreWeave, but check before reusing for another company.

# NOTE on "Forward EBITDA": there is no free source of real analyst-
# consensus forward EBITDA. SEC filings are historical/filed-only by
# definition, and yfinance's EBITDA-related info fields (`ebitda`,
# `enterpriseToEbitda`) are TRAILING-twelve-months, not forward - confirmed
# against yfinance's own docs, there is no `forwardEbitda` field the way
# there's a `forwardPE`/`forwardEps`. yfinance DOES expose real consensus
# analyst estimates (`Ticker.revenue_estimate`, indexed by forward period:
# current quarter, next quarter, current FY, next FY) but only for revenue
# and EPS, never an EBITDA line directly. Real consensus forward EBITDA
# generally requires a paid provider (Bloomberg/FactSet/Capital IQ).
#
# So "Forward EBITDA" here is an explicit PROXY, not real analyst EBITDA
# guidance, computed as:
#
#     Forward EBITDA = (next-fiscal-year consensus revenue estimate, from
#                        yfinance analysts) x (trailing EBITDA margin)
#
# where trailing EBITDA margin = trailing EBITDA / trailing revenue, each
# summed over the most recent up-to-4 discrete quarters SEC has (operating
# income + D&A as the EBITDA proxy - see depreciation_amortization above -
# falling back to yfinance's trailing `info['ebitda']`/`info['totalRevenue']`
# only when SEC has neither figure at all, e.g. Nebius). This assumes the
# trailing margin holds into the forward year, which real analyst EBITDA
# estimates would NOT assume uncritically (a scaling AI-cloud buildout can
# swing margins quarter to quarter) - it's a reasonable back-of-envelope
# figure, not a substitute for real sell-side EBITDA estimates. Labeled as
# such everywhere it's shown in the app.

# NOTE on "Forward EV/Sales" and "Forward P/S": unlike Forward EBITDA, these
# are NOT a made-up proxy - they're standard valuation multiples built from
# real inputs the app already has:
#
#     Forward P/S       = market cap (live, Yahoo Finance) / forward revenue
#                          (next-fiscal-year consensus estimate, Yahoo Finance)
#     Enterprise Value   = market cap + total debt (SEC, latest quarter) -
#                          cash & equivalents (SEC, latest quarter)
#     Forward EV/Sales   = enterprise value / forward revenue
#
# The only real caveat (same as Market Cap vs. RPO's) is that market cap and
# total debt/cash are "as of" different dates by construction - market cap
# is live/daily, debt and cash are only as fresh as the most recently filed
# quarter - so EV is a mix of a live number and a lagged one, same as RPO's
# multiple. Flagged in the caption rather than hidden. Forward revenue is
# the same next-FY consensus estimate used for Forward EBITDA (see above) -
# reused rather than fetched twice.

# Debt broken into the categories used for the stacked "Total Debt" chart.
#
# Each segment has a "primary" (taxonomy, tag) - a standard us-gaap concept -
# and an optional list of "components": (taxonomy, tag) pairs that get SUMMED
# together and used as a fallback for any balance-sheet date where the
# primary tag has no value.
#
# Why this exists: CoreWeave reclassified its balance sheet starting with its
# Q2 2026 10-Q, splitting what had been a single "Long-term debt" line into
# "Recourse debt" and "Non-recourse debt" (each still current/non-current).
# The old LongTermDebtCurrent/LongTermDebtNoncurrent us-gaap tags simply stop
# being reported from 2026-06-30 onward - so a naive "try tags until one has
# any data" approach (what this used to do) picks the old tag, since it DOES
# have history, and then silently shows $0 debt for every quarter after the
# switch, since that tag has no value for those dates. The primary/components
# fallback fixes that: for 2026-06-30 onward it reconstructs the equivalent
# total by summing crwv:RecourseDebtCurrent + crwv:NonRecourseDebtCurrent
# (and the non-current pair), while older quarters keep using the original
# single tag.
#
# IMPORTANT - the "crwv:" tags are CoreWeave-specific custom XBRL extensions,
# not standard us-gaap. They: (a) only exist for CoreWeave, so adding another
# company here means checking whether ITS filings have a similar split under
# its own custom prefix, and (b) aren't retrievable through SEC's JSON APIs at
# all - not companyconcept (404s), and NOT companyfacts either (it only ever
# contains standard us-gaap/dei/srt facts, never a filer's custom extensions).
# So sec_edgar.py fetches these by downloading the company's own recent
# 10-Q/10-K XBRL instance documents and parsing them directly for the tag
# (see fetch_custom_tag / xbrl_instance.py) - there's no JSON-endpoint
# shortcut for a custom tag, full stop.
# In short: any company that changes its own balance-sheet presentation
# will eventually need a similar one-off fix here; there's no way to make
# this fully automatic across arbitrary filers.
#
# NOTE on scope generally: this is the current/non-current split for long-term
# debt and finance leases. It does NOT break debt down by individual
# instrument (e.g. "Term Loan A" vs "Revolving Credit Facility" vs "DDTL"),
# because that level of detail lives in dimensional tags inside the debt
# footnote, not as a standalone concept - pulling it would mean parsing the
# full XBRL instance document rather than hitting a simple JSON endpoint.
DEBT_SEGMENTS = {
    "Long-term debt (current)": {
        "primary": ("us-gaap", "LongTermDebtCurrent"),
        "components": [("crwv", "RecourseDebtCurrent"), ("crwv", "NonRecourseDebtCurrent")],
    },
    "Long-term debt (non-current)": {
        "primary": ("us-gaap", "LongTermDebtNoncurrent"),
        "components": [("crwv", "RecourseDebtNonCurrent"), ("crwv", "NonRecourseDebtNonCurrent")],
    },
    "Finance lease liabilities (current)": {
        "primary": ("us-gaap", "FinanceLeaseLiabilityCurrent"),
        "components": [],
    },
    "Finance lease liabilities (non-current)": {
        "primary": ("us-gaap", "FinanceLeaseLiabilityNoncurrent"),
        "components": [],
    },
}
