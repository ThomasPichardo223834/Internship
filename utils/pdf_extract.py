"""
utils/pdf_extract.py
PDF financial-figure extraction for UK Companies House filings.

Scope note: intentionally UK-only. UK accounts follow FRS 102/105 reporting
standards which makes them reasonably predictable. Extraction for Dutch, German
or other non-UK annual reports is explicitly out of scope.

Strategy (in priority order):
  1. TABLE EXTRACTION: pdfplumber.extract_tables() finds the P&L as a structured
     table. Look for a row whose label cell matches turnover/profit patterns,
     then pull the first meaningful numeric column (current year, since UK
     accounts are formatted "Label | Note | CY | PY").
  2. SECTION-RESTRICTED TEXT: extract text from the P&L section only (between
     "Statement of Comprehensive Income" / "Profit and Loss Account" headings
     and the next section). Avoids picking up stray "Turnover" mentions from
     KPI tables, GHG emissions tables, accounting policy narrative.
  3. FULL TEXT FALLBACK: line-by-line label matching across the whole document,
     used only when the structured approaches find nothing. Tracks "skip zones"
     to avoid matching label-looking lines in misleading sections.

The tiered approach handles three real failure modes observed in the test set:
  - "Turnover 3 486,229 518,120" with a Note column between label and value
  - "Turnover (£M) 486 / 518" in the GHG intensity ratio table (wrong unit)
  - "1.6 Turnover" as an accounting policy heading (no numbers, but distracting)
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pdfplumber
import requests


# ── Label patterns ────────────────────────────────────────────

TURNOVER_PATTERNS = [
    # Plain "Turnover" but NOT "Turnover (£M)" (that's an intensity ratio in GHG tables)
    r"^\s*turnover\b(?!\s*\(?\s*£\s*m)",
    r"^\s*total\s+turnover\b",
    r"^\s*revenue\b(?!\s+recognition)",
    r"^\s*total\s+revenue\b",
    r"^\s*sales\s*(?:revenue)?\s*(?:\(note[^)]*\))?\s*$",
    r"^\s*gross\s+turnover\b",
]

PROFIT_PATTERNS = [
    r"^\s*profit\s*(?:/\s*\(loss\))?\s+(?:on\s+ordinary\s+activities\s+)?before\s+taxation\b",
    r"^\s*loss\s+(?:on\s+ordinary\s+activities\s+)?before\s+taxation\b",
    r"^\s*profit\s+before\s+tax(?:ation)?\b",
    r"^\s*loss\s+before\s+tax(?:ation)?\b",
    r"^\s*profit\s*(?:/\s*\(loss\))?\s+(?:for\s+the\s+(?:year|period|financial\s+year))\b",
    r"^\s*loss\s+(?:for\s+the\s+(?:year|period|financial\s+year))\b",
    r"^\s*profit\s*(?:/\s*\(loss\))?\s+after\s+taxation\b",
    r"^\s*loss\s+after\s+taxation\b",
    r"^\s*net\s+profit\s*(?:/\s*\(loss\))?\b",
    r"^\s*net\s+loss\b",
    r"^\s*operating\s+profit\s*(?:/\s*\(loss\))?\b",
    r"^\s*operating\s+loss\b",
]

PL_SECTION_HEADINGS = [
    r"statement\s+of\s+comprehensive\s+income",
    r"statement\s+of\s+profit\s+(?:or\s+loss|and\s+loss)",
    r"profit\s+and\s+loss\s+account",
    r"income\s+statement",
]

NEXT_SECTION_HEADINGS = [
    r"balance\s+sheet",
    r"statement\s+of\s+financial\s+position",
    r"statement\s+of\s+changes\s+in\s+equity",
    r"statement\s+of\s+cash\s+flows?",
    r"cash\s+flow\s+statement",
    r"notes\s+to\s+the\s+financial\s+statements",
]

SKIP_SECTION_HEADINGS = [
    r"greenhouse\s+gas",
    r"\bghg\s+emissions\b",
    r"streamlined\s+energy",
    r"\bsecr\b",
    r"key\s+performance\s+indicators?",
    r"intensity\s+ratio",
    r"^\s*\d+\.?\d*\s+turnover\s*$",
    r"revenue\s+recognition",
    r"turnover\s+recognition",
]

SCALE_PATTERNS = [
    (re.compile(r"£\s*['\u2019]?\s*000|\bin\s+thousands\b|\(£'?000s?\)|\(£000s?\)", re.IGNORECASE), 1_000),
    (re.compile(r"\bin\s+millions\b|\(£m\)|\(£'?millions?\)|£\s*millions?\b|\bin\s+£m\b|£m\b", re.IGNORECASE), 1_000_000),
]

NUMBER_RE = re.compile(r"""
    (?:^|\s)
    (
       \(?\-?
       (?:£\s*)?
       \d{1,3}(?:[,\s]\d{3})+
       (?:\.\d+)?
       \)?
    )
    (?=\s|$)
