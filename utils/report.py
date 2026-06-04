"""
utils/report.py
PDF credit-memo generator for IFC Prospect Lookup.

Produces a single-page (occasionally two-page) branded PDF summarising an
assessment. Uses reportlab's Platypus flowables — pure Python, no system
dependencies, runs fine on Streamlit Community Cloud.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
    KeepTogether, HRFlowable,
)


# ── Brand palette (matches .streamlit/config.toml) ────────────
IFC_NAVY       = colors.HexColor("#1b3a6b")
IFC_TEAL       = colors.HexColor("#2e7d8f")
IFC_LIGHT      = colors.HexColor("#eef0f4")
IFC_LIGHT_GREY = colors.HexColor("#d8dde6")
IFC_TEXT       = colors.HexColor("#2c3e50")
IFC_MUTED      = colors.HexColor("#6b7785")
VERDICT_GREEN  = colors.HexColor("#1e7a3c")
VERDICT_AMBER  = colors.HexColor("#b8740c")
VERDICT_RED    = colors.HexColor("#a62828")


def _styles() -> dict:
    """Paragraph styles used throughout the report."""
    base = getSampleStyleSheet()["Normal"]
    base.fontName = "Helvetica"
    base.fontSize = 9.5
    base.textColor = IFC_TEXT
    base.leading = 13

    return {
        "body": ParagraphStyle(
            "body", parent=base, fontSize=9.5, leading=13, textColor=IFC_TEXT,
        ),
        "body_bold": ParagraphStyle(
            "body_bold", parent=base, fontName="Helvetica-Bold",
            fontSize=9.5, leading=13, textColor=IFC_TEXT,
        ),
        "muted": ParagraphStyle(
            "muted", parent=base, fontSize=8.5, leading=11, textColor=IFC_MUTED,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName="Helvetica-Bold",
            fontSize=18, leading=22, textColor=IFC_NAVY,
            spaceBefore=0, spaceAfter=2,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=IFC_NAVY,
            spaceBefore=10, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base, fontSize=10, leading=13,
            textColor=IFC_MUTED, spaceAfter=8,
        ),
        "verdict": ParagraphStyle(
            "verdict", parent=base, fontName="Helvetica-Bold",
            fontSize=20, leading=24, alignment=1,  # centred
            textColor=colors.white,
        ),
        "verdict_sub": ParagraphStyle(
            "verdict_sub", parent=base, fontSize=10, leading=13,
            alignment=1, textColor=colors.white,
        ),
        "flag_red": ParagraphStyle(
            "flag_red", parent=base, fontName="Helvetica-Bold",
            fontSize=9.5, leading=13, textColor=VERDICT_RED,
        ),
        "flag_amber": ParagraphStyle(
            "flag_amber", parent=base, fontSize=9.5, leading=13,
            textColor=VERDICT_AMBER,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base, fontSize=7.5, leading=10,
            textColor=IFC_MUTED, alignment=1,
        ),
    }


# ── Helpers ───────────────────────────────────────────────────

def _fmt_money_eur(v) -> str:
    if v is None or v == 0:
        return "—"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:   return f"{sign}€{a/1e9:.2f}B"
    if a >= 1e6:   return f"{sign}€{a/1e6:.1f}M"
    if a >= 1e3:   return f"{sign}€{a/1e3:.0f}K"
    return f"{sign}€{a:,.0f}"


def _fmt_money_gbp(v) -> str:
    if v is None or v == 0:
        return "—"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:   return f"{sign}£{a/1e9:.2f}B"
    if a >= 1e6:   return f"{sign}£{a/1e6:.1f}M"
    if a >= 1e3:   return f"{sign}£{a/1e3:.0f}K"
    return f"{sign}£{a:,.0f}"


def _verdict_style(verdict: str):
    """Return (label, colour) for a verdict code."""
    return {
        "proceed":        ("PROCEED",             VERDICT_GREEN),
        "caution":        ("PROCEED WITH CAUTION", VERDICT_AMBER),
        "do_not_proceed": ("DO NOT PROCEED",      VERDICT_RED),
    }.get(verdict, ("REVIEW REQUIRED", IFC_MUTED))


def _md_to_rl(text: str) -> str:
    """Convert the subset of markdown used by build_summary into ReportLab HTML.
    Currently handles **bold** only (that's all build_summary uses)."""
    if not text:
        return ""
    # **bold** -> <b>bold</b>
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


# ── Page chrome (header/footer on every page) ─────────────────

def _page_chrome(canvas, doc):
    """Draws the branded header bar and footer on every page."""
    canvas.saveState()
    width, height = A4

    # Top navy band
    canvas.setFillColor(IFC_NAVY)
    canvas.rect(0, height - 18 * mm, width, 18 * mm, stroke=0, fill=1)

    # Teal accent stripe
    canvas.setFillColor(IFC_TEAL)
    canvas.rect(0, height - 20 * mm, width, 2 * mm, stroke=0, fill=1)

    # Header text
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(18 * mm, height - 11 * mm, "IFC Prospect Lookup")
    canvas.setFont("Helvetica", 9)
    canvas.drawString(18 * mm, height - 16 * mm, "Credit Risk Assessment")

    canvas.setFont("Helvetica", 8.5)
    canvas.drawRightString(
        width - 18 * mm, height - 13 * mm,
        f"Generated {datetime.now().strftime('%d %b %Y · %H:%M')}"
    )

    # Footer
    canvas.setFillColor(IFC_MUTED)
    canvas.setFont("Helvetica", 7.5)
    footer_y = 10 * mm
    canvas.drawCentredString(
        width / 2, footer_y,
        "International Furan Chemicals — Internal Use Only · "
        "Not a substitute for formal credit bureau reports."
    )
    canvas.drawRightString(width - 18 * mm, footer_y, f"Page {doc.page}")

    canvas.restoreState()


# ── Block builders ────────────────────────────────────────────

def _verdict_banner(result, styles) -> Table:
    label, colour = _verdict_style(result.get("verdict", ""))
    score = result.get("score", 0)

    cell = [
        Paragraph(label, styles["verdict"]),
        Paragraph(f"Score {score}/100", styles["verdict_sub"]),
    ]
    t = Table([[cell]], colWidths=[174 * mm], rowHeights=[20 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colour),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _company_header(selected: dict, sd: dict, styles) -> list:
    """Company name + identity strip."""
    name = sd.get("name") or selected.get("name", "—")
    jurisdiction = selected.get("jurisdiction") or "—"
    reg  = selected.get("company_number") or "—"
    inc  = selected.get("incorporation_date") or "—"
    typ  = selected.get("company_type") or "—"
    addr = selected.get("registered_address") or "—"

    blocks = [
        Paragraph(name, styles["h1"]),
        Paragraph(
            f"{jurisdiction} · Reg {reg} · {typ} · Incorporated {inc}",
            styles["subtitle"],
        ),
        Paragraph(addr, styles["muted"]),
    ]
    return blocks


def _flags_block(result, styles) -> list | None:
    """Red/amber flag strip. Returns None if no flags."""
    hard = result.get("hard_flags", []) or []
    soft = result.get("soft_flags", []) or []
    if not hard and not soft:
        return None

    items = []
    for f in hard:
        items.append(Paragraph(f"■  <b>{f}</b>", styles["flag_red"]))
    for f in soft:
        items.append(Paragraph(f"■  {f}", styles["flag_amber"]))

    t = Table([[items]], colWidths=[174 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), IFC_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, IFC_LIGHT_GREY),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [Paragraph("Key flags", styles["h2"]), t]


def _signals_table(signals: dict, styles) -> Table:
    """Two-column signal breakdown with coloured dots."""
    # Header row as plain strings — lets TableStyle TEXTCOLOR apply
    rows = [["Signal", "Value", "Points"]]
    for factor, (label, pts, max_pts) in signals.items():
        pct = (pts / max_pts) if max_pts else 0
        dot_colour = (
            VERDICT_GREEN if pct >= 0.75 else
            VERDICT_AMBER if pct >= 0.45 else
            VERDICT_RED
        )
        dot_html = f'<font color="{dot_colour.hexval()}">●</font>'
        rows.append([
            Paragraph(f"{dot_html}&nbsp;&nbsp;{factor}", styles["body"]),
            Paragraph(str(label), styles["body"]),
            Paragraph(f"{pts}/{max_pts}", styles["body"]),
        ])

    t = Table(rows, colWidths=[55 * mm, 90 * mm, 29 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), IFC_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, IFC_LIGHT]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _financials_block(fin: dict, styles) -> list:
    """Revenue trend table (if UK + iXBRL) OR a single-figure row."""
    out = [Paragraph("Financials", styles["h2"])]

    source = fin.get("source", "") if fin.get("found") else ""
    if source:
        out.append(Paragraph(f"Source: {source}", styles["muted"]))
        out.append(Spacer(1, 4))

    trend = fin.get("trend") or []
    if len(trend) >= 2:
        # Multi-year trend table: one column per year
        header_row = ["Metric"] + [f"FY{t.get('fiscal_year','—')}" for t in trend]
        rev_row    = ["Revenue"]     + [_fmt_money_gbp(t.get("revenue_gbp"))    for t in trend]
        rev_eur    = ["Revenue (€)"] + [_fmt_money_eur(t.get("revenue_eur"))    for t in trend]
        profit_row = ["Net profit"]  + [_fmt_money_gbp(t.get("net_profit_gbp")) for t in trend]

        rows = [header_row, rev_row, rev_eur, profit_row]
        col_count = len(trend) + 1
        col_width = 174 / col_count * mm
        t = Table(rows, colWidths=[col_width] * col_count)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), IFC_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9.5),
            ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 1), (0, -1), IFC_TEXT),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, IFC_LIGHT]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("FONTSIZE", (0, 1), (-1, -1), 9.5),
        ]))
        out.append(t)

        yoy = fin.get("yoy_pct")
        trend_label = fin.get("trend_label", "")
        if yoy is not None:
            arrow = {"growing": "▲", "declining": "▼", "flat": "▬"}.get(trend_label, "")
            out.append(Spacer(1, 4))
            out.append(Paragraph(
                f"<b>{arrow} Year-over-year: {yoy:+.1%}</b> ({trend_label})",
                styles["body"],
            ))
    else:
        # Single-figure fallback — what came from the latest filing or manual entry
        rev = fin.get("revenue")
        net = fin.get("net_income")
        fy  = fin.get("fiscal_year") or "—"
        rows = [
            ["Metric", "Value"],
            [f"Revenue FY{fy}",   _fmt_money_eur(rev)],
            [f"Net result FY{fy}", _fmt_money_eur(net)],
        ]
        t = Table(rows, colWidths=[80 * mm, 94 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), IFC_NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, IFC_LIGHT]),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ]))
        out.append(t)

    if fin.get("currency_note"):
        out.append(Spacer(1, 3))
        out.append(Paragraph(fin["currency_note"], styles["muted"]))
    if fin.get("filing_url"):
        out.append(Paragraph(
            f'<a href="{fin["filing_url"]}" color="#2e7d8f">View original filing on Companies House</a>',
            styles["muted"],
        ))

    return out


