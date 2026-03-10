"""Unit tests for PDF compliance report generation.

WeasyPrint requires system libraries (pango, cairo) that may not be available
in all environments. Tests verify HTML report generation (always available)
and PDF generation (skipped when WeasyPrint can't load).
"""

import pytest


_SAMPLE_SUMMARY = {
    "total_requests": 100,
    "allowed": 90,
    "denied": 10,
    "models_used": ["qwen3:4b", "gpt-4o"],
}

_SAMPLE_ATTESTATIONS = [
    {"model_id": "qwen3:4b", "provider": "ollama", "attestation_id": "self-attested:qwen3:4b",
     "request_count": 90, "total_tokens": 13500},
    {"model_id": "gpt-4o", "provider": "openai", "attestation_id": "self-attested:gpt-4o",
     "request_count": 10, "total_tokens": 2000},
]

_SAMPLE_EXECUTIONS = [
    {"execution_id": "exec-1", "model_id": "qwen3:4b", "policy_result": "pass",
     "timestamp": "2026-03-05T10:00:00+00:00", "latency_ms": 200,
     "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
]

_SAMPLE_CHAIN_INTEGRITY = {
    "sessions_verified": 3,
    "all_valid": True,
    "sessions": [
        {"session_id": "sess-1", "valid": True, "record_count": 5, "errors": []},
    ],
}

_KWARGS = dict(
    summary=_SAMPLE_SUMMARY,
    attestations=_SAMPLE_ATTESTATIONS,
    executions=_SAMPLE_EXECUTIONS,
    chain_integrity=_SAMPLE_CHAIN_INTEGRITY,
    framework="eu_ai_act",
    start="2026-03-01",
    end="2026-03-10",
)


def test_html_report_contains_expected_sections():
    """HTML report includes executive summary, attestation table, chain verification."""
    from gateway.compliance.pdf_report import render_report_html

    html = render_report_html(**_KWARGS)
    assert "Compliance Report" in html
    assert "Executive Summary" in html
    assert "qwen3:4b" in html
    assert "2026-03-01" in html
    assert "2026-03-10" in html


def test_html_report_with_empty_data():
    """Empty data produces valid HTML — no crash."""
    from gateway.compliance.pdf_report import render_report_html

    html = render_report_html(
        summary={"total_requests": 0, "allowed": 0, "denied": 0, "models_used": []},
        attestations=[],
        executions=[],
        chain_integrity={"sessions_verified": 0, "all_valid": True, "sessions": []},
        framework="nist",
        start="2026-01-01",
        end="2026-01-02",
    )
    assert "Compliance Report" in html
    assert "0" in html  # total_requests


def test_pdf_generation_produces_valid_pdf():
    """PDF output starts with %PDF- (requires WeasyPrint + system libs)."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint not available (missing system libraries)")

    from gateway.compliance.pdf_report import generate_pdf_report

    pdf_bytes = generate_pdf_report(**_KWARGS)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes[:5] == b"%PDF-"
    assert len(pdf_bytes) > 100


def test_pdf_with_empty_data_no_crash():
    """Zero records produces a valid PDF — no crash."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint not available (missing system libraries)")

    from gateway.compliance.pdf_report import generate_pdf_report

    pdf_bytes = generate_pdf_report(
        summary={"total_requests": 0, "allowed": 0, "denied": 0, "models_used": []},
        attestations=[],
        executions=[],
        chain_integrity={"sessions_verified": 0, "all_valid": True, "sessions": []},
        framework="nist",
        start="2026-01-01",
        end="2026-01-02",
    )
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes[:5] == b"%PDF-"
