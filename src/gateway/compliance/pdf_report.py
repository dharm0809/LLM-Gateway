"""PDF compliance report generation via Jinja2 HTML + WeasyPrint."""

from __future__ import annotations

from datetime import datetime, timezone

from jinja2 import Template

from gateway.compliance.frameworks import get_framework_mapping

_REPORT_TEMPLATE = Template("""\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Walacor Gateway — Compliance Report</title>
<style>
  body { font-family: 'Helvetica Neue', Arial, sans-serif; margin: 40px; color: #1a1a2e; font-size: 11px; }
  h1 { color: #0f3460; border-bottom: 3px solid #e94560; padding-bottom: 10px; font-size: 22px; }
  h2 { color: #16213e; margin-top: 30px; font-size: 16px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }
  h3 { color: #0f3460; font-size: 13px; }
  .meta { color: #666; font-size: 10px; margin-bottom: 20px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 10px; }
  th { background: #0f3460; color: white; padding: 6px 8px; text-align: left; }
  td { border: 1px solid #ddd; padding: 5px 8px; }
  tr:nth-child(even) { background: #f8f9fa; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 9px; font-weight: bold; }
  .compliant { background: #d4edda; color: #155724; }
  .partial { background: #fff3cd; color: #856404; }
  .non_compliant { background: #f8d7da; color: #721c24; }
  .stat-grid { display: flex; gap: 20px; margin: 15px 0; }
  .stat-card { background: #f8f9fa; border: 1px solid #ddd; border-radius: 8px; padding: 15px; flex: 1; text-align: center; }
  .stat-value { font-size: 24px; font-weight: bold; color: #0f3460; }
  .stat-label { font-size: 10px; color: #666; margin-top: 5px; }
  .footer { margin-top: 40px; padding-top: 10px; border-top: 1px solid #ddd; color: #999; font-size: 9px; }
  @page { margin: 2cm; @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9px; color: #999; } }
</style>
</head>
<body>

<h1>Walacor Gateway — Compliance Report</h1>
<div class="meta">
  <strong>Period:</strong> {{ start }} to {{ end }} &nbsp;|&nbsp;
  <strong>Framework:</strong> {{ framework_name }} &nbsp;|&nbsp;
  <strong>Generated:</strong> {{ generated_at }}
</div>

<h2>Executive Summary</h2>
<div class="stat-grid">
  <div class="stat-card">
    <div class="stat-value">{{ summary.total_requests }}</div>
    <div class="stat-label">Total Requests</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{{ summary.allowed }}</div>
    <div class="stat-label">Allowed</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{{ summary.denied }}</div>
    <div class="stat-label">Denied</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{{ chain_integrity.sessions_verified }}</div>
    <div class="stat-label">Sessions Verified</div>
  </div>
</div>

{% if summary.models_used %}
<p><strong>Models Used:</strong> {{ summary.models_used | join(', ') }}</p>
{% endif %}

<p><strong>Chain Integrity:</strong>
  {% if chain_integrity.all_valid %}
    <span class="badge compliant">ALL VALID</span>
  {% else %}
    <span class="badge non_compliant">INTEGRITY ISSUES</span>
  {% endif %}
</p>

<h2>Model Attestation Inventory</h2>
{% if attestations %}
<table>
  <tr><th>Model</th><th>Provider</th><th>Attestation ID</th><th>Requests</th><th>Total Tokens</th></tr>
  {% for att in attestations %}
  <tr>
    <td>{{ att.model_id }}</td>
    <td>{{ att.provider }}</td>
    <td style="font-size:9px;">{{ att.attestation_id }}</td>
    <td>{{ att.request_count }}</td>
    <td>{{ att.total_tokens }}</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p><em>No attestations in this period.</em></p>
{% endif %}

<h2>Chain Verification Results</h2>
{% if chain_integrity.sessions %}
<table>
  <tr><th>Session ID</th><th>Records</th><th>Status</th><th>Errors</th></tr>
  {% for s in chain_integrity.sessions %}
  <tr>
    <td style="font-size:9px;">{{ s.session_id }}</td>
    <td>{{ s.record_count }}</td>
    <td>
      {% if s.valid %}<span class="badge compliant">VALID</span>
      {% else %}<span class="badge non_compliant">INVALID</span>{% endif %}
    </td>
    <td>{{ s.errors | length }}</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p><em>No sessions in this period.</em></p>
{% endif %}

{% if framework_mapping %}
<h2>{{ framework_name }} Compliance Mapping</h2>
{% if framework_mapping.articles %}
  {% for key, article in framework_mapping.articles.items() %}
  <h3>{{ article.title }} <span class="badge {{ article.status }}">{{ article.status | upper }}</span></h3>
  <table>
    <tr><th>ID</th><th>Requirement</th><th>Status</th><th>Evidence</th></tr>
    {% for req in article.requirements %}
    <tr>
      <td>{{ req.id }}</td>
      <td>{{ req.description }}</td>
      <td><span class="badge {{ req.status }}">{{ req.status }}</span></td>
      <td>{{ req.evidence_ref }}</td>
    </tr>
    {% endfor %}
  </table>
  {% endfor %}
{% elif framework_mapping.functions %}
  {% for key, func in framework_mapping.functions.items() %}
  <h3>{{ func.title }} <span class="badge {{ func.status }}">{{ func.status | upper }}</span></h3>
  <p>{{ func.description }}</p>
  {% endfor %}
{% elif framework_mapping.criteria %}
  {% for key, crit in framework_mapping.criteria.items() %}
  <h3>{{ key }}: {{ crit.title }} <span class="badge {{ crit.status }}">{{ crit.status | upper }}</span></h3>
  <p>{{ crit.description }}</p>
  {% endfor %}
{% elif framework_mapping.clauses %}
  {% for key, clause in framework_mapping.clauses.items() %}
  <h3>Clause {{ key }}: {{ clause.title }} <span class="badge {{ clause.status }}">{{ clause.status | upper }}</span></h3>
  {% endfor %}
{% endif %}
{% endif %}

<h2>Sample Execution Records</h2>
{% if executions[:10] %}
<table>
  <tr><th>Execution ID</th><th>Timestamp</th><th>Model</th><th>Policy</th><th>Latency (ms)</th><th>Tokens</th></tr>
  {% for ex in executions[:10] %}
  <tr>
    <td style="font-size:9px;">{{ ex.execution_id }}</td>
    <td>{{ ex.timestamp }}</td>
    <td>{{ ex.model_id }}</td>
    <td>{{ ex.policy_result }}</td>
    <td>{{ ex.latency_ms }}</td>
    <td>{{ ex.total_tokens }}</td>
  </tr>
  {% endfor %}
</table>
{% if executions | length > 10 %}
<p><em>Showing 10 of {{ executions | length }} records. Full data available via JSON/CSV export.</em></p>
{% endif %}
{% else %}
<p><em>No execution records in this period.</em></p>
{% endif %}

<div class="footer">
  Generated by Walacor AI Security Gateway &mdash; {{ generated_at }}
</div>

</body>
</html>
""")

