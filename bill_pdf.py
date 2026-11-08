"""Bill PDF rendering: one Jinja document, four visual templates.

The caller (routers/billing.py) passes a plain dict, so this module never touches the
database. Rendering goes through pdf_generator.render_pdf_cached (WeasyPrint, with the
xhtml2pdf fallback and a small render cache). Layout uses tables rather than flex/grid so
the fallback engine renders it too. Autoescape is on and external resources are blocked
by pdf_generator._safe_url_fetcher, so user text cannot inject markup or fetch URLs.
"""
from datetime import date, datetime
from functools import lru_cache

from jinja2 import Environment

from pdf_generator import inr, render_pdf_cached

TEMPLATE_NAMES = ("classic", "modern", "minimal", "professional")
DEFAULT_TEMPLATE = "modern"

# Per-template colours. Everything else is shared CSS below.
_THEMES = {
    "classic":      {"accent": "#334155", "soft": "#F1F5F9", "on_accent": "#FFFFFF"},
    "modern":       {"accent": "#D97706", "soft": "#FFFBEB", "on_accent": "#FFFFFF"},
    "minimal":      {"accent": "#0F172A", "soft": "#FFFFFF", "on_accent": "#FFFFFF"},
    "professional": {"accent": "#0F766E", "soft": "#F0FDFA", "on_accent": "#FFFFFF"},
}

