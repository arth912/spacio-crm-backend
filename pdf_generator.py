import re
import os
import sys
import logging
import hashlib
import threading
from functools import lru_cache
from collections import OrderedDict
from jinja2 import Template  # noqa: F401
from datetime import datetime

logger = logging.getLogger(__name__)

_LOGO_RE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,([A-Za-z0-9+/=\s]+)$")
_LOGO_INPUT_MAX = 12_000_000      # chars of data URI we are willing to decode
_LOGO_BOX = (600, 240)            # px; plenty for a ~45px-tall print logo at high DPI


def sanitize_logo(value):
    if not isinstance(value, str):
        return None
    return _sanitize_logo_cached(value)


@lru_cache(maxsize=8)
def _sanitize_logo_cached(value):
    """Return a small, safe PNG data URI for the PDF, or None.

    Only inline raster data URIs are accepted (http/file URLs are never
    fetched - SSRF). Large uploads are decoded with Pillow and downscaled
    instead of being silently dropped; this also fixes a wrong MIME label
    (e.g. JPEG bytes tagged image/png) and keeps the PDF small.
    """
    if not isinstance(value, str) or len(value) > _LOGO_INPUT_MAX:
        return None
    m = _LOGO_RE.match(value.strip())
    if not m:
        return None
    try:
        import base64, io
        from PIL import Image

        raw = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
        img = Image.open(io.BytesIO(raw))
        img.load()
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGBA")
        img.thumbnail(_LOGO_BOX, Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except Exception as e:
        logger.warning(f"Company logo could not be processed for PDF: {e}")
        return None

def _safe_url_fetcher(url, *args, **kwargs):
    """Block file://, http(s):// etc. in PDF rendering (prevents SSRF / local file read)."""
    if url.startswith("data:"):
        from weasyprint import default_url_fetcher
        return default_url_fetcher(url, *args, **kwargs)
    raise ValueError(f"External resource blocked in PDF rendering: {url[:60]}")


# Monkeypatch cffi to find macOS Homebrew dynamic libraries when running on Apple Silicon
if sys.platform == 'darwin':
    try:
        import cffi
        original_dlopen = cffi.FFI.dlopen

        def patched_dlopen(self, name, flags=0):
            try:
                return original_dlopen(self, name, flags)
            except OSError:
                if name and not name.startswith('/'):
                    # Try prepending /opt/homebrew/lib/
                    brew_path = os.path.join('/opt/homebrew/lib', name)
                    try:
                        return original_dlopen(self, brew_path, flags)
                    except OSError:
                        pass
                    
                    # Map name to common macOS Homebrew library filenames
                    base_name = name
                    if base_name.startswith('lib'):
                        base_name = base_name[3:]
                    # Remove trailing numbers like -0
                    parts = base_name.split('-')
                    if parts and parts[-1].isdigit():
                        base_name = '-'.join(parts[:-1])
                    
                    # Try standard variations
                    for ext in ['.dylib', '.0.dylib', '.so']:
                        for prefix in ['lib', '']:
                            try_name = f'{prefix}{base_name}{ext}'
                            try_path = os.path.join('/opt/homebrew/lib', try_name)
                            try:
                                return original_dlopen(self, try_path, flags)
                            except OSError:
                                pass
                raise

        cffi.FFI.dlopen = patched_dlopen
    except Exception:
        pass

HTML = None
WEASYPRINT_AVAILABLE = False
WEASYPRINT_ERROR = ""


def _load_weasyprint() -> bool:
    """Import WeasyPrint on demand. A failure is NOT cached, so installing
    pango later takes effect without restarting the server."""
    global HTML, WEASYPRINT_AVAILABLE, WEASYPRINT_ERROR
    if WEASYPRINT_AVAILABLE:
        return True
    try:
        from weasyprint import HTML as _HTML
        HTML, WEASYPRINT_AVAILABLE, WEASYPRINT_ERROR = _HTML, True, ""
    except Exception as e:
        WEASYPRINT_ERROR = str(e)
    return WEASYPRINT_AVAILABLE


_load_weasyprint()



# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def _num(value, default=0.0):
    """None/garbage-safe float."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _group_indian(int_part: str) -> str:
    """12345678 -> 1,23,45,678"""
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def inr(value) -> str:
    """Format as Indian rupees: ₹1,80,752.40 (negatives as -₹...)."""
    n = round(_num(value), 2)
    sign = "-" if n < 0 else ""
    int_part, dec_part = f"{abs(n):.2f}".split(".")
    return f"{sign}₹{_group_indian(int_part)}.{dec_part}"


def _g(value) -> str:
    return f"{_num(value):g}"


def item_area(qi: dict) -> float:
    """Same area rule as pricing.calculate_item_price (length x height, else x breadth, else x 1)."""
    length = _num(qi.get("length"), 1.0) or 1.0
    height = _num(qi.get("height"), 0.0)
    breadth = _num(qi.get("breadth"), 0.0)
    second = height if height > 0 else (breadth if breadth > 0 else 1.0)
    return length * second


def qty_label(qi: dict) -> str:
    """Human readable quantity: '1 pc', '2 × 28 sq ft', '1 × 12 rft'."""
    qty = _num(qi.get("qty"), 1.0)
    ptype = qi.get("pricing_type")
    if ptype == "sq_ft":
        return f"{_g(qty)} × {_g(item_area(qi))} sq ft"
    if ptype == "running_ft":
        return f"{_g(qty)} × {_g(_num(qi.get('length'), 1.0) or 1.0)} rft"
    return f"{_g(qty)} pc"


def css_str(value) -> str:
    """Make a value safe inside a CSS string literal (page margin-box content)."""
    text = "" if value is None else str(value)
    return re.sub(r'["\\\n\r{}<>]', " ", text.replace("&", "and")).strip()


def _parse_date(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return None


def quote_number(quotation: dict) -> str:
    qid = quotation.get("id")
    if not qid:
        return "DRAFT"
    qid = str(qid)
    if qid.startswith("local_"):
        return f"LQ-{qid[-6:].upper()}"
    return f"QT-{qid[:8].upper()}"


# --------------------------------------------------------------------------
# Template (single source of truth)
# --------------------------------------------------------------------------

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Quotation - {{ quotation.project_name }}</title>
<style>
    @page {
        size: A4;
        margin: 22mm 16mm 20mm 16mm;
        @top-right {
            content: "{{ brand_css }} PROPOSAL";
            font-family: Helvetica, Arial, sans-serif;
            font-size: 7pt; font-weight: 700; letter-spacing: 1.5px; color: #b45309;
        }
        @bottom-left {
            content: "{{ brand_plain }} Proposal  \\2022  {{ project_css }}  \\2022  Generated by SpaceIO";
            font-family: Helvetica, Arial, sans-serif;
            font-size: 7pt; color: #94a3b8;
        }
        @bottom-right {
            content: "Page " counter(page) " of " counter(pages);
            font-family: Helvetica, Arial, sans-serif;
            font-size: 7pt; color: #94a3b8;
        }
    }
    @page :first {
        margin: 16mm;
        @top-right { content: none; }
        @bottom-left { content: none; }
        @bottom-right { content: none; }
    }

    * { box-sizing: border-box; }
    body {
        font-family: Helvetica, Arial, sans-serif;
        color: #1e293b; margin: 0; padding: 0;
        font-size: 9pt; line-height: 1.45; background: #ffffff;
    }
    table { border-collapse: collapse; }

    /* ---------- COVER ---------- */
    .cover { page-break-after: always; break-after: page; height: 255mm; position: relative; }
    .cover-brand td { border: none; padding: 0; vertical-align: middle; }
    .brand-icon {
        width: 38px; height: 38px; border-radius: 8px; background: #1c1917; color: #fff;
        font-weight: 700; font-size: 18px; line-height: 38px; text-align: center;
    }
    .brand-name { font-size: 15pt; font-weight: 700; color: #0f172a; padding-left: 10px; }
    .brand-sub { font-size: 7pt; color: #b45309; font-weight: 700; letter-spacing: 1.5px; padding-left: 10px; }
    .cover-hero { margin-top: 62mm; }
    .cover-badge {
        display: inline-block; font-size: 7.5pt; font-weight: 700; color: #b45309;
        background: #fef3c7; padding: 4px 10px; border-radius: 4px; letter-spacing: 1.2px;
    }
    .cover-title {
        font-size: 28pt; font-weight: 700; color: #0f172a; line-height: 1.2;
        margin: 10px 0 8px 0; letter-spacing: normal; word-spacing: normal;
        font-kerning: normal; overflow-wrap: break-word;
    }
    .cover-sub { font-size: 10.5pt; color: #475569; margin: 0 0 10mm 0; }
    .rule { border: none; border-top: 2px solid #e2e8f0; margin: 0 0 6mm 0; }
    .meta-title { font-size: 7.5pt; font-weight: 700; color: #94a3b8; letter-spacing: 2px; margin-bottom: 8px; }
    .meta-table { width: 100%; }
    .meta-table td { border: none; padding: 7px 15px 7px 0; vertical-align: top; width: 50%; }
    .meta-label { font-size: 7pt; font-weight: 700; color: #64748b; letter-spacing: 1px; text-transform: uppercase; }
    .meta-value { font-size: 10pt; font-weight: 600; color: #1e293b; overflow-wrap: break-word; }
    .meta-code { font-family: 'Courier New', Courier, monospace; font-size: 10pt; font-weight: 700; color: #b45309; }

    /* ---------- SECTIONS ---------- */
    .section-title {
        font-size: 13pt; font-weight: 700; color: #0f172a; margin: 0 0 10px 0;
        border-bottom: 2px solid #e2e8f0; padding-bottom: 5px;
        break-after: avoid; page-break-after: avoid;
    }
    .room { margin-bottom: 12px; }
    .room-header {
        font-size: 10pt; font-weight: 700; color: #1e293b; background: #f8fafc;
        border-left: 3px solid #b45309; padding: 5px 10px; margin-bottom: 0;
        text-transform: uppercase; letter-spacing: 0.8px;
        break-after: avoid; page-break-after: avoid;
    }

    table.items { width: 100%; table-layout: fixed; }
    table.items thead { display: table-header-group; }
    table.items tr { break-inside: avoid; page-break-inside: avoid; }
    table.items th {
        background: #f1f5f9; color: #475569; font-weight: 700; text-align: left;
        padding: 6px 8px; font-size: 7pt; border-bottom: 1.5px solid #e2e8f0;
        text-transform: uppercase; letter-spacing: 0.5px;
    }
    table.items td {
        padding: 6px 8px; font-size: 8.5pt; border-bottom: 1px solid #eef2f7;
        vertical-align: top; line-height: 1.35; overflow-wrap: break-word;
    }
    .c-no { width: 6%; text-align: center; }
    .c-item { width: 34%; }
    .c-cat { width: 16%; }
    .c-qty { width: 15%; text-align: center; }
    .c-rate { width: 14%; text-align: right; }
    .c-amt { width: 15%; text-align: right; }
    .sub { font-size: 7pt; color: #718096; }
    .remark { font-size: 7pt; color: #b45309; font-style: italic; margin-top: 2px; }
    tr.room-total td {
        background: #f8fafc; font-weight: 700; font-size: 8.5pt; border-bottom: 1.5px solid #e2e8f0;
    }

    /* ---------- SUMMARY / TERMS / SIGNATURES (kept together) ---------- */
    .closing { break-inside: avoid; page-break-inside: avoid; margin-top: 14px; }
    .summary { width: 100%; }
    .summary td { border: none; padding: 0; vertical-align: top; }
    .summary-box { width: 290px; background: #f8fafc; border: 1px solid #e2e8f0; }
    .summary-box td { padding: 6px 12px; font-size: 8.5pt; }
    .s-label { font-weight: 600; color: #64748b; }
    .s-value { font-weight: 700; color: #1e293b; text-align: right; }
    .s-discount, .s-discount .s-label, .s-discount .s-value { color: #c53030; }
    tr.grand td { background: #1c1917; padding: 8px 12px; }
    tr.grand .s-label, tr.grand .s-value { color: #ffffff; font-weight: 700; font-size: 10pt; }

    .terms { margin-top: 14px; background: #fafbfc; border: 1px solid #e2e8f0; padding: 12px 16px; }
    .terms-title {
        font-weight: 700; font-size: 9pt; color: #0f172a; margin-bottom: 6px;
        text-transform: uppercase; letter-spacing: 0.8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px;
    }
    .terms-body { font-size: 8pt; color: #475569; line-height: 1.55; white-space: pre-line; }

    .sig { width: 100%; margin-top: 8px; }
    .sig td { border: none; width: 50%; vertical-align: top; padding: 0; }
    .sig-line { margin-top: 34px; border-top: 1px solid #cbd5e1; padding-top: 5px; width: 75%; }
    .sig-right .sig-line { margin-left: auto; text-align: right; }
    .sig-label { font-size: 8pt; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: 0.4px; }
    .sig-sub { font-size: 7pt; color: #94a3b8; margin-top: 1px; }

    @media print { .no-print { display: none !important; } }
</style>
</head>
<body>
{% if weasyprint_failed %}
<div class="no-print" style="background:#FFFBEB;border:1px solid #F59E0B;color:#B45309;padding:14px;margin:10px 0;font-size:9pt;">
    <strong>Print-Friendly Preview Mode</strong><br>
    Press <strong>Cmd + P</strong> (Mac) or <strong>Ctrl + P</strong> (Windows) and choose Save as PDF.
</div>
{% endif %}

<!-- COVER -->
<div class="cover">
    <table class="cover-brand">
        <tr>
            {% if creator.company_logo %}
            <td style="padding-right: 6px;">
                <img src="{{ creator.company_logo }}" alt="Logo" style="height: 72px; max-width: 200px; width: auto;" />
            </td>
            {% else %}
            <td style="width: 48px;">
                <div class="brand-icon">{{ brand[:1] | upper }}</div>
            </td>
            {% endif %}
            <td>
                <div class="brand-name">{{ brand }}</div>
                <div class="brand-sub">INTERIOR DESIGN</div>
            </td>
        </tr>
    </table>

    <div class="cover-hero">
        <div class="cover-badge">DESIGN PROPOSAL &amp; ESTIMATE</div>
        <h1 class="cover-title">{{ quotation.project_name or 'Untitled Project' }}</h1>
        <p class="cover-sub">Complete interior design execution &amp; supply estimate.</p>
        <hr class="rule">

        <div class="meta-title">PROJECT &amp; PROPOSAL DETAILS</div>
        <table class="meta-table">
            <tr>
                <td><div class="meta-label">Client Name</div><div class="meta-value">{{ quotation.client_name or '-' }}</div></td>
                <td><div class="meta-label">Proposal Date</div><div class="meta-value">{{ date_str }}</div></td>
            </tr>
            <tr>
                <td><div class="meta-label">Project Name</div><div class="meta-value">{{ quotation.project_name or '-' }}</div></td>
                <td><div class="meta-label">Quote Version</div><div class="meta-value">V{{ quotation.version or 1 }}</div></td>
            </tr>
            <tr>
                <td><div class="meta-label">Site Address</div><div class="meta-value">{{ quotation.site_address or 'Not provided' }}</div></td>
                <td><div class="meta-label">Quote Number</div><div class="meta-code">{{ quote_no }}</div></td>
            </tr>
            {% if creator.name or creator.phone or creator.email %}
            <tr>
                <td><div class="meta-label">Prepared By</div><div class="meta-value">{{ creator.name or brand }}</div></td>
                <td>
                    <div class="meta-label">Designer Contact</div>
                    <div class="meta-value">
                        {% if creator.phone %}{{ creator.phone }}{% endif %}{% if creator.phone and creator.email %}<br>{% endif %}{% if creator.email %}{{ creator.email }}{% endif %}
                    </div>
                </td>
            </tr>
            {% endif %}
        </table>
    </div>
</div>

<!-- ITEMS -->
<h2 class="section-title">Itemized Estimates by Room</h2>
{% for room in rooms %}
<div class="room">
    <div class="room-header">{{ room.name }}</div>
    <table class="items">
        <thead>
            <tr>
                <th class="c-no">#</th>
                <th class="c-item">Item &amp; Description</th>
                <th class="c-cat">Category</th>
                <th class="c-qty" style="text-align:center;">Qty</th>
                <th class="c-rate" style="text-align:right;">Final Rate</th>
                <th class="c-amt" style="text-align:right;">Amount</th>
            </tr>
        </thead>
        <tbody>
            {% for qi in room['items'] %}
            <tr>
                <td class="c-no">{{ loop.index }}</td>
                <td class="c-item">
                    <strong>{{ qi.item_name or 'Item' }}</strong>
                    {% if qi.item_brand %}<div class="sub">{{ qi.item_brand }}</div>{% endif %}
                    {% if qi.item_dimensions %}<div class="sub">{{ qi.item_dimensions }}</div>{% endif %}
                    {% if qi.remark %}<div class="remark">{{ qi.remark }}</div>{% endif %}
                </td>
                <td class="c-cat">{{ qi.category_name or '-' }}</td>
                <td class="c-qty">{{ qi | qty_label }}</td>
                <td class="c-rate">{{ qi.final_price | inr }}</td>
                <td class="c-amt"><strong>{{ qi.total_amount | inr }}</strong></td>
            </tr>
            {% endfor %}
            <tr class="room-total">
                <td colspan="5" style="text-align:right;">{{ room.name }} total</td>
                <td class="c-amt">{{ room.total | inr }}</td>
            </tr>
        </tbody>
    </table>
</div>
{% endfor %}

<!-- SUMMARY + TERMS + SIGNATURES -->
<div class="closing">
    <h2 class="section-title">Cost Summary</h2>
    <table class="summary">
        <tr>
            <td></td>
            <td style="width: 290px;">
                <table class="summary-box">
                    <tr><td class="s-label">Items subtotal (excl. GST)</td><td class="s-value">{{ total_excl_gst | inr }}</td></tr>
                    <tr><td class="s-label">GST / Taxes</td><td class="s-value">{{ total_gst | inr }}</td></tr>
                    {% if discount > 0 %}
                    <tr class="s-discount"><td class="s-label">Discount</td><td class="s-value">-{{ discount | inr }}</td></tr>
                    {% endif %}
                    <tr class="grand"><td class="s-label">Grand Total</td><td class="s-value">{{ grand_total | inr }}</td></tr>
                </table>
            </td>
        </tr>
    </table>

    <div class="terms">
        <div class="terms-title">{{ terms_title }}</div>
        <div class="terms-body">{{ terms_body }}</div>
    </div>

    <table class="sig">
        <tr>
            <td>
                <div class="sig-line">
                    <div class="sig-label">Authorized Designer / Architect</div>
                    <div class="sig-sub">For {{ brand }}</div>
                </div>
            </td>
            <td class="sig-right">
                <div class="sig-line">
                    <div class="sig-label">Client Acceptance Signature</div>
                    <div class="sig-sub">Accepted By: {{ quotation.client_name or '' }}</div>
                </div>
            </td>
        </tr>
    </table>
</div>
</body>
</html>
"""

DEFAULT_TERMS = (
    "1. Payment Terms: 40% Booking Advance, 40% on Material Delivery, 20% on Completion.\n"
    "2. Prices are valid for 30 days from the proposal generation date.\n"
    "3. Any extra items or alterations requested outside this proposal will be billed separately.\n"
    "4. Completion timeline is subject to site handover, client design approvals, and timely payment stages."
)


def _build_context(quotation_dict: dict, weasyprint_failed: bool) -> dict:
    creator = quotation_dict.get("creator") or {}
    brand = (creator.get("name") or "").strip() or "SpaceIO"

    # Group by room, preserving first-seen order
    rooms_map = {}
    total_excl_gst = 0.0
    total_item_gst = 0.0
    for qi in quotation_dict.get("items") or []:
        key = qi.get("room_id") or qi.get("room_name") or "general"
        room = rooms_map.setdefault(
            key,
            {"name": (qi.get("room_name") or "").strip() or "General", "items": [], "total": 0.0},
        )
        room["items"].append(qi)
        total_amt = _num(qi.get("total_amount"))
        room["total"] += total_amt

        gst_percent = _num(qi.get("snapshot_gst_percent"))
        pre_tax = total_amt / (1.0 + gst_percent / 100.0)
        total_excl_gst += pre_tax
        total_item_gst += total_amt - pre_tax

    global_gst = _num(quotation_dict.get("gst_amount"))
    total_gst = total_item_gst + global_gst
    discount = _num(quotation_dict.get("discount_amount"))
    grand_total = _num(quotation_dict.get("grand_total"))

    created = _parse_date(quotation_dict.get("created_at")) or datetime.now()

    custom_terms = (quotation_dict.get("terms_conditions") or "").strip()
    if custom_terms:
        terms_title, terms_body = "Terms & Conditions", custom_terms
    else:
        terms_title = "Standard Project Terms"
        terms_body = (creator.get("general_terms") or "").strip() or DEFAULT_TERMS

    return {
        "quotation": quotation_dict,
        "creator": creator,
        "brand": brand,
        "brand_css": css_str(brand).upper(),
        "brand_plain": css_str(brand),
        "project_css": css_str(quotation_dict.get("project_name")),
        "rooms": list(rooms_map.values()),
        "date_str": created.strftime("%B %d, %Y"),
        "quote_no": quote_number(quotation_dict),
        "weasyprint_failed": weasyprint_failed,
        "total_excl_gst": total_excl_gst,
        "total_gst": total_gst,
        "discount": discount,
        "grand_total": grand_total,
        "terms_title": terms_title,
        "terms_body": terms_body,
    }


_TEMPLATE_CACHE = None


def _jinja_template() -> Template:
    """Compile the template once per process (parsing it is pure overhead per request)."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        from jinja2 import Environment
        env = Environment(autoescape=True)
        env.filters["inr"] = inr
        env.filters["qty_label"] = qty_label
        _TEMPLATE_CACHE = env.from_string(HTML_TEMPLATE)
    return _TEMPLATE_CACHE


def generate_quotation_html(quotation_dict: dict, weasyprint_failed: bool = False) -> str:
    """Render the quotation as HTML (single template, used for both PDF engines)."""
    return _jinja_template().render(**_build_context(quotation_dict, weasyprint_failed))


def _strip_page_rules(html: str) -> str:
    """Remove @page blocks (incl. nested margin boxes) - xhtml2pdf cannot parse them."""
    out, i = [], 0
    while True:
        m = re.search(r"@page\b[^{]*\{", html[i:])
        if not m:
            out.append(html[i:])
            break
        start = i + m.start()
        out.append(html[i:start])
        depth, j = 1, i + m.end()
        while j < len(html) and depth:
            depth += {"{": 1, "}": -1}.get(html[j], 0)
            j += 1
        i = j
    return "".join(out)


_XHTML2PDF_CSS = """
<style>
    @page { size: A4; margin: 16mm; }
    .cover { height: auto; }
    .cover-hero { margin-top: 40mm; }
    .meta-table td { padding: 3px 10px 3px 0; }
    .meta-label, .meta-value, .meta-code { margin: 0; padding: 0; line-height: 1.2; }
    .cover-badge { display: block; }
    .rule { border-top: 1px solid #e2e8f0; }
</style>
"""


def _simplify_for_xhtml2pdf(html: str) -> str:
    """xhtml2pdf mis-handles fixed heights, break-inside on rows and the ₹
    glyph (its built-in Helvetica has none, so it prints a box). Tone the
    markup down so the fallback still gives a readable proposal."""
    out = _strip_page_rules(html)
    # Fixed-height cover + per-row keep-together make xhtml2pdf put each
    # cover row on its own page.
    out = re.sub(r"(break|page-break)-inside:\s*avoid;?", "", out)
    out = re.sub(r"height:\s*255mm;?", "", out)
    out = out.replace("₹", "Rs. ")
    return out.replace("</head>", _XHTML2PDF_CSS + "</head>")


_PDF_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_PDF_CACHE_MAX = 32
_PDF_CACHE_LOCK = threading.Lock()


def generate_quotation_pdf(quotation_dict: dict) -> bytes:
    """Render to PDF bytes, reusing a recent result when the rendered HTML is identical
    (re-downloading an unchanged quotation is then instant)."""
    html_content = generate_quotation_html(quotation_dict, weasyprint_failed=False)
    key = hashlib.sha256(html_content.encode("utf-8")).hexdigest()
    with _PDF_CACHE_LOCK:
        cached = _PDF_CACHE.get(key)
        if cached is not None:
            _PDF_CACHE.move_to_end(key)
            return cached
    pdf = _render_pdf(html_content)
    with _PDF_CACHE_LOCK:
        _PDF_CACHE[key] = pdf
        while len(_PDF_CACHE) > _PDF_CACHE_MAX:
            _PDF_CACHE.popitem(last=False)
    return pdf


def _render_pdf(html_content: str) -> bytes:
    """
    Render HTML to PDF bytes. WeasyPrint first (full layout, page
    footer); xhtml2pdf as a no-system-deps fallback (no running footer).
    """

    if _load_weasyprint():
        try:
            logger.info("Compiling PDF with WeasyPrint...")
            return HTML(string=html_content, url_fetcher=_safe_url_fetcher).write_pdf()
        except Exception as e:
            logger.warning(f"WeasyPrint rendering failed: {e}. Falling back to xhtml2pdf.")
    else:
        logger.warning(f"WeasyPrint unavailable ({WEASYPRINT_ERROR[:200]}). Using xhtml2pdf fallback.")

    try:
        import io
        from xhtml2pdf import pisa

        logger.info("Compiling PDF with xhtml2pdf (fallback)...")
        simplified = _simplify_for_xhtml2pdf(html_content)
        buf = io.BytesIO()
        status = pisa.CreatePDF(io.BytesIO(simplified.encode("utf-8")), dest=buf)
        if status.err:
            logger.warning(f"xhtml2pdf reported {status.err} error(s) during rendering (non-fatal)")
        pdf_bytes = buf.getvalue()
        if pdf_bytes[:5] == b"%PDF-":
            return pdf_bytes
        raise RuntimeError("xhtml2pdf produced invalid PDF output")
    except ImportError:
        raise ImportError(
            "No PDF engine available. Install either WeasyPrint (brew install pango) "
            "or xhtml2pdf (pip install xhtml2pdf) to enable PDF generation."
        )
    except Exception as e:
        logger.error(f"xhtml2pdf fallback also failed: {e}")
        raise RuntimeError(f"PDF generation failed with all available engines: {e}")


# Backward-compatible alias
def generate_pdf(quotation_dict: dict) -> bytes:
    return generate_quotation_pdf(quotation_dict)
