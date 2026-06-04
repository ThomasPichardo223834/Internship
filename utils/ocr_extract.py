"""
utils/ocr_extract.py
OCR-based PDF extraction for scanned Companies House filings.

USE CASE: when a user uploads a scanned (image-only) PDF that pdfplumber
returned no text from. OCR is opt-in (user action), explicitly warned about,
and produces clearly-flagged "verify before use" output.

IMPORTANT: this module is intentionally NOT used for automatic batch extraction
(it would be too slow and the user wouldn't know the figure is OCR-derived).
It is only invoked from the upload widget after the user has chosen to wait.

Strategy:
  1. Convert PDF pages to images (pdf2image / poppler)
  2. Search for the P&L page by OCR'ing pages in priority chunks
     (typical annual reports place the P&L mid-document, after the auditor's
     report) — stops as soon as a P&L page is identified
  3. Re-OCR the identified page at higher DPI for accurate digit recognition
  4. Apply ranked regex patterns to extract Turnover and Profit-before-tax

Time budget on a 48-page annual report: ~60-100 seconds end-to-end.
Time budget on a 10-15 page small-company filing: ~20-30 seconds.

Returns the same shape as utils.pdf_extract.ExtractionResult so the caller
doesn't need to special-case the OCR path.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import sys
from dataclasses import dataclass

# These imports happen at module import. If any is missing on the deployment
# environment, OCR will be disabled and the upload handler will fall back
# to a clear "OCR not available" message instead of crashing.
try:
    from pdf2image import convert_from_bytes
    import pytesseract
    OCR_AVAILABLE = True
    OCR_IMPORT_ERROR = ""
except ImportError as e:
    convert_from_bytes = None  # type: ignore
    pytesseract = None          # type: ignore
    OCR_AVAILABLE = False
    OCR_IMPORT_ERROR = str(e)


# ── Platform-specific binary path detection ──────────────────
# On Linux (Streamlit Cloud), tesseract and poppler are installed via packages.txt
# and are on PATH automatically. On Windows local development, they often aren't
# on PATH, so we look in common install locations and configure the libraries
# to use them explicitly. This means the same code works in both environments
# without needing users to edit PATH.

POPPLER_PATH: str | None = None  # passed to convert_from_bytes() if not on PATH

def _configure_windows_binaries() -> None:
    """On Windows, detect Tesseract and Poppler install locations if they
    aren't already on PATH. Sets pytesseract.tesseract_cmd and the module-level
    POPPLER_PATH so the rest of the module can pass it to pdf2image."""
    global POPPLER_PATH

    if sys.platform != "win32" or not OCR_AVAILABLE:
        return

    # ── Tesseract ─────────────────────────────────────────────
    # If tesseract.exe is already on PATH, shutil.which will find it.
    if shutil.which("tesseract") is None:
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break

    # ── Poppler ───────────────────────────────────────────────
    # pdftoppm.exe is the binary pdf2image actually calls.
    if shutil.which("pdftoppm") is None:
        # Search common Poppler install locations. The bin folder is what
        # pdf2image needs (we pass it as poppler_path).
        candidates = [
            r"C:\Program Files\poppler\Library\bin",
            r"C:\Program Files\poppler\bin",
            r"C:\poppler\Library\bin",
            r"C:\poppler\bin",
            os.path.expandvars(r"%LOCALAPPDATA%\poppler\Library\bin"),
            os.path.expandvars(r"%USERPROFILE%\Downloads\poppler\Library\bin"),
        ]
        # Also try wildcarded versions: C:\poppler-XX.X.X\Library\bin
        import glob
        for root in [r"C:\\", os.path.expandvars(r"%USERPROFILE%\Downloads"),
                     os.path.expandvars(r"%LOCALAPPDATA%")]:
            for pattern in [r"poppler-*\Library\bin", r"poppler-*\bin"]:
                matches = glob.glob(os.path.join(root, pattern))
                candidates.extend(matches)

        for path in candidates:
            if os.path.isdir(path) and os.path.isfile(os.path.join(path, "pdftoppm.exe")):
                POPPLER_PATH = path
                break


# Run detection once at import time
_configure_windows_binaries()


# ── Patterns ──────────────────────────────────────────────────

PL_HEADING_PATTERNS = [
    r"statement\s+of\s+comprehensive\s+income",
    r"statement\s+of\s+profit\s+(?:or\s+loss|and\s+loss)",
    r"profit\s+and\s+loss\s+account",
    r"income\s+statement",
    r"consolidated\s+income\s+statement",
    r"statement\s+of\s+income(?:\s+and\s+retained\s+earnings)?",
]

# Distinct line labels that appear on a real P&L. We require >=2 to qualify
# a page (filters out audit-opinion pages which mention 'income statement'
# in prose but don't contain financial line items).
PL_LINE_LABELS = [
    r"\bturnover\b",
    r"\brevenue[s]?\b",
    r"\bsales\b",
    r"\bcost\s+of\s+(?:sales|goods\s+sold|materials)\b",
    r"\bgross\s+profit\b",
    r"\boperating\s+(?:profit|loss|expenses)\b",
    r"\badministrative\s+(?:expenses|income)\b",
    r"\bdistribution\s+costs\b",
    r"\bprofit\s+before\s+tax(?:ation)?\b",
    r"\bloss\s+before\s+tax(?:ation)?\b",
    r"\btax\s+on\s+profit\b",
    r"\bprofit\s+(?:for\s+the\s+(?:year|period|financial\s+year))\b",
    r"\bloss\s+(?:for\s+the\s+(?:year|period|financial\s+year))\b",
    r"\bstaff\s+costs\b",
    r"\bdepreciation\s+and\s+amortisation\b",
    r"\binterest\s+(?:receivable|payable)\b",
]

# Markers that indicate the filing is NOT in GBP — typically a foreign parent
# company filing or a multi-currency consolidated report. When detected, we
# route the user to manual entry rather than attempting an incorrect GBP→EUR
# conversion on figures already in another currency.
NON_GBP_MARKERS = [
    r"in\s+million\s+euros?",
    r"in\s+thousand\s+euros?",
    r"\beuros?\s+millions?\b",
    r"\beur\s+millions?\b",
    r"€\s*million",
    r"€'?000",
    # USD markers (Natara's report is in USD even though UK-registered).
    # OCR mangles $'000 in several ways: $°000 (degree sign), $7000 (7 for '),
    # $|000 (pipe for '), or just spaced-out $ ' 000. Detect all variants.
    r"\$\s*(?:['\u2019\u00b0|7]\s*)?000",   # $'000, $°000, $|000, $7000, $ 000
    r"\$['\u2019\u00b0|7]?\s*000",
    r"\$\s*millions?\b",
    r"us\s*dollars?\b",
    r"\busd\s+(?:million|thousand|'?000)",
    # Specific multi-marker line (Natara's case): "$°000 $7000 $7000..."
    r"\$[\u00b07|'\u2019]\s*0\s*0\s*0(?:\s+\$)",
    # German/Dutch/French parent indicators
    r"konzern(?:abschluss|gewinn)",
    r"jahresabschluss",
    r"compte\s+de\s+r[eé]sultat",
    r",\s*korschenbroich",
]
NON_GBP_RE = re.compile("|".join(NON_GBP_MARKERS), re.IGNORECASE)

# Turnover patterns — handle "Turnover [optional note ref] [CY] [optional PY]"
# Group 1 = current year, group 2 = prior year (if present)
TURNOVER_OCR_PATTERNS = [
    # Most specific: with note column and both years
    r"turnover\s+\d{1,2}\s+([\d,]{4,})\s+([\d,]{4,})",
    # With note, single value (last filing)
    r"turnover\s+\d{1,2}\s+([\d,]{4,})()",
    # No note column, both years
    r"turnover\s+([\d,]{4,})\s+([\d,]{4,})",
    # Simplest
    r"turnover\s+([\d,]{4,})()",
    # Revenue variants
    r"revenue\s+\d{1,2}\s+([\d,]{4,})\s+([\d,]{4,})",
    r"revenue\s+\d{1,2}\s+([\d,]{4,})()",
    r"revenue\s+([\d,]{4,})\s+([\d,]{4,})",
    r"revenue\s+([\d,]{4,})()",
]

PROFIT_OCR_PATTERNS = [
    r"profit\s+before\s+tax(?:ation)?\s+\d{1,2}\s+([\d,]{4,})\s+([\d,]{4,})",
    r"profit\s+before\s+tax(?:ation)?\s+\d{1,2}\s+([\d,]{4,})()",
    r"profit\s+before\s+tax(?:ation)?\s+([\d,]{4,})\s+([\d,]{4,})",
    r"profit\s+before\s+tax(?:ation)?\s+([\d,]{4,})()",
    r"profit\s+(?:for\s+the\s+(?:year|financial\s+year))\s+\d{1,2}\s+([\d,]{4,})\s+([\d,]{4,})",
    r"profit\s+(?:for\s+the\s+(?:year|financial\s+year))\s+\d{1,2}\s+([\d,]{4,})()",
    r"profit\s+(?:for\s+the\s+(?:year|financial\s+year))\s+([\d,]{4,})\s+([\d,]{4,})",
    r"profit\s+(?:for\s+the\s+(?:year|financial\s+year))\s+([\d,]{4,})()",
]

# Fiscal year detection from page header.
# Annual reports header looks like "FOR THE YEAR ENDED 31 DECEMBER 2024"
# Also commonly find "2024 2023" on its own column-header line above values.
FISCAL_YEAR_HEADER_PATTERN = re.compile(
    r"(?:year\s+end(?:ed|ing)|period\s+end(?:ed|ing))[\s\w]*?(\d{4})",
    re.IGNORECASE,
)
FISCAL_YEAR_COLUMN_PATTERN = re.compile(
    r"^\s*(\d{4})\s+(\d{4})\s*$",
    re.MULTILINE,
)

SCALE_OCR_PATTERNS = [
    # £'000, £000, £ '000, OCR variants where ' becomes 7 or | or other chars
    (re.compile(
        r"""(?:
            £\s*['\u2019]?\s*000     |  # £'000 or £ '000
            \(£'?000s?\)             |  # (£000)
            £000                     |
            £\s*[7|]\s*000           |  # OCR error: £'000 -> £7000 / £|000
            £\s*['\u2019]?\s*0\s*0\s*0   # spaced-out variant
        )""",
        re.IGNORECASE | re.VERBOSE,
    ), 1_000),
    (re.compile(r"£\s*m\b|£\s*million", re.IGNORECASE), 1_000_000),
]


@dataclass
class OcrResult:
    """Same shape as pdf_extract.ExtractionResult for compatibility."""
    found: bool
    revenue_gbp: float | None = None
    net_profit_gbp: float | None = None
    # Prior-year figures from the comparative column. Annual reports always
    # show the prior year alongside the current year, so we get this for free
    # whenever the OCR is successful.
    revenue_gbp_prior: float | None = None
    net_profit_gbp_prior: float | None = None
    fiscal_year: str = ""        # e.g. "2024"
    fiscal_year_prior: str = ""  # e.g. "2023"
    scale_detected: int = 1
    scale_label: str = "units"
    revenue_line: str = ""
    profit_line: str = ""
    raw_text: str = ""
    error: str = ""
    page_count: int = 0
    extraction_method: str = "ocr"
    ocr_page: int = 0
    ocr_warnings: list = None


def _parse_number(raw: str) -> float | None:
    if not raw:
        return None
    s = raw.replace(",", "").replace(" ", "").strip()
    s = s.replace("£", "").strip("()")
    try:
        return float(s)
    except ValueError:
        return None


def _detect_scale(text: str) -> tuple[int, str]:
    head = text[:3000]
    for pattern, mult in SCALE_OCR_PATTERNS:
        if pattern.search(head):
            return mult, "thousands" if mult == 1_000 else "millions"
    return 1, "units"


def _find_pl_page(pdf_bytes: bytes, page_range: tuple[int, int], dpi: int = 150) -> tuple[int | None, str]:
    """
    OCR pages in the given inclusive range, return (1-indexed page number, full
    text) of the first page that looks like a real P&L.

    A page qualifies if BOTH:
      (a) one of PL_HEADING_PATTERNS matches in the FIRST 8 lines (heading,
          not body-text reference)
      (b) the page contains AT LEAST 2 distinct P&L line labels from
          PL_LINE_LABELS — this filters out:
            • auditor's report pages that mention "income statement" in prose
            • TOC pages that list the heading without showing content
            • Camida-like cases of P&L pages without revenue still being valid
              (since we count 'operating profit', 'profit for the year' etc.
              as P&L line labels, a no-revenue P&L still passes)
    """
    start, end = page_range
    try:
        kwargs = {"dpi": dpi, "first_page": start, "last_page": end}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH
        images = convert_from_bytes(pdf_bytes, **kwargs)
    except Exception:
        return None, ""

    for offset, img in enumerate(images):
        try:
            text = pytesseract.image_to_string(img)
        except Exception:
            continue
        # Heading must be in first lines, not body
        first_lines = "\n".join(text.splitlines()[:8]).lower()
        if not any(re.search(kw, first_lines) for kw in PL_HEADING_PATTERNS):
            continue
        # Page must contain multiple P&L line items, not just a passing
        # mention of the heading in body text. Counts distinct labels so
        # turnover-less P&Ls (e.g. holding companies) still qualify via
        # other P&L lines like Operating profit, Tax on profit, etc.
        text_low = text.lower()
        distinct_labels = sum(1 for lbl in PL_LINE_LABELS if re.search(lbl, text_low))
        if distinct_labels < 2:
            continue
        return start + offset, text

    return None, ""


def _hi_dpi_extract(pdf_bytes: bytes, page_no: int, dpi: int = 250) -> str:
    """Re-OCR a single page at higher DPI for accurate digit recognition."""
    try:
        kwargs = {"dpi": dpi, "first_page": page_no, "last_page": page_no}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH
        images = convert_from_bytes(pdf_bytes, **kwargs)
        if not images:
            return ""
        return pytesseract.image_to_string(images[0])
    except Exception:
        return ""


def _detect_fiscal_years(text: str) -> tuple[str, str]:
    """
    Detect current and prior fiscal year from the OCR'd P&L page.

    Tries two strategies:
      1. Header pattern: 'FOR THE YEAR ENDED 31 DECEMBER 2024' → CY=2024, PY=2023
      2. Column-header line: a line containing just '2024 2023' → CY=2024, PY=2023

    Returns (current_year, prior_year) as strings, or ('', '') if neither found.
    """
    # Try the "year ended" header first
    m = FISCAL_YEAR_HEADER_PATTERN.search(text)
    if m:
        cy = m.group(1)
        try:
            py = str(int(cy) - 1)
        except ValueError:
            py = ""
        return cy, py

    # Fall back to scanning for a "2024 2023" style column header line
    m = FISCAL_YEAR_COLUMN_PATTERN.search(text)
    if m:
        cy, py = m.group(1), m.group(2)
        # Sanity check: years should be plausible and adjacent
        try:
            if abs(int(cy) - int(py)) == 1 and 1990 < int(cy) < 2100:
                return cy, py
        except ValueError:
            pass

    return "", ""


def _find_numbers_in_line(line: str) -> list[float]:
    """Pull all plausible numeric values from a single OCR'd line.

    Handles:
      - Comma-separated thousands: '486,229'
      - Decimal millions: '1,279.2'
      - Parenthesised negatives: '(231)'
      - Plain integers: '486229', or smaller like '231'

    Deliberately does NOT treat spaces as thousands-separators, because that
    would cause 'Turnover 3 486,229 518,120' to collapse into one giant number.
    If OCR loses commas the values just appear as plain integers and we still
    catch them via the \\d{2,} branch.
    """
    pat = r"""
        \(?                          # optional opening paren (for negatives)
        -?                           # optional minus sign
        (?:
            \d{1,3}(?:,\d{3})+       # 1-3 digits + comma-separated groups of 3
            (?:\.\d{1,3})?
            |
            \d{2,}                   # OR 2+ plain digits (catches small values)
            (?:\.\d{1,3})?
        )
        \)?                          # optional closing paren
    """
    out = []
    for m in re.finditer(pat, line, re.VERBOSE):
        raw = m.group(0)
        is_neg = raw.startswith("(") and raw.endswith(")")
        cleaned = raw.strip("()").replace(",", "")
        try:
            v = float(cleaned)
        except ValueError:
            continue
        if is_neg:
            v = -v
        out.append(v)
    return out


# ── Unified label detection ───────────────────────────────────
# Rather than enumerating every possible regex variant of every P&L label, we
# detect labels by checking if a line contains the relevant keywords (in order
# of specificity). Ordered by priority: more specific labels first.

LABEL_TURNOVER_RE = re.compile(
    r"^(?:total\s+)?(?:turnover|revenue[s]?|sales\s+revenues?|net\s+sales|net\s+revenue)\b",
    re.IGNORECASE,
)

# Profit labels in priority order — first hit wins
PROFIT_LABEL_PRIORITIES = [
    re.compile(r"profit\s+before\s+tax(?:ation)?", re.IGNORECASE),
    re.compile(r"loss\s+before\s+tax(?:ation)?", re.IGNORECASE),
    re.compile(r"(?:profit|loss)(?:\s*/\s*\(loss\))?\s+for\s+the\s+(?:year|period|financial\s+year)", re.IGNORECASE),
    re.compile(r"net\s+(?:profit|loss|income)", re.IGNORECASE),
    re.compile(r"operating\s+(?:profit|loss)", re.IGNORECASE),
]

# Lines that LOOK like labels but should be skipped — these contain financial
# keywords but aren't the figures we want.
SKIP_LABEL_RE = re.compile(
    r"^(?:"
    r"raw\s+materials|cost\s+of|other\s+(?:operating|external)|staff\s+costs|"
    r"depreciation|amortisation|interest|tax\s+on\s+profit|exchange|"
    r"gross\s+profit|distribution\s+costs|administrative|"
    r"income\s+from|other\s+income|impairment|non[\s-]?recurring|"
    r"comprehensive\s+income|total\s+comprehensive|dividends?|"
    r"earnings?\s+per\s+share"
    r")\b",
    re.IGNORECASE,
)


def _is_year_header_line(line: str) -> bool:
    """Lines like '2024 2023' or 'Year to 31 December 2024' or '£000 £000'
    or just 'Note' / '£' / '$' / '€' / a 1-2 digit note ref."""
    s = line.strip()
    if not s:
        return True
    s_low = s.lower()
    if s in ("£", "$", "€"):
        return True
    if s_low == "note":
        return True
    if re.match(r"^\d{1,2}$", s):  # bare "3" or "23" note reference
        return True
    if re.match(r"^(?:£|stg|eur|€|\$|usd|gbp)\b", s_low):
        return True
    if re.match(r"^(?:note\s+)?(?:£|stg|eur|€)", s_low):
        return True
    if re.match(r"^(?:year(?:s)?\s+to|year(?:s)?\s+ended|period|months?\s+to|column|underlying|exceptional|total)\b", s_low):
        return True
    if re.match(r"^\d{4}(?:\s+\d{4})+$", s):
        return True
    if re.match(r"^\d{4}$", s):
        return True
    return False


def _filter_note_refs(nums: list[float]) -> list[float]:
    """
    Drop leading small numbers that look like note references. UK accounts
    have a 'Note' column with 1-2 digit references (e.g. 'Turnover 3 486,229').
    Rule: if the first number is small (<=99) AND there are larger numbers
    after it (at least 5x bigger), drop the first one.
    """
    if not nums:
        return nums
    if abs(nums[0]) <= 99 and len(nums) >= 2:
        if abs(nums[1]) >= 5 * max(abs(nums[0]), 1):
            return nums[1:]
    return nums


def _find_value_for_label(lines: list[str], label_idx: int, allow_long_scan: bool = False) -> tuple[float | None, float | None, str]:
    """
    Given the line index where a label was found, return (current_year_value,
    prior_year_value, source_text).

    Strategy:
      1. Check the same line for numbers — Brenntag/Whitford-style
      2. If same line had nothing, walk forward up to 15 lines for the next
         numeric row, skipping year headers
      3. If we found only one CY value, look further for the PY value
      4. (allow_long_scan=True only) If no value found within 15 lines and the
         intermediate lines were all skip-labels or empty, keep going up to 60
         lines to handle layouts where the entire label column is OCR'd before
         the value column (OQEMA-style "labels-only" sections)
    """
    label_line = lines[label_idx]
    same_line_nums = _filter_note_refs(_find_numbers_in_line(label_line))

    def plausible(n: float, has_paren: bool) -> bool:
        if has_paren:
            return abs(n) >= 5
        return abs(n) >= 20

    has_paren = "(" in label_line
    usable_same = [n for n in same_line_nums if plausible(n, has_paren)]

    if len(usable_same) >= 2:
        return usable_same[0], usable_same[1], label_line.strip()

    if len(usable_same) == 1:
        cy = usable_same[0]
        for j in range(label_idx + 1, min(label_idx + 20, len(lines))):
            ln = lines[j]
            if LABEL_TURNOVER_RE.match(ln.strip()) or any(p.search(ln) for p in PROFIT_LABEL_PRIORITIES):
                break
            if _is_year_header_line(ln):
                continue
            if SKIP_LABEL_RE.match(ln.strip()):
                break
            nums = _filter_note_refs(_find_numbers_in_line(ln))
            has_p = "(" in ln
            usable = [n for n in nums if plausible(n, has_p)]
            if usable:
                return cy, usable[0], f"{label_line.strip()} | next: {ln.strip()}"
        return cy, None, label_line.strip()

    # Same-line had nothing. Walk forward looking for a value row.
    # Normal mode: stop at the next skip-label (means we're in a multi-line
    # value layout and the value is on the immediate next non-skip row).
    # Long-scan mode: pass through skip-labels because the entire label
    # column may be OCR'd before any values appear (OQEMA case).
    max_distance = 60 if allow_long_scan else 16

    for j in range(label_idx + 1, min(label_idx + max_distance, len(lines))):
        ln = lines[j]
        if _is_year_header_line(ln):
            continue
        if LABEL_TURNOVER_RE.match(ln.strip()) or any(p.search(ln) for p in PROFIT_LABEL_PRIORITIES):
            break
        if SKIP_LABEL_RE.match(ln.strip()) and not allow_long_scan:
            break
        nums = _filter_note_refs(_find_numbers_in_line(ln))
        has_p = "(" in ln
        usable = [n for n in nums if plausible(n, has_p)]
        if usable:
            cy = usable[0]
            py = usable[1] if len(usable) > 1 else None
            return cy, py, f"{label_line.strip()} | next: {ln.strip()}"

    return None, None, ""


def _parse_pl_page(text: str) -> tuple:
    """
    Walk lines looking for turnover and profit labels. For each label found,
    use _find_value_for_label to extract current and prior year values.

    Three passes (in order):
      Pass 1: normal scan (stops at next skip-label) — handles standard
              same-row or near-row layouts (Brenntag, Whitford, Camida)
      Pass 2: long-scan that passes through skip-labels — handles cases where
              labels and values are within ~60 lines but separated
      Pass 3: positional pass — when OCR reads the entire label column first
              and then the entire value column (OQEMA), build an ordered list
              of labels, find the value block, and pair them by position.

    Returns (turnover_cy, turnover_py, turnover_src,
             profit_cy, profit_py, profit_src).
    """
    lines = text.splitlines()

    def _scan(label_re_or_priority, allow_long: bool):
        is_list = isinstance(label_re_or_priority, list)
        candidates = label_re_or_priority if is_list else [label_re_or_priority]
        for label_re in candidates:
            for i, line in enumerate(lines):
                stripped = line.strip()
                if SKIP_LABEL_RE.match(stripped):
                    continue
                matched = label_re.match(stripped) if hasattr(label_re, "match") else label_re.search(stripped)
                if matched:
                    cy, py, src = _find_value_for_label(lines, i, allow_long_scan=allow_long)
                    if cy is not None:
                        return cy, py, src
        return None, None, ""

    turnover_cy, turnover_py, turnover_src = _scan(LABEL_TURNOVER_RE, allow_long=False)
    if turnover_cy is None:
        turnover_cy, turnover_py, turnover_src = _scan(LABEL_TURNOVER_RE, allow_long=True)

    profit_cy, profit_py, profit_src = _scan(PROFIT_LABEL_PRIORITIES, allow_long=False)
    if profit_cy is None:
        profit_cy, profit_py, profit_src = _scan(PROFIT_LABEL_PRIORITIES, allow_long=True)

    # Pass 3: positional matching for "all labels, then all values" layout
    if turnover_cy is None or profit_cy is None:
        t_cy, t_py, t_src, p_cy, p_py, p_src = _positional_match(lines)
        if turnover_cy is None and t_cy is not None:
            turnover_cy, turnover_py, turnover_src = t_cy, t_py, t_src
        if profit_cy is None and p_cy is not None:
            profit_cy, profit_py, profit_src = p_cy, p_py, p_src

    return turnover_cy, turnover_py, turnover_src, profit_cy, profit_py, profit_src


def _positional_match(lines: list[str]) -> tuple:
    """
    For layouts where OCR reads the entire label column before the value column
    (OQEMA-style): build ordered lists of (label_idx, kind) and (value_idx, value),
    pair them by position.

    Returns (t_cy, t_py, t_src, p_cy, p_py, p_src).
    """
    # Collect ordered labels along with what kind they are
    labels = []  # list of (line_idx, kind) where kind ∈ {'turnover', 'profit_pbt', 'profit_pft', 'other'}
    PROFIT_PBT = re.compile(r"(?:profit|loss)(?:\s*/\s*\(loss\))?\s+before\s+tax", re.IGNORECASE)
    PROFIT_PFT = re.compile(r"(?:profit|loss)(?:\s*/\s*\(loss\))?\s+for\s+the\s+(?:year|period|financial)", re.IGNORECASE)

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        # Stop collecting labels once we hit a line that looks like data
        if _looks_like_value_line(s):
            break
        if LABEL_TURNOVER_RE.match(s):
            labels.append((i, "turnover"))
        elif PROFIT_PBT.search(s):
            labels.append((i, "profit_pbt"))
        elif PROFIT_PFT.search(s):
            labels.append((i, "profit_pft"))
        elif SKIP_LABEL_RE.match(s):
            labels.append((i, "other"))

    if not labels:
        return None, None, "", None, None, ""

    # Find the start of the CY value block
    # A value block starts after the last label and is dominated by numeric lines
    last_label_idx = max(idx for idx, _ in labels)

    # Collect ordered values appearing after last_label_idx
    cy_values = []  # list of (line_idx, value)
    py_values = []  # list of (line_idx, value)
    section = "skip"  # "cy" once we see "2024 £", "py" once we see "2023 £"

    for i in range(last_label_idx + 1, len(lines)):
        s = lines[i].strip()
        if not s:
            continue
        # Year-header transitions: "2024" + "£" pattern signals start of CY block
        if re.match(r"^(20\d{2})$", s):
            year = int(s)
            # Look at next non-empty line for currency marker
            next_s = next((lines[k].strip() for k in range(i+1, min(i+3, len(lines))) if lines[k].strip()), "")
            if next_s in ("£", "$", "€") or len(next_s) <= 2:
                if not cy_values:
                    section = "cy"
                else:
                    section = "py"
                continue
        if s in ("£", "$", "€", "Note") or re.match(r"^\d{1,2}$", s):
            # Note refs and currency marks — skip
            continue
        # Numeric value?
        nums = _find_numbers_in_line(s)
        if nums and all(abs(n) >= 20 or "(" in s for n in nums):
            # Take the first number; sometimes a single line has two stacked values
            for n in nums:
                if section == "cy":
                    cy_values.append((i, n))
                elif section == "py":
                    py_values.append((i, n))

    # Build kind→position map (turnover is at position 0 in the label list)
    # The Nth label corresponds to the Nth value
    label_positions = {}
    for pos, (idx, kind) in enumerate(labels):
        if kind not in label_positions:
            label_positions[kind] = pos

    def value_at(kind: str):
        pos = label_positions.get(kind)
        if pos is None:
            return None, None
        cy = cy_values[pos][1] if pos < len(cy_values) else None
        py = py_values[pos][1] if pos < len(py_values) else None
        return cy, py

    t_cy, t_py = value_at("turnover")
    p_cy, p_py = value_at("profit_pbt")
    p_kind = "Profit/(loss) before tax"
    if p_cy is None:
        p_cy, p_py = value_at("profit_pft")
        p_kind = "Profit/(loss) for the financial year"

    t_src = f"positional: label '{lines[labels[label_positions['turnover']][0]].strip()}' at #{label_positions.get('turnover', '?')+1}" if t_cy is not None else ""
    p_src = f"positional: '{p_kind}'" if p_cy is not None else ""

    return t_cy, t_py, t_src, p_cy, p_py, p_src


def _looks_like_value_line(s: str) -> bool:
    """True if a line looks like it's part of a numeric value block."""
    s = s.strip()
    if not s:
        return False
    # Pure year like "2024" or "2023"
    if re.match(r"^20\d{2}$", s):
        return True
    # Currency marker alone: "£", "$", "€"
    if s in ("£", "$", "€"):
        return True
    # Note ref: 1-2 digits
    if re.match(r"^\d{1,2}$", s):
        return True
    # Number with comma separators (accounting style)
    if re.match(r"^\(?[\d,]+\.?\d*\)?$", s):
        return True
    return False


def extract_from_pdf_ocr(pdf_bytes: bytes) -> OcrResult:
    """
    OCR-based extraction from a scanned PDF. Caller should only invoke this
    after pdfplumber has returned no text (indicating a scan).

    Returns an OcrResult with `found=True` if turnover or profit was extracted.
    """
    warnings = []

    if not OCR_AVAILABLE:
        return OcrResult(
            found=False,
            error=(
                "OCR is not available in this deployment environment. "
                f"Missing: {OCR_IMPORT_ERROR}. "
                "Install pytesseract, pdf2image, and the tesseract-ocr binary."
            ),
            ocr_warnings=warnings,
        )

    if not pdf_bytes:
        return OcrResult(found=False, error="Empty PDF data", ocr_warnings=warnings)

    # Determine PDF page count cheaply
    try:
        # pdf2image doesn't give us page count without conversion. Estimate by
        # quick low-DPI conversion of the whole document.
        # For huge documents this could be slow — cap searches at 40 pages.
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
    except Exception:
        page_count = 0

    if page_count == 0:
        return OcrResult(found=False, error="Could not read PDF", ocr_warnings=warnings)

    # ── Find the P&L page ─────────────────────────────────────
    # Search in priority order:
    #   1. Pages 8-25 (typical for full annual reports — after auditor's report)
    #   2. Pages 1-7 (small-company filings often place P&L early)
    #   3. Pages 26-40 (very long reports)
    target_page = None
    found_text = ""

    if page_count >= 8:
        target_page, found_text = _find_pl_page(pdf_bytes, (8, min(25, page_count)))
    if target_page is None:
        target_page, found_text = _find_pl_page(pdf_bytes, (1, min(7, page_count)))
    if target_page is None and page_count > 25:
        target_page, found_text = _find_pl_page(pdf_bytes, (26, min(40, page_count)))

    if target_page is None:
        return OcrResult(
            found=False,
            error=(
                "OCR ran but could not locate a Profit & Loss section in the "
                "first 40 pages. The document may use unusual section headings, "
                "or may not contain a P&L at all (filleted accounts)."
            ),
            page_count=page_count,
            ocr_warnings=warnings,
        )

    # ── Re-OCR the target page at higher DPI for accuracy ────
    hi_text = _hi_dpi_extract(pdf_bytes, target_page, dpi=250)
    if not hi_text:
        # Fall back to the lower-DPI text we already have
        hi_text = found_text

    # ── Check: is this a non-UK / non-GBP filing? ───────────
    # Some "UK Companies House" search hits return a foreign parent's
    # consolidated accounts (e.g. OQEMA AG's German group filing). These have
    # values in EUR millions, often using different decimal conventions, and
    # would produce nonsense if blindly converted at the GBP→EUR rate.
    if NON_GBP_RE.search(hi_text):
        return OcrResult(
            found=False,
            error=(
                "The filing on page {p} appears to be a non-UK consolidated "
                "report (currency markers like 'in million euros' or a foreign "
                "parent indicator were detected). Automatic GBP-denominated "
                "extraction is not appropriate here. Enter the figures manually "
                "after converting to GBP at the appropriate rate, or commission "
                "a paid bureau report for the UK subsidiary instead.".format(p=target_page)
            ),
            page_count=page_count,
            ocr_page=target_page,
            raw_text=hi_text,
            ocr_warnings=warnings,
        )

    # ── Extract figures ──────────────────────────────────────
    scale, scale_label = _detect_scale(hi_text)
    t_cy, t_py, t_src, p_cy, p_py, p_src = _parse_pl_page(hi_text)
    fy_cy, fy_py = _detect_fiscal_years(hi_text)

    if t_cy is None and p_cy is None:
        return OcrResult(
            found=False,
            error=(
                f"OCR identified the P&L on page {target_page} but could not "
                f"parse turnover or profit values from the OCR'd text. The "
                f"text may have OCR errors that the regex patterns don't handle. "
                f"Try entering the figures manually."
            ),
            page_count=page_count,
            ocr_page=target_page,
            raw_text=hi_text,
            ocr_warnings=warnings,
        )

    revenue_gbp = t_cy * scale if t_cy is not None else None
    revenue_gbp_prior = t_py * scale if t_py is not None else None
    profit_gbp = p_cy * scale if p_cy is not None else None
    profit_gbp_prior = p_py * scale if p_py is not None else None

    # Build OCR-specific warnings
    warnings.append(
        f"Values extracted via OCR from a scanned PDF on page {target_page}. "
        f"OCR can misread digits, please verify the extracted figures against "
        f"the source document before relying on them for credit decisions."
    )
    if revenue_gbp and revenue_gbp > 10_000_000_000:
        warnings.append(
            f"Revenue figure of £{revenue_gbp:,.0f} looks unusually large, "
            f"check the scale (the document might be in millions not thousands)."
        )

    return OcrResult(
        found=True,
        revenue_gbp=revenue_gbp,
        net_profit_gbp=profit_gbp,
        revenue_gbp_prior=revenue_gbp_prior,
        net_profit_gbp_prior=profit_gbp_prior,
        fiscal_year=fy_cy,
        fiscal_year_prior=fy_py,
        scale_detected=scale,
        scale_label=scale_label,
        revenue_line=t_src,
        profit_line=p_src,
        raw_text=hi_text,
        page_count=page_count,
        extraction_method="ocr",
        ocr_page=target_page,
        ocr_warnings=warnings,
    )
