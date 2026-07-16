from __future__ import annotations

from agent_doc_bench.doc_requirements import build_doc_requirements


def test_includes_known_pattern_labels() -> None:
    text = build_doc_requirements("blpapi")

    assert "blpapi_open_session" in text
    assert "instantiates blpapi.Session" in text
    assert "uses credential-based auth (wrong method)" in text


def test_includes_rubric_prose() -> None:
    text = build_doc_requirements("blpapi")

    assert "product_selection" in text


def test_reads_as_guidance_not_a_spec() -> None:
    text = build_doc_requirements("blpapi")

    assert "not a template to follow" in text
