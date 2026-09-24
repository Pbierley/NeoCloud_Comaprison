"""
Minimal parser for a raw XBRL instance document (the "_htm.xml" file SEC
publishes alongside every inline-XBRL filing).

Why this exists: SEC's companyconcept and companyfacts JSON APIs
(sec_edgar.py's main data source) only serve facts tagged under the
standard us-gaap / dei / srt taxonomies. A filer's own custom extension
tags - like CoreWeave's crwv:RecourseDebtCurrent, introduced when it
restructured its balance sheet in its Q2 2026 10-Q - are NOT retrievable
through those JSON endpoints at all (confirmed: SEC returns a 404 for a
custom-taxonomy companyconcept request, and companyfacts only ever
contains standard-taxonomy facts). The only way to get a custom tag's
value is to download and parse the filing's own XBRL instance document
directly, which is what this module does.

This is a deliberately minimal instance-document parser - just enough to
answer "what value did this filing report for tag X, as of which date(s)",
matched purely by local element name (ignoring the custom namespace URI,
which SEC/filers version per-filing, e.g. http://coreweave.com/20260630 -
one different URI every quarter - so matching on it would break every
quarter). It is NOT a general XBRL processor: it doesn't resolve
dimensional (segment/member) breakdowns, doesn't handle calculation or
presentation linkbases, and skips any fact whose context has a
<segment>/<scenario> dimension, since those are sub-breakdowns of a
concept rather than the top-level balance we want here.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional


def _local_name(tag: str) -> str:
    """Strip a namespace URI off an ElementTree tag, e.g.
    '{http://coreweave.com/20260630}RecourseDebtCurrent' -> 'RecourseDebtCurrent'."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_contexts(root: ET.Element) -> dict[str, dict]:
    """
    Build {context_id: {"instant": str|None, "start": str|None, "end": str|None,
    "has_dimension": bool}} from every <context> element in the instance doc.
    """
    contexts: dict[str, dict] = {}
    for ctx in root.iter():
        if _local_name(ctx.tag) != "context":
            continue
        ctx_id = ctx.get("id")
        if not ctx_id:
            continue
        instant = start = end = None
        has_dimension = False
        for child in ctx:
            name = _local_name(child.tag)
            if name == "period":
                for p in child:
                    pname = _local_name(p.tag)
                    if pname == "instant":
                        instant = p.text
                    elif pname == "startDate":
                        start = p.text
                    elif pname == "endDate":
                        end = p.text
            elif name == "entity":
                for e in child:
                    if _local_name(e.tag) == "segment":
                        # Any segment content means this context represents a
                        # dimensional breakdown (e.g. by debt instrument), not
                        # the top-level reported total - skip those.
                        has_dimension = True
        contexts[ctx_id] = {
            "instant": instant,
            "start": start,
            "end": end,
            "has_dimension": has_dimension,
        }
    return contexts


def extract_facts_by_local_name(
    xml_bytes: bytes, local_names: list[str]
) -> dict[str, list[dict]]:
    """
    Parse an XBRL instance document and pull out every non-dimensional fact
    for the given element local names (namespace-agnostic - see module
    docstring for why).

    Returns {local_name: [{"end": "YYYY-MM-DD", "start": str|None, "value": float}, ...]}
    - only for instant-context facts by default (start is carried through in
    case a duration fact is ever requested, but callers here want balance-
    sheet instants).
    """
    root = ET.fromstring(xml_bytes)
    contexts = _parse_contexts(root)
    wanted = set(local_names)
    results: dict[str, list[dict]] = {name: [] for name in local_names}

    for el in root.iter():
        name = _local_name(el.tag)
        if name not in wanted:
            continue
        ctx_id = el.get("contextRef")
        ctx = contexts.get(ctx_id)
        if not ctx or ctx["has_dimension"]:
            continue
        text = (el.text or "").strip()
        if not text:
            continue
        try:
            value = float(text.replace(",", ""))
        except ValueError:
            continue
        if el.get("sign") == "-":
            value = -value

        end = ctx["instant"] or ctx["end"]
        if not end:
            continue
        results[name].append({"end": end, "start": ctx["start"], "value": value})

    return results


def facts_to_concept_json(
    facts: list[dict], cik: str, taxonomy: str, tag: str, form: str, filed: str
) -> Optional[dict]:
    """
    Reshape extract_facts_by_local_name's output for one tag into the same
    {"units": {"USD": [{"end", "val", "form", "filed", ...}]}} shape that
    sec_edgar's companyconcept responses use, so it can flow through the
    exact same data_processing parsing functions either way.
    """
    if not facts:
        return None
    usd_entries = [
        {
            "end": f["end"],
            "start": f.get("start"),
            "val": f["value"],
            "form": form,
            "filed": filed,
            "fy": None,
            "fp": None,
        }
        for f in facts
    ]
    return {"cik": cik, "taxonomy": taxonomy, "tag": tag, "units": {"USD": usd_entries}}