_CSS = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1e293b; line-height: 1.4; }
table { width: 100%; border-collapse: collapse; }
td, th { vertical-align: top; }
.muted { color: #64748b; }
.small { font-size: 9pt; }
.right { text-align: right; }
.label { font-size: 8pt; font-weight: bold; letter-spacing: .08em; text-transform: uppercase; color: #64748b; margin: 0 0 3px; }
.company-name { font-size: 16pt; font-weight: bold; margin: 0; }
.logo { max-height: 16mm; max-width: 60mm; }
.doc-title { font-size: 22pt; font-weight: bold; margin: 0; letter-spacing: .04em; }
.head td { padding: 0; }
.parties { margin-top: 8mm; }
.parties td { width: 50%; padding: 0 6mm 0 0; }
.who { font-size: 12pt; font-weight: bold; margin: 0 0 2px; }
.items { margin-top: 8mm; }
.items th { text-align: left; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .05em; padding: 7px 8px; }
.items td { padding: 8px; border-bottom: 1px solid #e2e8f0; }
.items .num { text-align: right; white-space: nowrap; }
.totals { width: 82mm; margin: 6mm 0 0 auto; }
.totals td { padding: 4px 0; }
.totals .grand td { font-size: 13pt; font-weight: bold; padding-top: 8px; }
.totals .due td { font-weight: bold; }
.section { margin-top: 8mm; }
.pay th { text-align: left; font-size: 8.5pt; color: #64748b; padding: 4px 0; border-bottom: 1px solid #cbd5e1; }
.pay th.right { text-align: right; }
.pay td { padding: 4px 0; border-bottom: 1px solid #f1f5f9; }
.pre { white-space: pre-line; }
.badge { display: inline-block; padding: 2px 9px; border-radius: 99px; font-size: 8pt; font-weight: bold; text-transform: uppercase; }
.sign { margin-top: 10mm; page-break-inside: avoid; }
.sign td { width: 50%; vertical-align: bottom; }
.stamp { max-height: 28mm; max-width: 45mm; }
.signature { max-height: 18mm; max-width: 55mm; }
.sign-line { border-top: 1px solid #94a3b8; margin-top: 2mm; padding-top: 1mm; display: inline-block; min-width: 50mm; }
.foot { margin-top: 10mm; padding-top: 4mm; border-top: 1px solid #e2e8f0; text-align: center; font-size: 8.5pt; color: #64748b; }

/* Classic: ruled, formal */
.t-classic .head-rule { border-bottom: 2px solid {{ th.accent }}; padding-bottom: 5mm; }
.t-classic .items th { background: {{ th.soft }}; border: 1px solid {{ th.accent }}; }
.t-classic .items td { border: 1px solid #cbd5e1; }
.t-classic .doc-title { color: {{ th.accent }}; }
.t-classic .totals .grand td { border-top: 2px solid {{ th.accent }}; }

/* Modern: colour header band, striped rows */
.t-modern .band { background: {{ th.accent }}; color: #fff; padding: 7mm 8mm; border-radius: 3mm; }
.t-modern .band .muted, .t-modern .band .label { color: #FEF3C7; }
.t-modern .items th { background: {{ th.soft }}; color: #92400E; }
.t-modern .items tr.alt td { background: #F8FAFC; }
.t-modern .items td { border-bottom: 0; }
.t-modern .totals .grand td { color: {{ th.accent }}; }
.t-modern .badge { background: #FEF3C7; color: #92400E; }

/* Minimal: type only */
.t-minimal .doc-title { font-size: 16pt; font-weight: normal; letter-spacing: .2em; }
.t-minimal .items th { border-bottom: 1px solid #0F172A; padding-left: 0; padding-right: 0; }
.t-minimal .items td { border-bottom: 1px solid #f1f5f9; padding-left: 0; padding-right: 0; }
.t-minimal .totals .grand td { border-top: 1px solid #0F172A; }

/* Professional: side accent, GST summary box */
.t-professional .page-wrap { border-left: 3mm solid {{ th.accent }}; padding-left: 7mm; }
.t-professional .items th { background: {{ th.accent }}; color: #fff; }
.t-professional .totals { border: 1px solid {{ th.accent }}; border-radius: 2mm; }
.t-professional .totals td { padding: 4px 5mm; }
.t-professional .totals .grand td { color: {{ th.accent }}; }
.t-professional .doc-title { color: {{ th.accent }}; }
.t-professional .badge { background: {{ th.soft }}; color: {{ th.accent }}; }
"""

_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ bill.bill_number }}</title><style>{{ css | safe }}</style></head>
<body class="t-{{ t }}"><div class="page-wrap">

{% macro company_block() -%}
  {% if company.logo %}<img class="logo" src="{{ company.logo }}" alt="">{% else %}<p class="company-name">{{ company.name }}</p>{% endif %}
  <div class="small muted">{% if company.logo %}{{ company.name }}<br>{% endif %}{% if company.phone %}{{ company.phone }}{% endif %}{% if company.phone and company.email %} · {% endif %}{{ company.email or '' }}</div>
{%- endmacro %}
{% macro doc_meta() -%}
  <p class="doc-title">{% if bill.status == 'generated' %}INVOICE{% else %}BILL{% endif %}</p>
  <div class="small">No. <b>{{ bill.bill_number }}</b></div>
  <div class="small">Date <b>{{ bill.bill_date | dfmt }}</b></div>
  {% if bill.due_date %}<div class="small">Due <b>{{ bill.due_date | dfmt }}</b></div>{% endif %}
{%- endmacro %}

{% if t == 'modern' %}
<div class="band"><table class="head"><tr><td>{{ company_block() }}</td><td class="right">{{ doc_meta() }}</td></tr></table></div>
{% else %}
<div class="head-rule"><table class="head"><tr><td>{{ company_block() }}</td><td class="right">{{ doc_meta() }}</td></tr></table></div>
{% endif %}

<table class="parties"><tr>
  <td>
    <p class="label">Billed to</p>
    <p class="who">{{ customer.name }}</p>
    <div class="small muted pre">{% if customer.address %}{{ customer.address }}{% endif %}{% if customer.phone %}
{{ customer.phone }}{% endif %}{% if customer.email %}
{{ customer.email }}{% endif %}{% if customer.gstin %}
GSTIN: {{ customer.gstin }}{% endif %}</div>
  </td>
  <td>
    <p class="label">Project</p>
    <p class="who">{{ bill.project_name or '—' }}</p>
    <div class="small">Bill date: <b>{{ bill.bill_date | dfmt }}</b></div>
    {% if bill.payment_status != 'unpaid' %}<div class="small"><span class="badge">{{ bill.payment_status }}</span></div>{% endif %}
  </td>
</tr></table>

<table class="items">
  <thead><tr><th style="width:6%">#</th><th>Item</th><th class="num">Qty</th><th class="num">Rate</th><th class="num">GST</th><th class="num">Amount</th></tr></thead>
  <tbody>
  {% for it in bill['items'] %}
    <tr class="{{ 'alt' if loop.index is even else '' }}">
      <td>{{ loop.index }}</td><td>{{ it.description }}{% if it.remark %}<div class="small muted pre"><i>{{ it.remark }}</i></div>{% endif %}</td>
      <td class="num">{{ it | measure }}</td><td class="num">{{ it.rate | inr }}<div class="small muted">{{ it | unit }}</div></td>
      <td class="num">{{ it.tax_percent | qty }}%</td><td class="num"><b>{{ it.amount | inr }}</b></td>
    </tr>
  {% endfor %}
  </tbody>
</table>

<table class="totals">
  <tr><td class="muted">Subtotal</td><td class="right">{{ bill.subtotal | inr }}</td></tr>
  {% if bill.discount_amount %}<tr><td class="muted">Discount</td><td class="right">- {{ bill.discount_amount | inr }}</td></tr>{% endif %}
  <tr><td class="muted">GST</td><td class="right">{{ bill.tax_amount | inr }}</td></tr>
  <tr class="grand"><td>Total</td><td class="right">{{ bill.grand_total | inr }}</td></tr>
  {% if bill.paid_amount %}
  <tr><td class="muted">Paid</td><td class="right">{{ bill.paid_amount | inr }}</td></tr>
  <tr class="due"><td>Balance due</td><td class="right">{{ bill.balance | inr }}</td></tr>
  {% endif %}
</table>

{% if payments %}
<div class="section">
  <p class="label">Payments received</p>
  <table class="pay"><thead><tr><th>Date</th><th>Method</th><th>Reference</th><th class="right">Amount</th></tr></thead><tbody>
  {% for p in payments %}<tr><td>{{ p.date | dfmt }}</td><td>{{ p.method }}</td><td>{{ p.reference or '—' }}</td><td class="right">{{ p.amount | inr }}</td></tr>{% endfor %}
  </tbody></table>
</div>
{% endif %}

{% if bill.notes %}<div class="section"><p class="label">Notes</p><div class="small pre">{{ bill.notes }}</div></div>{% endif %}
{% if company.terms %}<div class="section"><p class="label">Terms &amp; conditions</p><div class="small muted pre">{{ company.terms }}</div></div>{% endif %}

{% if company.signature or company.stamp %}
<table class="sign"><tr>
  <td>{% if company.stamp %}<img class="stamp" src="{{ company.stamp }}" alt="">{% endif %}</td>
  <td class="right">{% if company.signature %}<img class="signature" src="{{ company.signature }}" alt=""><div class="sign-line small muted">Authorised signatory{% if company.name %} · {{ company.name }}{% endif %}</div>{% endif %}</td>
</tr></table>
{% endif %}

<div class="foot">Thank you for your business · {{ company.name }}</div>
</div></body></html>"""


def _fmt_date(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    return str(value or "")


def _fmt_qty(value) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return ""


_UNITS = {"sq_ft": "sq ft", "running_ft": "ft", "piece": "pc"}


def _item_get(it, key, default=None):
    return it.get(key, default) if isinstance(it, dict) else getattr(it, key, default)


def _fmt_measure(it) -> str:
    """Quantity as billed, e.g. '2', '2 x 10 ft' or '2 x 10 x 8 sq ft'."""
    qty = _fmt_qty(_item_get(it, "qty"))
    kind = _item_get(it, "pricing_type", "piece")
    if kind == "running_ft":
        return f"{qty} x {_fmt_qty(_item_get(it, 'length'))} ft"
    if kind == "sq_ft":
        h, b = _item_get(it, "height", 0) or 0, _item_get(it, "breadth", 0) or 0
        second = h if h > 0 else b
        dims = _fmt_qty(_item_get(it, "length")) + (f" x {_fmt_qty(second)}" if second > 0 else "")
        return f"{qty} x {dims} sq ft"
    return qty


def _fmt_unit(it) -> str:
    kind = _item_get(it, "pricing_type", "piece")
    return "" if kind == "piece" else f"per {_UNITS.get(kind, '')}"


@lru_cache(maxsize=1)
def _env() -> Environment:
    env = Environment(autoescape=True)
    env.filters.update(inr=inr, dfmt=_fmt_date, qty=_fmt_qty, measure=_fmt_measure, unit=_fmt_unit)
    return env


@lru_cache(maxsize=len(TEMPLATE_NAMES))
def _compiled(name: str):
    env = _env()
    css = env.from_string(_CSS).render(th=_THEMES[name])
    return env.from_string(_HTML), css


def generate_bill_html(context: dict) -> str:
    name = context.get("template") if context.get("template") in TEMPLATE_NAMES else DEFAULT_TEMPLATE
    template, css = _compiled(name)
    return template.render(
        t=name, css=css, bill=context["bill"], customer=context["customer"],
        company=context["company"], payments=context.get("payments") or [],
    )


def generate_bill_pdf(context: dict) -> bytes:
    return render_pdf_cached(generate_bill_html(context))
