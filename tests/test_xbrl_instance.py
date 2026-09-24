"""
Offline test for xbrl_instance.py against a small hand-built XBRL instance
document (tests/fixture_instance_snippet.xml) shaped like a real filing's:
a plain instant context, a comparative-period instant context, and a
dimensional (segment) context for the same tag/date that must be excluded.

Run: python tests/test_xbrl_instance.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from xbrl_instance import extract_facts_by_local_name

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_instance_snippet.xml")


def test_extract_facts():
    with open(FIXTURE, "rb") as f:
        xml_bytes = f.read()

    results = extract_facts_by_local_name(
        xml_bytes, ["RecourseDebtCurrent", "NonRecourseDebtCurrent"]
    )

    assert set(results.keys()) == {"RecourseDebtCurrent", "NonRecourseDebtCurrent"}

    recourse = {f["end"]: f["value"] for f in results["RecourseDebtCurrent"]}
    # Only the two non-dimensional contexts should show up - the dimensional
    # c-3-dimensional fact (4,000,000,000) must be excluded even though it's
    # the same tag and date.
    assert recourse == {"2026-06-30": 6235000000.0, "2025-12-31": 6118000000.0}, recourse

    nonrecourse = {f["end"]: f["value"] for f in results["NonRecourseDebtCurrent"]}
    assert nonrecourse == {"2026-06-30": 1278000000.0, "2025-12-31": 590000000.0}, nonrecourse

    print("XBRL instance extraction checks passed.")
    print("RecourseDebtCurrent:", recourse)
    print("NonRecourseDebtCurrent:", nonrecourse)


def test_ignores_untagged_names():
    with open(FIXTURE, "rb") as f:
        xml_bytes = f.read()
    # Asking for a tag that isn't in the file should just come back empty,
    # not error - and shouldn't accidentally pick up us-gaap:Assets or
    # anything else not explicitly requested.
    results = extract_facts_by_local_name(xml_bytes, ["Assets", "SomeTagThatDoesNotExist"])
    assert results["SomeTagThatDoesNotExist"] == []
    # We DID ask for "Assets" here, so it's fine that it's found - this just
    # confirms the function is namespace-agnostic and picks up us-gaap facts too.
    assert results["Assets"][0]["value"] == 55570000000.0
    print("Untagged-name and cross-namespace checks passed.")


if __name__ == "__main__":
    test_extract_facts()
    test_ignores_untagged_names()