def _identity_table(sd: dict, selected: dict, styles) -> Table:
    """Two-column key/value identity block."""
    lei_val = sd.get("lei") or "Not found"
    lei_status = sd.get("lei_status") or ""
    lei_display = f"{lei_val}" + (f" ({lei_status})" if lei_val != "Not found" else "")

    rows = [
        ["Legal name",     sd.get("name") or selected.get("name", "—")],
        ["Legal form",     selected.get("company_type") or "—"],
        ["Status",         selected.get("status") or "—"],
        ["Jurisdiction",   f"{sd.get('country','—')} — tier {sd.get('country_tier','?')}"],
        ["Incorporation",  selected.get("incorporation_date") or "—"],
        ["LEI",            lei_display],
        ["Address",        selected.get("registered_address") or "—"],
    ]
    # Wrap values in Paragraphs so long addresses wrap
    rows = [[Paragraph(f"<b>{k}</b>", styles["body"]),
             Paragraph(str(v), styles["body"])] for k, v in rows]

    t = Table(rows, colWidths=[40 * mm, 134 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, IFC_LIGHT]),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBEFORE", (0, 0), (0, -1), 2, IFC_TEAL),
    ]))
    return t


def _news_block(news: dict, styles) -> list | None:
    if not news.get("found") or not news.get("articles"):
        return None
    items = [Paragraph("Recent news", styles["h2"])]
    for a in news["articles"][:5]:
        title = a.get("title", "")
        pub   = a.get("published", "")
        url   = a.get("url", "")
        dot_colour = VERDICT_RED if a.get("sentiment") == "negative" else IFC_MUTED
        dot = f'<font color="{dot_colour.hexval()}">●</font>'
        link = f'<a href="{url}" color="#2e7d8f">{title}</a>' if url else title
        items.append(Paragraph(
            f'{dot}&nbsp;&nbsp;{link} <font color="#6b7785">· {pub}</font>',
            styles["body"],
        ))
        items.append(Spacer(1, 2))
    return items