""", re.VERBOSE)

NUMBER_RE_SIMPLE = re.compile(r"""
    (?:^|\s)
    (
       \(?\-?
       (?:£\s*)?
       \d{4,}
       (?:\.\d+)?
       \)?
    )
    (?=\s|$)
""", re.VERBOSE)


@dataclass
class ExtractionResult:
    found: bool
    revenue_gbp: float | None = None
    net_profit_gbp: float | None = None
    scale_detected: int = 1
    scale_label: str = "units"
    revenue_line: str = ""
    profit_line: str = ""
    raw_text: str = ""
    error: str = ""
    page_count: int = 0
    extraction_method: str = ""


# ── Helpers ───────────────────────────────────────────────────

def _parse_accounts_number(raw: str) -> float | None:
    if not raw:
        return None
    s = raw.strip()
    is_paren_neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace("£", "").replace(",", "").replace(" ", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    if is_paren_neg:
        v = -abs(v)
    return v


def _detect_scale(text: str) -> tuple[int, str]:
    head = text[:3000]
    for pattern, mult in SCALE_PATTERNS:
        if pattern.search(head):
            return mult, "thousands" if mult == 1_000 else "millions"
    return 1, "units"


def _extract_numbers_from_text(text: str) -> list[float]:
    nums = []
    for m in NUMBER_RE.finditer(text):
        v = _parse_accounts_number(m.group(1))
        if v is not None:
            nums.append(v)
    if not nums:
        for m in NUMBER_RE_SIMPLE.finditer(text):
            v = _parse_accounts_number(m.group(1))
            if v is not None:
                nums.append(v)
    return nums


def _label_matches(text: str, patterns: list[str]) -> int | None:
    s = text.strip()
    if len(s) < 3:
        return None
    for rank, pat in enumerate(patterns):
        if re.match(pat, s, re.IGNORECASE):
            return rank
    return None


# ── Strategy 1: table extraction ──────────────────────────────

def _try_table_extraction(pdf) -> tuple[float | None, str, float | None, str]:
    """
    Walk every table on every page, look for rows whose first cell matches
    turnover/profit label patterns, take the first meaningful numeric value
    (= current year in UK CY/PY layout).
    """
    best_turnover = (999, 0.0, "")   # (rank, value, source)
    best_profit   = (999, 0.0, "")

    for page in pdf.pages:
        try:
            tables = page.extract_tables() or []
        except Exception:
            tables = []

        for table in tables:
            if not table or not table[0]:
                continue

            for row in table:
                if not row:
                    continue
                label_cell = ""
                value_cells = []
                for cell in row:
                    if cell is None:
                        continue
                    cell_s = str(cell).strip()
                    if not cell_s:
                        continue
                    if not label_cell:
                        label_cell = cell_s
                    else:
                        value_cells.append(cell_s)

                if not label_cell or not value_cells:
                    continue

                row_numbers: list[float] = []
                for v in value_cells:
                    row_numbers.extend(_extract_numbers_from_text(v))

                if not row_numbers:
                    continue

                meaningful = [n for n in row_numbers if abs(n) >= 100]
                if not meaningful:
                    continue

                value = meaningful[0]

                t_rank = _label_matches(label_cell, TURNOVER_PATTERNS)
                if t_rank is not None and t_rank < best_turnover[0]:
                    best_turnover = (t_rank, value, f"{label_cell} | {value_cells}")

                p_rank = _label_matches(label_cell, PROFIT_PATTERNS)
                if p_rank is not None and p_rank < best_profit[0]:
                    best_profit = (p_rank, value, f"{label_cell} | {value_cells}")

    turnover_val = best_turnover[1] if best_turnover[0] < 999 else None
    profit_val   = best_profit[1]   if best_profit[0]   < 999 else None
    return turnover_val, best_turnover[2], profit_val, best_profit[2]


# ── Strategy 2: section-restricted text ───────────────────────

def _find_pl_section(full_text: str) -> str | None:
    lines = full_text.splitlines()
    pl_start_idx = None
    for i, line in enumerate(lines):
        clean = line.strip().lower()
        for pat in PL_SECTION_HEADINGS:
            if re.search(pat, clean):
                pl_start_idx = i
                break
        if pl_start_idx is not None:
            break

    if pl_start_idx is None:
        return None

    pl_end_idx = len(lines)
    for i in range(pl_start_idx + 1, len(lines)):
        clean = lines[i].strip().lower()
        for pat in NEXT_SECTION_HEADINGS:
            if re.search(pat, clean):
                pl_end_idx = i
                break
        if pl_end_idx != len(lines):
            break

    return "\n".join(lines[pl_start_idx:pl_end_idx])


def _in_skip_zone(line: str) -> bool:
    s = line.strip().lower()
    for pat in SKIP_SECTION_HEADINGS:
        if re.search(pat, s):
            return True
    return False


def _find_in_text_smart(text: str, patterns: list[str]) -> tuple[float | None, str]:
    """
    Line-by-line label matching with skip-zone tracking.
    Numbers can be on the same line OR the next 1-2 lines (table wrap).
    """
    best_rank = len(patterns) + 1
    best_value: float | None = None
    best_line = ""

    lines = text.splitlines()
    skip_until_next_section = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if _in_skip_zone(stripped):
            skip_until_next_section = True
            continue
        if skip_until_next_section:
            all_headings = PL_SECTION_HEADINGS + NEXT_SECTION_HEADINGS
            for pat in all_headings:
                if re.search(pat, stripped.lower()):
                    skip_until_next_section = False
                    break
            if skip_until_next_section:
                continue

        if len(stripped) < 3:
            continue

        rank = _label_matches(stripped, patterns)
        if rank is None:
            continue

        nums = _extract_numbers_from_text(stripped)
        if not [n for n in nums if abs(n) >= 100]:
            for j in range(i + 1, min(i + 3, len(lines))):
                more = _extract_numbers_from_text(lines[j])
                nums.extend(more)
                if [n for n in more if abs(n) >= 100]:
                    break

        candidates = [n for n in nums if abs(n) >= 100]
        if candidates and rank < best_rank:
            best_rank = rank
            best_value = candidates[0]
            best_line = stripped

    return best_value, best_line


# ── Main entry point ──────────────────────────────────────────

def extract_from_pdf(pdf_bytes: bytes) -> ExtractionResult:
    if not pdf_bytes:
        return ExtractionResult(found=False, error="Empty PDF data")

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = pdf.pages
            page_count = len(pages)
            if page_count == 0:
                return ExtractionResult(found=False, error="PDF has no pages")

            full_text_parts = []
            for p in pages:
                t = p.extract_text() or ""
                full_text_parts.append(t)
            raw_text = "\n".join(full_text_parts)

            if not raw_text.strip():
                return ExtractionResult(
                    found=False,
                    error="PDF contains no extractable text — likely a scanned image. OCR would be required.",
                    page_count=page_count,
                )

            scale, scale_label = _detect_scale(raw_text)

            # Strategy 1: tables
            t_raw, t_src, p_raw, p_src = _try_table_extraction(pdf)
            method = "table" if (t_raw is not None or p_raw is not None) else ""

            # Strategy 2: P&L section text
            if t_raw is None or p_raw is None:
                pl_text = _find_pl_section(raw_text)
                if pl_text:
                    if t_raw is None:
                        v, src = _find_in_text_smart(pl_text, TURNOVER_PATTERNS)
                        if v is not None:
                            t_raw, t_src = v, src
                            method = method or "section"
                    if p_raw is None:
                        v, src = _find_in_text_smart(pl_text, PROFIT_PATTERNS)
                        if v is not None:
                            p_raw, p_src = v, src
                            method = method or "section"

            # Strategy 3: full text fallback
            if t_raw is None:
                v, src = _find_in_text_smart(raw_text, TURNOVER_PATTERNS)
                if v is not None:
                    t_raw, t_src = v, src
                    method = method or "fulltext"
            if p_raw is None:
                v, src = _find_in_text_smart(raw_text, PROFIT_PATTERNS)
                if v is not None:
                    p_raw, p_src = v, src
                    method = method or "fulltext"

    except Exception as e:
        return ExtractionResult(found=False, error=f"PDF parse failed: {e}")

    revenue_gbp = t_raw * scale if t_raw is not None else None
    profit_gbp  = p_raw * scale if p_raw is not None else None

    if revenue_gbp is not None and abs(revenue_gbp) < 100:
        revenue_gbp = None
    if profit_gbp is not None and abs(profit_gbp) < 100:
        profit_gbp = None

    return ExtractionResult(
        found=(revenue_gbp is not None) or (profit_gbp is not None),
        revenue_gbp=revenue_gbp,
        net_profit_gbp=profit_gbp,
        scale_detected=scale,
        scale_label=scale_label,
        revenue_line=t_src,
        profit_line=p_src,
        raw_text=raw_text,
        page_count=page_count,
        extraction_method=method or "none",
    )


# ── Auto-fetch the PDF from Companies House ──────────────────

def fetch_pdf_from_companies_house(filing_item: dict, api_key: str) -> bytes | None:
    if not api_key:
        return None
    meta_url = (filing_item.get("links") or {}).get("document_metadata", "")
    if not meta_url:
        return None
    try:
        r1 = requests.get(meta_url, auth=(api_key, ""), timeout=10)
        if r1.status_code != 200:
            return None
        meta = r1.json()
        resources = meta.get("resources", {}) or {}
        if "application/pdf" not in resources:
            return None
        doc_url = (meta.get("links") or {}).get("document", "")
        if not doc_url:
            return None
        r2 = requests.get(
            doc_url,
            auth=(api_key, ""),
            headers={"Accept": "application/pdf"},
            timeout=30,
        )
        r2.raise_for_status()
        if r2.headers.get("Content-Type", "").startswith("application/pdf"):
            return r2.content
        if r2.content[:4] == b"%PDF":
            return r2.content
        return None
    except Exception:
        return None
