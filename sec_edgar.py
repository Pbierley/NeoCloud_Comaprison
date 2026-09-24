"""
Thin client for the SEC EDGAR XBRL "company concept" / "company facts" APIs.

No API key needed, but the SEC requires a descriptive User-Agent
(see config.SEC_USER_AGENT) and asks that you not hammer the API -
we keep requests to a modest rate and cache results.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests

from config import SEC_USER_AGENT, REVENUE_TAGS, OTHER_TAGS, DEBT_SEGMENTS, UNITS_KEY_OVERRIDES
from xbrl_instance import extract_facts_by_local_name

BASE = "https://data.sec.gov"
EDGAR = "https://www.sec.gov/Archives/edgar/data"
_HEADERS = {"User-Agent": SEC_USER_AGENT}

# How many of a company's most recent 10-Q/10-K filings to check for a custom
# extension tag. Each check downloads and parses that filing's full XBRL
# instance document, which is slower than the JSON APIs - kept small since we
# mainly need the current + prior comparative period a tag was introduced in.
_MAX_CUSTOM_TAG_FILINGS = 4

# SEC asks for no more than ~10 requests/second; we stay well under that.
_MIN_INTERVAL = 0.2
_last_call = 0.0


def _throttle():
    global _last_call
    elapsed = time.time() - _last_call
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_call = time.time()


def _get(url: str) -> Optional[dict]:
    _throttle()
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _get_bytes(url: str) -> Optional[bytes]:
    _throttle()
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def _pad_cik(cik: str) -> str:
    return str(cik).zfill(10)


def lookup_cik_by_ticker(ticker: str) -> Optional[str]:
    """Look up a company's CIK from its stock ticker via SEC's ticker map."""
    data = _get("https://www.sec.gov/files/company_tickers.json")
    if not data:
        return None
    ticker = ticker.upper()
    for entry in data.values():
        if entry.get("ticker") == ticker:
            return str(entry["cik_str"])
    return None


def get_company_concept(cik: str, tag: str, taxonomy: str = "us-gaap") -> Optional[dict]:
    """Fetch a single XBRL concept (e.g. 'Revenues') for a company."""
    url = f"{BASE}/api/xbrl/companyconcept/CIK{_pad_cik(cik)}/{taxonomy}/{tag}.json"
    return _get(url)


def get_company_facts(cik: str) -> Optional[dict]:
    """Fetch ALL XBRL facts SEC has for a company (larger payload)."""
    url = f"{BASE}/api/xbrl/companyfacts/CIK{_pad_cik(cik)}.json"
    return _get(url)


def get_submissions(cik: str) -> Optional[dict]:
    """Fetch a company's filing history/metadata (forms, accession numbers,
    primary document filenames, filing dates)."""
    url = f"{BASE}/submissions/CIK{_pad_cik(cik)}.json"
    return _get(url)


def get_recent_filings(cik: str, forms=("10-Q", "10-K"), limit: int = _MAX_CUSTOM_TAG_FILINGS) -> list[dict]:
    """
    Most recent filings of the given form types, newest first, as
    [{"accessionNumber", "primaryDocument", "form", "filingDate"}].
    Used to find the instance document(s) to parse for a custom tag - see
    fetch_custom_tag below for why we can't just use the JSON APIs for those.
    """
    subs = get_submissions(cik)
    if not subs:
        return []
    recent = subs.get("filings", {}).get("recent", {})
    rows = list(
        zip(
            recent.get("form", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
            recent.get("filingDate", []),
        )
    )
    matches = [
        {"form": form, "accessionNumber": acc, "primaryDocument": doc, "filingDate": filed}
        for form, acc, doc, filed in rows
        if form in forms
    ]
    return matches[:limit]


def _instance_doc_url(cik: str, accession_number: str, primary_document: str) -> str:
    acc_no_dashes = accession_number.replace("-", "")
    # SEC's inline-XBRL convention: the machine-readable instance document is
    # the primary .htm filename with "_htm.xml" in place of ".htm".
    instance_name = primary_document.replace(".htm", "_htm.xml")
    return f"{EDGAR}/{int(cik)}/{acc_no_dashes}/{instance_name}"


def fetch_custom_tag(
    cik: str, tag: str, filings_cache: Optional[dict] = None, max_filings: int = _MAX_CUSTOM_TAG_FILINGS
) -> Optional[dict]:
    """
    Fetch a filer-specific custom XBRL tag (e.g. CoreWeave's
    "RecourseDebtCurrent") by downloading and parsing its own recent 10-Q/10-K
    instance documents directly.

    This exists because SEC's companyconcept and companyfacts JSON APIs only
    cover the standard us-gaap/dei/srt taxonomies - a custom extension tag
    404s on companyconcept and simply never appears in companyfacts, so there
    is no JSON-endpoint shortcut for it (see xbrl_instance.py's docstring).

    Checks up to `max_filings` of the company's most recent 10-Q/10-K filings
    (newest first) for the tag, merging what each one reports. If the same
    balance-sheet date appears in more than one filing (e.g. as a comparative
    period), the value from the most-recently-filed one wins - same
    "trust the latest filing" rule used everywhere else in this app.

    filings_cache: optional dict the caller can pass in so multiple custom
    tags for the same company reuse one get_recent_filings() call and one
    downloaded+parsed instance document per filing, instead of re-fetching
    per tag.
    """
    if filings_cache is not None and cik in filings_cache:
        filings = filings_cache[cik]
    else:
        filings = get_recent_filings(cik, limit=max_filings)
        if filings_cache is not None:
            filings_cache[cik] = filings

    if not filings:
        return None

    # Oldest first, so later (newer) filings' values overwrite earlier ones
    # for any date they both cover - implementing "latest filing wins".
    by_date: dict[str, dict] = {}
    for filing in reversed(filings):
        url = _instance_doc_url(cik, filing["accessionNumber"], filing["primaryDocument"])
        xml_bytes = _get_bytes(url)
        if not xml_bytes:
            continue
        try:
            extracted = extract_facts_by_local_name(xml_bytes, [tag])
        except ET.ParseError:
            continue
        for fact in extracted.get(tag, []):
            by_date[fact["end"]] = {
                "end": fact["end"],
                "start": fact.get("start"),
                "value": fact["value"],
                "form": filing["form"],
                "filed": filing["filingDate"],
            }

    if not by_date:
        return None

    # Each fact already carries its own per-filing form/filed date (unlike a
    # single companyconcept response, which shares one shape for everything),
    # so build the units.USD list directly rather than via a helper that
    # assumes one uniform form/filed for the whole batch.
    usd_entries = [
        {
            "end": f["end"],
            "start": f["start"],
            "val": f["value"],
            "form": f["form"],
            "filed": f["filed"],
            "fy": None,
            "fp": None,
        }
        for f in sorted(by_date.values(), key=lambda f: f["end"])
    ]
    return {"cik": cik, "taxonomy": "custom", "tag": tag, "units": {"USD": usd_entries}}


def fetch_tagged_concept(
    cik: str, taxonomy: str, tag: str, facts_cache: Optional[dict] = None
) -> Optional[dict]:
    """
    Fetch one (taxonomy, tag) concept, e.g. ("us-gaap", "Assets") or
    ("crwv", "RecourseDebtCurrent"). Standard taxonomies (us-gaap, dei, srt)
    are served directly by SEC's lightweight companyconcept endpoint.
    Anything else is treated as a filer-specific custom extension tag and
    fetched by parsing the company's own recent filings directly (see
    fetch_custom_tag) - companyconcept/companyfacts don't carry these at all.

    facts_cache is reused here as the "shared state across calls for this
    company" cache (originally just for companyfacts, now doubling as the
    get_recent_filings cache for fetch_custom_tag) so multiple custom tags
    for one company only hit the filing list / instance documents once.
    """
    if taxonomy in ("us-gaap", "dei", "srt"):
        data = get_company_concept(cik, tag, taxonomy=taxonomy)
        if data and data.get("units", {}).get("USD"):
            return data
        return None

    return fetch_custom_tag(cik, tag, filings_cache=facts_cache)


def fetch_revenue_concept(cik: str) -> tuple[Optional[dict], Optional[str]]:
    """
    Try each known revenue tag until one returns data.
    Returns (raw_concept_json, tag_used) - (None, None) if nothing hit.
    """
    return fetch_first_matching_concept(cik, REVENUE_TAGS)


def fetch_first_matching_concept(
    cik: str, tags: list[str], units_key: str = "USD"
) -> tuple[Optional[dict], Optional[str]]:
    """
    Try each tag in order until one returns data. Used for revenue and for
    the OTHER_TAGS concepts (assets, cash, debt, shares outstanding, etc.)
    in config.py.

    units_key: which bucket of the concept's "units" object counts as "has
    data" - "USD" for dollar figures (the default), "shares" for a
    share-count concept. SEC nests values by the filer's declared unit type,
    so checking the wrong key here means a tag that genuinely has data still
    gets treated as empty and the next fallback tag gets tried instead.
    """
    for tag in tags:
        data = get_company_concept(cik, tag)
        if data and data.get("units", {}).get(units_key):
            return data, tag
    return None, None


def fetch_concept_by_name(cik: str, concept_name: str) -> tuple[Optional[dict], Optional[str]]:
    """Fetch one of the named concepts from config.OTHER_TAGS (e.g. 'total_assets').
    Looks up the right units bucket from config.UNITS_KEY_OVERRIDES (defaults
    to "USD" for anything not listed there, e.g. share-count concepts)."""
    tags = OTHER_TAGS.get(concept_name)
    if not tags:
        raise KeyError(f"Unknown concept '{concept_name}' - add it to OTHER_TAGS in config.py")
    units_key = UNITS_KEY_OVERRIDES.get(concept_name, "USD")
    return fetch_first_matching_concept(cik, tags, units_key=units_key)


def fetch_debt_segments(cik: str) -> dict[str, dict]:
    """
    Fetch every debt category in config.DEBT_SEGMENTS for a company.

    Returns, per segment label:
        {
            "primary": (concept_json_or_None, "taxonomy:tag"_or_None),
            "components": [(concept_json_or_None, "taxonomy:tag"), ...],
        }
    data_processing.build_debt_segment_df turns this into a single
    per-date series: primary value where the company reported one, else the
    sum of whichever components it reported for that date (see the long
    comment on config.DEBT_SEGMENTS for why - CoreWeave started reporting
    "recourse"/"non-recourse" debt instead of undifferentiated long-term debt
    partway through its filing history, and the primary tag has no value at
    all for dates after that switch).

    A segment/component with genuinely no data anywhere (e.g. a company that
    never had finance leases) comes back as (None, ...) - callers should
    treat that as "zero/not applicable", not an error.
    """
    facts_cache: dict = {}
    result = {}
    for label, seg in DEBT_SEGMENTS.items():
        p_taxonomy, p_tag = seg["primary"]
        primary_json = fetch_tagged_concept(cik, p_taxonomy, p_tag, facts_cache)
        primary_tag_str = f"{p_taxonomy}:{p_tag}" if primary_json else None

        components = []
        for c_taxonomy, c_tag in seg.get("components", []):
            c_json = fetch_tagged_concept(cik, c_taxonomy, c_tag, facts_cache)
            components.append((c_json, f"{c_taxonomy}:{c_tag}"))

        result[label] = {"primary": (primary_json, primary_tag_str), "components": components}
    return result