# ── Main entry point ──────────────────────────────────────────

def build_pdf(
    company_name: str,
    selected: dict,
    result: dict,
    data: dict,
    narrative: str,
) -> bytes:
    """
    Build the full credit-memo PDF and return it as bytes.

    Parameters
    ----------
    company_name : the search term the user typed
    selected     : OpenCorporates company dict that was picked
    result       : output of compute_score() — verdict, signals, flags, summary_data
    data         : the partial dict of all fetched sources (oc, gleif, sanctions,
                   financials, ch_profile, news)
    narrative    : the plain-language summary string already produced for the UI
    """
    buf = BytesIO()

    # Leave room at the top for the header band (20mm + a little)
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=26 * mm, bottomMargin=16 * mm,
        title=f"Credit Assessment — {company_name}",
        author="IFC Prospect Lookup",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="main",
    )
    doc.addPageTemplates([
        PageTemplate(id="withChrome", frames=[frame], onPage=_page_chrome),
    ])

    styles = _styles()
    story: list = []

    # 1. Company header
    story += _company_header(selected, result.get("summary_data", {}), styles)
    story.append(Spacer(1, 6))

    # 2. Verdict banner
    story.append(_verdict_banner(result, styles))
    story.append(Spacer(1, 10))

    # 3. Narrative summary — keep together so it doesn't orphan
    narrative_block = [
        Paragraph("Assessment", styles["h2"]),
        Paragraph(_md_to_rl(narrative), styles["body"]),
    ]
    story.append(KeepTogether(narrative_block))
    story.append(Spacer(1, 6))

    # 4. Flags (optional)
    flags = _flags_block(result, styles)
    if flags:
        story += flags
        story.append(Spacer(1, 4))

    # 5. Financials
    fin = data.get("financials", {}) or {}
    story += _financials_block(fin, styles)
    story.append(Spacer(1, 6))

    # 6. Signals table
    story.append(Paragraph("Signal breakdown", styles["h2"]))
    story.append(_signals_table(result.get("signals", {}), styles))
    story.append(Spacer(1, 6))

    # 7. Identity
    story.append(Paragraph("Company identity", styles["h2"]))
    story.append(_identity_table(result.get("summary_data", {}), selected, styles))
    story.append(Spacer(1, 6))

    # 8. News
    news_block = _news_block(data.get("news", {}) or {}, styles)
    if news_block:
        story += news_block
        story.append(Spacer(1, 6))

    # 9. Sources footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=IFC_LIGHT_GREY,
                            spaceBefore=4, spaceAfter=4))
    story.append(Paragraph(
        "Sources: OpenCorporates · GLEIF · Companies House · "
        "Financial Modeling Prep · NewsData.io · EU Consolidated Sanctions List · "
        "ECB reference rates",
        styles["muted"],
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