_FRAMEWORK_NAMES = {
    "eu_ai_act": "EU AI Act",
    "nist": "NIST AI RMF",
    "soc2": "SOC 2 Type II",
    "iso42001": "ISO 42001",
}


def render_report_html(
    summary: dict,
    attestations: list,
    executions: list,
    chain_integrity: dict,
    framework: str,
    start: str,
    end: str,
) -> str:
    """Render compliance report as HTML string."""
    framework_mapping = get_framework_mapping(framework, summary, attestations, executions)
    return _REPORT_TEMPLATE.render(
        summary=summary,
        attestations=attestations,
        executions=executions,
        chain_integrity=chain_integrity,
        framework_mapping=framework_mapping,
        framework_name=_FRAMEWORK_NAMES.get(framework, framework),
        start=start,
        end=end,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )


def generate_pdf_report(
    summary: dict,
    attestations: list,
    executions: list,
    chain_integrity: dict,
    framework: str,
    start: str,
    end: str,
) -> bytes:
    """Generate a PDF compliance report. Requires WeasyPrint + system libraries."""
    import weasyprint

    html = render_report_html(
        summary=summary,
        attestations=attestations,
        executions=executions,
        chain_integrity=chain_integrity,
        framework=framework,
        start=start,
        end=end,
    )
    return weasyprint.HTML(string=html).write_pdf()
