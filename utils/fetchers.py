"""
utils/fetchers.py
All external API calls for the IFC prospect lookup tool.
Every function returns a plain dict and never raises — failures return {"found": False}.

UK companies: revenue fetched automatically via Companies House API + iXBRL parsing,
              with multi-year trend and company-health flags (overdue accounts, liquidation).
Other countries: manual entry with a direct link to the right national registry.
"""

import os
import re
import requests
import streamlit as st
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

TIMEOUT = 10

# ── API keys ──────────────────────────────────────────────────
OPENCORPORATES_KEY  = st.secrets.get("OPENCORPORATES_API_KEY",   os.getenv("OPENCORPORATES_API_KEY", ""))
NEWSDATA_KEY        = st.secrets.get("NEWSDATA_API_KEY",          os.getenv("NEWSDATA_API_KEY", ""))
COMPANIES_HOUSE_KEY = st.secrets.get("COMPANIES_HOUSE_API_KEY",   os.getenv("COMPANIES_HOUSE_API_KEY", ""))
FMP_KEY             = st.secrets.get("FMP_API_KEY",               os.getenv("FMP_API_KEY", ""))

CH_BASE = "https://api.company-information.service.gov.uk"

# ── Country risk tiers ────────────────────────────────────────
COUNTRY_RISK = {
    "NL": 1, "DE": 1, "AT": 1, "CH": 1, "SE": 1, "NO": 1, "DK": 1, "FI": 1,
    "GB": 1, "IE": 1, "BE": 1, "LU": 1, "FR": 1, "US": 1, "CA": 1, "AU": 1,
    "NZ": 1, "JP": 1, "SG": 1, "KR": 1,
    "IT": 2, "ES": 2, "PT": 2, "GR": 2, "CZ": 2, "PL": 2, "HU": 2, "SK": 2,
    "RO": 2, "BG": 2, "HR": 2, "SI": 2, "EE": 2, "LV": 2, "LT": 2, "CN": 2,
    "IN": 2, "MX": 2, "BR": 2, "ZA": 2, "TR": 2, "AE": 2, "SA": 2, "IL": 2,
    "MY": 2, "TH": 2, "ID": 2, "VN": 2, "PH": 2, "EG": 2, "MA": 2, "NG": 2,
    "RU": 3, "BY": 3, "UA": 3, "KZ": 3, "UZ": 3, "AZ": 3, "GE": 3,
    "IQ": 3, "IR": 3, "SY": 3, "LY": 3, "YE": 3, "AF": 3, "PK": 3,
    "MM": 3, "KP": 3, "CU": 3, "VE": 3, "ZW": 3, "SD": 3, "SS": 3,
}

COUNTRY_NAMES = {
    "NL": "Netherlands", "DE": "Germany", "GB": "United Kingdom", "BE": "Belgium",
    "FR": "France", "IT": "Italy", "ES": "Spain", "PL": "Poland", "SE": "Sweden",
    "NO": "Norway", "DK": "Denmark", "FI": "Finland", "AT": "Austria", "CH": "Switzerland",
    "US": "United States", "CN": "China", "IN": "India", "JP": "Japan", "SG": "Singapore",
    "RU": "Russia", "TR": "Turkey", "AE": "UAE", "SA": "Saudi Arabia", "BR": "Brazil",
    "AU": "Australia", "CA": "Canada", "ZA": "South Africa",
}

# Manual lookup links shown for non-UK companies
FINANCIAL_SOURCE_URLS = {
    "NL": ("KVK / Kamer van Koophandel", "https://www.kvk.nl/zoeken/"),
    "DE": ("Bundesanzeiger", "https://www.bundesanzeiger.de/"),
    "BE": ("NBB Annual Accounts", "https://www.nbb.be/en/central-balance-sheet-office"),
    "FR": ("Infogreffe", "https://www.infogreffe.fr/"),
    "IT": ("Registro Imprese", "https://www.registroimprese.it/"),
    "ES": ("BORME / Registro Mercantil", "https://www.boe.es/diario_borme/"),
    "US": ("SEC EDGAR", "https://www.sec.gov/cgi-bin/browse-edgar"),
    "CN": ("CNIPA / SAMR", "https://www.gsxt.gov.cn/"),
}

# XBRL tag names Companies House uses for turnover/revenue.
# Note: GrossProfit intentionally removed — it is NOT revenue.
TURNOVER_TAGS = [
    "Turnover",
    "TurnoverRevenue",
    "TurnoverGrossProceeds",
    "Revenue",
    "TotalRevenue",
]

PROFIT_TAGS = [
    "ProfitLossOnOrdinaryActivitiesBeforeTax",
    "ProfitLoss",
    "ProfitLossForPeriod",
    "NetIncomeLoss",
    "OperatingProfitLoss",
]

# Terms that indicate real credit-negative news (used for lightweight sentiment)
NEGATIVE_NEWS_TERMS = [
    "bankruptcy", "bankrupt", "insolvency", "insolvent", "administration",
    "liquidation", "winding up", "wound up", "receivership", "creditor",
    "fraud", "investigation", "lawsuit", "sued", "sanction", "fined",
    "layoff", "layoffs", "redundanc", "strike off", "struck off",
    "default", "missed payment", "scandal", "probe",
]


# ─────────────────────────────────────────────────────────────
# OPENCORPORATES
# ─────────────────────────────────────────────────────────────

def fetch_candidates(name: str, jurisdiction: str = "") -> list:
    """Returns up to 5 OpenCorporates matches for the user to pick from."""
    try:
        params = {"q": name, "per_page": 5, "order": "score"}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction
        if OPENCORPORATES_KEY:
            params["api_token"] = OPENCORPORATES_KEY
        r = requests.get(
            "https://api.opencorporates.com/v0.4/companies/search",
            params=params, timeout=TIMEOUT,
        )
        r.raise_for_status()
        results = r.json().get("results", {}).get("companies", [])
        candidates = []
        for item in results:
            c = item.get("company", {})
            candidates.append({
                "name": c.get("name", ""),
                "company_number": c.get("company_number", ""),
                "jurisdiction": c.get("jurisdiction_code", "").upper(),
                "company_type": c.get("company_type", ""),
                "status": c.get("current_status", ""),
                "incorporation_date": c.get("incorporation_date", ""),
                "registered_address": c.get("registered_address_in_full", ""),
                "source_url": c.get("opencorporates_url", ""),
                "inactive": c.get("inactive", False),
            })
        return candidates
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────
# GLEIF
# ─────────────────────────────────────────────────────────────

def fetch_gleif(name: str) -> dict:
    """Returns the top GLEIF match for a company name. One call, not two."""
    try:
        r = requests.get(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": name, "page[size]": 1},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        if not records:
            return {"found": False}
        best   = records[0]
        attrs  = best.get("attributes", {})
        entity = attrs.get("entity", {})
        reg    = attrs.get("registration", {})
        return {
            "found": True,
            "lei": best.get("id", ""),
            "lei_status": reg.get("status", "UNKNOWN"),
            "legal_name": entity.get("legalName", {}).get("name", ""),
            "country": entity.get("legalAddress", {}).get("country", ""),
            "city": entity.get("legalAddress", {}).get("city", ""),
            "status": entity.get("status", ""),
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# NEWS
# ─────────────────────────────────────────────────────────────

def _keyword_sentiment(title: str) -> str:
    """Simple, transparent sentiment based on credit-risk keywords in the title."""
    t = (title or "").lower()
    for term in NEGATIVE_NEWS_TERMS:
        if term in t:
            return "negative"
    return "neutral"


def fetch_news(name: str) -> dict:
    """Recent news headlines. Uses keyword-based sentiment — NewsData's own
    sentiment field is unreliable on the free tier."""
    if not NEWSDATA_KEY:
        return {"found": False, "reason": "No API key"}
    try:
        r = requests.get(
            "https://newsdata.io/api/1/news",
            params={"apikey": NEWSDATA_KEY, "q": f'"{name}"', "language": "en", "size": 5},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        articles = r.json().get("results", [])
        if not articles:
            return {"found": False, "reason": "No recent news found"}
        return {
            "found": True,
            "articles": [{
                "title": a.get("title", ""),
                "source": a.get("source_id", ""),
                "published": (a.get("pubDate") or "")[:10],
                "url": a.get("link", ""),
                "sentiment": _keyword_sentiment(a.get("title", "")),
            } for a in articles[:5]],
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# SANCTIONS
# ─────────────────────────────────────────────────────────────

# Cache the sanctions list in memory — it's ~5MB and updated infrequently
_SANCTIONS_CACHE = {"fetched_at": None, "entity_names": None}
_SANCTIONS_TTL = timedelta(hours=24)


def _normalise_name(n: str) -> str:
    """Lowercase, strip punctuation and common company suffixes for sanctions matching."""
    s = (n or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    suffixes = [
        " ltd", " limited", " plc", " p l c", " llc", " inc", " incorporated",
        " corp", " corporation", " co", " company", " gmbh", " ag",
        " bv", " nv", " sa", " spa", " srl", " sarl", " oy", " ab",
    ]
    # Strip repeatedly in case multiple suffixes stack (rare but possible)
    changed = True
    while changed:
        changed = False
        for suf in suffixes:
            if s.endswith(suf):
                s = s[: -len(suf)].strip()
                changed = True
                break
    return s


def _load_sanctions_list():
    """Download and parse the EU sanctions XML into a set of normalised entity names.
    Cached for 24 hours. Returns None on failure."""
    now = datetime.now()
    cached = _SANCTIONS_CACHE.get("entity_names")
    fetched = _SANCTIONS_CACHE.get("fetched_at")
    if cached is not None and fetched and now - fetched < _SANCTIONS_TTL:
        return cached

    try:
        r = requests.get(
            "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content",
            timeout=20,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)

        names = set()
        # Walk for nameAlias elements (EU list uses nameAlias with wholeName attribute)
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]  # strip namespace
            if tag == "nameAlias":
                whole = elem.attrib.get("wholeName", "")
                if whole:
                    names.add(_normalise_name(whole))

        _SANCTIONS_CACHE["entity_names"] = names
        _SANCTIONS_CACHE["fetched_at"] = now
        return names
    except Exception:
        return None


def fetch_sanctions(name: str) -> dict:
    """Exact-match sanctions screening against EU consolidated list.
    Matches on normalised entity name, not substring of full XML — this
    eliminates the false-positive problem of the original implementation."""
    names = _load_sanctions_list()
    if names is None:
        return {"screened": False, "error": "Could not load sanctions list"}
    normalised = _normalise_name(name)
    if not normalised or len(normalised) < 4:
        return {"screened": True, "flagged": False}
    # Flag only if the normalised name matches a sanctioned entity's normalised name exactly
    flagged = normalised in names
    return {"screened": True, "flagged": flagged}


# ─────────────────────────────────────────────────────────────
# COMPANIES HOUSE — UK FINANCIAL DATA + HEALTH FLAGS
# ─────────────────────────────────────────────────────────────

def _ch_get(path: str, params: dict | None = None) -> requests.Response:
    """Authenticated GET to Companies House API."""
    return requests.get(
        f"{CH_BASE}{path}",
        params=params or {},
        auth=(COMPANIES_HOUSE_KEY, ""),
        timeout=TIMEOUT,
    )


def fetch_ch_company_profile(company_number: str) -> dict:
    """
    Pulls the main company profile — status, accounts due date, insolvency flags.
    These are free credit-risk signals sitting right there in the API.
    """
    if not COMPANIES_HOUSE_KEY or not company_number:
        return {"found": False}
    try:
        r = _ch_get(f"/company/{company_number}")
        r.raise_for_status()
        d = r.json()

        accounts = d.get("accounts", {}) or {}
        next_accounts = accounts.get("next_accounts", {}) or {}
        last_accounts = accounts.get("last_accounts", {}) or {}

        accounts_overdue = bool(next_accounts.get("overdue", False))
        confirmation_overdue = bool(
            (d.get("confirmation_statement") or {}).get("overdue", False)
        )

        status = (d.get("company_status") or "").lower()

        return {
            "found": True,
            "status":                d.get("company_status", ""),
            "status_detail":         d.get("company_status_detail", ""),
            "accounts_overdue":      accounts_overdue,
            "confirmation_overdue":  confirmation_overdue,
            "last_accounts_date":    last_accounts.get("made_up_to", ""),
            "next_accounts_due":     next_accounts.get("due_on", ""),
            "accounts_category":     last_accounts.get("type", ""),
            "is_active":             status == "active",
            "is_liquidation":        "liquidation" in status,
            "is_dissolved":          status in ("dissolved", "converted-closed"),
            "is_administration":     "administration" in status,
            "has_insolvency_history": bool(d.get("has_insolvency_history", False)),
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


def _extract_xbrl_value(xbrl_text: str, tags: list):
    """
    Extract the most-recent numeric value from iXBRL by searching for known
    tag names. Handles iXBRL `scale` and `sign` attributes properly — this is
    a real bug in the original implementation, where `scale="3"` (thousands)
    or `scale="6"` (millions) was ignored and values were read as raw units.
    """
    for tag in tags:
        # Prefer inline XBRL form: <ix:nonFraction name="uk-gaap:Turnover" scale="3" sign="-">1234</...>
        pattern_ixbrl = rf'<[^>]*\bname="[^"]*:?{tag}"[^>]*>\s*([-\d,\.\(\)\s]+?)\s*<'
        matches = list(re.finditer(pattern_ixbrl, xbrl_text, re.IGNORECASE))
        # Fallback: plain namespaced tag form: <ns:Turnover>value</ns:Turnover>
        if not matches:
            pattern_plain = rf'<[^>]*:{tag}\b[^>]*>\s*([-\d,\.\(\)\s]+?)\s*<'
            matches = list(re.finditer(pattern_plain, xbrl_text, re.IGNORECASE))

        for match in matches:
            raw = match.group(1).strip()
            is_paren_neg = raw.startswith("(") and raw.endswith(")")
            cleaned = raw.strip("()").replace(",", "").replace(" ", "")
            try:
                val = float(cleaned)
            except ValueError:
                continue
            if is_paren_neg:
                val = -val

            # Pull scale/sign attributes from the matched opening-tag span.
            # The match spans from `<` through the content to the closing `<`,
            # so the opening tag is everything up to the first `>`.
            open_tag_end = match.group(0).find(">")
            open_tag = match.group(0)[:open_tag_end] if open_tag_end != -1 else match.group(0)
            scale_match = re.search(r'\bscale="(-?\d+)"', open_tag)
            sign_match  = re.search(r'\bsign="([-+])"',   open_tag)
            if scale_match:
                try:
                    val *= 10 ** int(scale_match.group(1))
                except ValueError:
                    pass
            if sign_match and sign_match.group(1) == "-":
                val = -abs(val)

            if abs(val) >= 100:
                return val
    return None


def _detect_filing_disclosure_level(xbrl_text: str) -> str:
    """
    Inspect the iXBRL for markers that indicate what level of disclosure the
    directors chose. Returns one of:
      - 'filleted'  : directors elected NOT to deliver the P&L (s.444(5A))
      - 'small'     : small companies regime, may or may not include P&L
      - 'micro'     : micro-entity (FRS 105)
      - 'full'      : no limiting marker found — treat as full accounts
    This is used purely for UX messaging; extraction still runs normally.
    """
    t = xbrl_text or ""
    if "ElectedNotToDeliverProfitLossAccount" in t or "NotToDeliverProfitLoss" in t:
        return "filleted"
    if "FRS105" in t or "MicroEntities" in t:
        return "micro"
    if "SmallCompaniesRegime" in t or "SmallEntities" in t or "FRS102Section1A" in t:
        return "small"
    return "full"


def _fetch_single_ixbrl_accounts(filing: dict):
    """
    Given one filing from the filing-history API, download its iXBRL document
    and attempt to extract turnover + profit.

    Returns:
        None                       — no iXBRL available at all for this filing
        dict with all-None figures — iXBRL downloaded but no P&L disclosed
                                     (directors elected not to deliver — s.444(5A))
        dict with figures          — full extraction successful
    """
    meta_url = (filing.get("links") or {}).get("document_metadata", "")
    if not meta_url:
        return None
    filing_date = filing.get("date", "")
    filing_desc = filing.get("description", "")

    try:
        r2 = requests.get(meta_url, auth=(COMPANIES_HOUSE_KEY, ""), timeout=TIMEOUT)
        if r2.status_code != 200:
            return None
        meta = r2.json()
        resources = meta.get("resources", {}) or {}

        if not any("xhtml" in ct.lower() or "xbrl" in ct.lower() for ct in resources):
            return None

        doc_url = (meta.get("links") or {}).get("document", "")
        if not doc_url:
            return None

        r3 = requests.get(
            doc_url,
            auth=(COMPANIES_HOUSE_KEY, ""),
            headers={"Accept": "application/xhtml+xml"},
            timeout=20,
        )
        r3.raise_for_status()
        xbrl_text = r3.text

        turnover   = _extract_xbrl_value(xbrl_text, TURNOVER_TAGS)
        net_profit = _extract_xbrl_value(xbrl_text, PROFIT_TAGS)
        disclosure = _detect_filing_disclosure_level(xbrl_text)

        return {
            "filing_date": filing_date,
            "filing_desc": filing_desc,
            "turnover_gbp": turnover,
            "net_profit_gbp": net_profit,
            "disclosure_level": disclosure,
        }
    except Exception:
        return None


# ── PDF auto-fetch fallback ──────────────────────────────────

def _try_pdf_extraction(filing_items: list) -> dict | None:
    """
    When no iXBRL is available, walk the filings and try to find the most
    recent one that's available as a PDF. Download it and run pdfplumber-based
    extraction.

    Returns a financials dict compatible with the iXBRL path on success.
    On failure, returns a dict with `found=False` and a `failure_reason` field
    that describes WHY extraction failed (so the caller can route the user
    to the appropriate message — scanned PDF vs text PDF that missed vs no
    PDF at all are very different situations).
    """
    # Late import so the main fetchers module doesn't hard-depend on pdfplumber
    try:
        from utils.pdf_extract import extract_from_pdf, fetch_pdf_from_companies_house
    except Exception:
        return None

    # Track diagnostics across all filings tried
    best_partial: dict | None = None
    saw_pdf_at_all = False
    saw_scan = False
    saw_text_pdf_miss = False

    for f in filing_items:
        pdf_bytes = fetch_pdf_from_companies_house(f, COMPANIES_HOUSE_KEY)
        if not pdf_bytes:
            continue
        saw_pdf_at_all = True
        result = extract_from_pdf(pdf_bytes)
        if not result.found:
            # Distinguish scans from text PDFs that simply didn't yield a hit
            if "scanned image" in (result.error or "").lower():
                saw_scan = True
            else:
                saw_text_pdf_miss = True
            continue

        turnover = result.revenue_gbp
        net_profit = result.net_profit_gbp
        filing_date = f.get("date", "")
        filing_desc = f.get("description", "")

        is_partial = (turnover is None and net_profit is not None)

        rate, rate_src = get_gbp_to_eur_rate()
        out = {
            "found": turnover is not None,
            "partial_extract": is_partial,
            "source": "Companies House PDF (auto-extracted)",
            "revenue":        round(turnover * rate)    if turnover   is not None else None,
            "revenue_gbp":    turnover,
            "net_income":     round(net_profit * rate)  if net_profit is not None else None,
            "net_income_gbp": net_profit,
            "currency_note":  (
                f"Extracted from PDF at scale ×{result.scale_detected} "
                f"({result.scale_label}). GBP→EUR at {rate:.3f} ({rate_src}). "
                f"Verify against source filing before use."
            ),
            "fiscal_year":    filing_date[:4] if filing_date else "",
            "filing_date":    filing_date,
            "filing_desc":    filing_desc,
            "trend":          [],
            "yoy_pct":        None,
            "trend_label":    "single_year",
            "pdf_extracted":  True,
            "pdf_source_line_revenue": result.revenue_line,
            "pdf_source_line_profit":  result.profit_line,
        }
        if is_partial:
            out["reason"] = (
                "Profit extracted but turnover was not disclosed — this usually means "
                "the company files small-company, abridged, or filleted accounts "
                "which omit the profit and loss account. Enter revenue manually if available."
            )
            # Remember this partial in case no later filing is better
            if best_partial is None:
                best_partial = out
            continue  # try the next (older) filing to see if it's a full set

        # Full extract — return immediately
        return out

    # If we got here, either no PDFs existed or every PDF failed.
    # Return a diagnostic dict so the caller can route to the right message.
    if best_partial is not None:
        return best_partial

    if not saw_pdf_at_all:
        return None  # no PDFs available — caller's existing "no data" path handles this

    # We tried PDFs but extraction failed
    return {
        "found": False,
        "pdf_attempted": True,
        "pdf_scan_only": saw_scan and not saw_text_pdf_miss,
        "pdf_text_miss": saw_text_pdf_miss,
        "failure_reason": (
            "scan_only" if (saw_scan and not saw_text_pdf_miss)
            else "text_extraction_miss" if saw_text_pdf_miss
            else "no_pdf"
        ),
    }


# ── GBP → EUR: live rate via ECB, cached 24h ─────────────────

_FX_CACHE = {"rate": None, "fetched_at": None}


def get_gbp_to_eur_rate():
    """
    Return (rate, source_label). ECB publishes EUR→GBP; we invert it.
    Falls back to a static rate if the call fails. Cached 24h.
    """
    now = datetime.now()
    if (
        _FX_CACHE.get("rate") is not None
        and _FX_CACHE.get("fetched_at")
        and now - _FX_CACHE["fetched_at"] < timedelta(hours=24)
    ):
        return _FX_CACHE["rate"], "ECB daily rate (cached)"

    try:
        r = requests.get(
            "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        m = re.search(r"currency=['\"]GBP['\"]\s+rate=['\"]([\d.]+)['\"]", r.text)
        if m:
            eur_to_gbp = float(m.group(1))
            gbp_to_eur = 1.0 / eur_to_gbp
            _FX_CACHE["rate"] = gbp_to_eur
            _FX_CACHE["fetched_at"] = now
            return gbp_to_eur, "ECB daily rate"
    except Exception:
        pass

    # Fallback — reasonable mid-2020s rate
    return 1.15, "static fallback"


def fetch_financials_companies_house(company_number: str) -> dict:
    """
    Fetch revenue + profit from Companies House iXBRL accounts.

    Upgraded behaviour vs original:
      - Extracts multi-year trend (up to 3 most recent accounts) not just the latest
      - Proper iXBRL scale/sign handling (fixes the "12" vs "12,000,000" bug)
      - Live GBP→EUR via ECB with 24h cache, falls back to static rate on failure
      - GrossProfit is no longer used as a turnover fallback (it isn't revenue)
      - If iXBRL unavailable, tries PDF extraction via pdfplumber automatically
    """
    if not COMPANIES_HOUSE_KEY:
        return {"found": False, "reason": "No Companies House API key configured"}
    if not company_number:
        return {"found": False, "reason": "No company number"}

    filing_url = f"https://find-and-update.company-information.service.gov.uk/company/{company_number}/filing-history"

    try:
        r = _ch_get(
            f"/company/{company_number}/filing-history",
            {"category": "accounts", "items_per_page": 25},
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            return {"found": False, "reason": "No accounts filed",
                    "filing_url": filing_url,
                    "manual_source": "Companies House", "manual_url": filing_url}

        # Walk filings newest-first; collect iXBRL-readable ones. Each parsed
        # result includes disclosure_level and may have turnover/net_profit
        # as None if the P&L was not disclosed (filleted accounts).
        parsed_filings = []
        for f in items:
            parsed = _fetch_single_ixbrl_accounts(f)
            if parsed:
                parsed_filings.append(parsed)
                if len(parsed_filings) >= 5:
                    break

        # Only include filings with a turnover figure in the trend display
        trend = [p for p in parsed_filings if p.get("turnover_gbp") is not None][:3]

        if not trend:
            # No filing had an extractable turnover figure. Several distinct
            # situations land here and each warrants a different user-facing
            # message:
            #
            #   (a) iXBRL exists but the company filed FILLETED (s.444(5A))
            #   (b) iXBRL exists but the company filed MICRO-ENTITY (FRS 105)
            #   (c) iXBRL exists but P&L was filed without a turnover tag
            #       (partial-disclosure variant, observed empirically)
            #   (d) NO iXBRL EXISTS at all; only PDF filings are available
            #       AND those PDFs are scanned images (no extractable text)
            #   (e) NO iXBRL exists; PDFs are text-based but extraction missed
            #
            # The original code grouped (d), (e) and unknown iXBRL misses under
            # a single "may file abridged or filleted" message, which is wrong:
            # the company may file full accounts in a format the tool can't
            # parse, not a legally restricted format.
            #
            had_ixbrl = bool(parsed_filings)
            disclosure_levels = [p.get("disclosure_level") for p in parsed_filings]
            is_filleted = any(d == "filleted" for d in disclosure_levels)
            is_micro = any(d == "micro" for d in disclosure_levels) or any(
                ("micro" in (f.get("description") or "").lower()) or
                ("total exemption" in (f.get("description") or "").lower())
                for f in items[:3]
            )
            is_small = any(d == "small" for d in disclosure_levels)

            # Try PDF extraction (still worth a shot — text-based PDFs may yield
            # a result even when iXBRL was missing or insufficient).
            pdf_result = _try_pdf_extraction(items)
            if pdf_result and pdf_result.get("found"):
                return {**pdf_result, "filing_url": filing_url,
                        "disclosure_level": "pdf_extracted"}

            # Determine the right message in priority order. Legal disclosure
            # situations take precedence over format situations because they're
            # more specific and more actionable for the user.
            if is_filleted:
                reason = (
                    "The directors of this company elected not to deliver a profit "
                    "and loss account (filed under s.444(5A) Companies Act 2006, "
                    "'filleted accounts'). Turnover and profit are not publicly "
                    "disclosed. Enter revenue manually if available from another source."
                )
                level = "filleted"
            elif is_micro:
                reason = (
                    "This company files micro-entity accounts (FRS 105). Turnover "
                    "and profit are not legally required to be disclosed at this "
                    "size. Enter manually if available."
                )
                level = "micro"
            elif is_small:
                reason = (
                    "This company files under the small companies regime. The "
                    "filed accounts do not include a profit and loss account. "
                    "Enter turnover manually if available."
                )
                level = "small"
            elif had_ixbrl:
                # iXBRL was present but no turnover tag — partial disclosure
                reason = (
                    "The iXBRL filing was parsed successfully but did not include "
                    "a turnover tag. This typically indicates partial disclosure "
                    "(some figures published, others omitted) under FRS 102 small "
                    "or abridged-account variants. Enter revenue manually if available."
                )
                level = "partial_disclosure"
            elif pdf_result and pdf_result.get("pdf_scan_only"):
                # No iXBRL exists AND the only PDFs available are image scans.
                # This is a FORMAT barrier, not a legal-disclosure barrier.
                reason = (
                    "Filings are available only as scanned PDF documents, which "
                    "contain no machine-readable text. Automatic extraction is "
                    "not possible. Recommended: commission a Graydon Creditsafe "
                    "report, or enter financial data manually from the source filing."
                )
                level = "pdf_scan_only"
            elif pdf_result and pdf_result.get("pdf_text_miss"):
                # Text-based PDFs were present but the extractor couldn't find
                # the figures (unusual layout, atypical labels, etc).
                reason = (
                    "Filings are available as PDF only, and automatic extraction "
                    "could not locate the turnover figure in the document layout. "
                    "Upload the PDF manually below, or enter revenue directly."
                )
                level = "pdf_text_miss"
            else:
                # No iXBRL, no PDFs, or unclear situation — generic fallback
                reason = (
                    "Revenue was not found in the filed accounts. Enter revenue "
                    "manually if available from another source."
                )
                level = "unknown"

            # Even with no turnover, we may have extracted a profit from iXBRL
            # (rare but possible). Include it so the UI can show something useful.
            has_profit = any(p.get("net_profit_gbp") is not None for p in parsed_filings)
            latest_profit = None
            latest_profit_fy = ""
            if has_profit:
                for p in parsed_filings:
                    if p.get("net_profit_gbp") is not None:
                        latest_profit = p["net_profit_gbp"]
                        latest_profit_fy = (p.get("filing_date") or "")[:4]
                        break

            out = {
                "found": False,
                "reason": reason,
                "disclosure_level": level,
                "filing_url": filing_url,
                "manual_source": "Companies House",
                "manual_url": filing_url,
                "pdf_auto_tried": True,
                "is_micro_entity": is_micro,
                "is_filleted": is_filleted,
            }
            if latest_profit is not None:
                rate, _ = get_gbp_to_eur_rate()
                out["net_income_gbp"] = latest_profit
                out["net_income"]     = round(latest_profit * rate)
                out["fiscal_year"]    = latest_profit_fy
            return out

        latest = trend[0]
        turnover   = latest["turnover_gbp"]
        net_profit = latest["net_profit_gbp"]

        rate, rate_src = get_gbp_to_eur_rate()

        trend_out = [{
            "fiscal_year": (t["filing_date"] or "")[:4],
            "filing_date": t["filing_date"],
            "revenue_gbp": t["turnover_gbp"],
            "revenue_eur": round(t["turnover_gbp"] * rate) if t["turnover_gbp"] else None,
            "net_profit_gbp": t["net_profit_gbp"],
            "net_profit_eur": round(t["net_profit_gbp"] * rate) if t["net_profit_gbp"] else None,
        } for t in trend]

        # Year-over-year direction (needs ≥2 years of turnover)
        revenues = [t["revenue_gbp"] for t in trend_out if t["revenue_gbp"]]
        if len(revenues) >= 2 and revenues[1]:
            yoy_pct = (revenues[0] - revenues[1]) / abs(revenues[1])
            if   yoy_pct >=  0.05: trend_label = "growing"
            elif yoy_pct <= -0.05: trend_label = "declining"
            else:                  trend_label = "flat"
        else:
            yoy_pct, trend_label = None, "single_year"

        return {
            "found": True,
            "source": "Companies House (automatic)",
            "revenue":         round(turnover * rate) if turnover else None,
            "revenue_gbp":     turnover,
            "net_income":      round(net_profit * rate) if net_profit else None,
            "net_income_gbp":  net_profit,
            "currency_note":   f"GBP→EUR at {rate:.3f} ({rate_src}). Original: £{turnover/1e6:.1f}M turnover" if turnover else "",
            "fiscal_year":     (latest["filing_date"] or "")[:4],
            "filing_date":     latest["filing_date"],
            "filing_desc":     latest["filing_desc"],
            "filing_url":      filing_url,
            "trend":           trend_out,
            "yoy_pct":         yoy_pct,
            "trend_label":     trend_label,
        }

    except Exception as e:
        return {"found": False, "error": str(e), "filing_url": filing_url,
                "manual_source": "Companies House", "manual_url": filing_url}


# ─────────────────────────────────────────────────────────────
# FMP — LISTED COMPANIES (GLOBAL FALLBACK)
# ─────────────────────────────────────────────────────────────

def fetch_financials_fmp(name: str) -> dict:
    """Revenue from Financial Modeling Prep — for listed companies only."""
    if not FMP_KEY:
        return {"found": False, "reason": "No FMP API key"}
    try:
        r = requests.get(
            "https://financialmodelingprep.com/api/v3/search",
            params={"query": name, "limit": 3, "apikey": FMP_KEY},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        hits = r.json()
        if not hits:
            return {"found": False, "reason": "Not listed"}
        ticker = hits[0].get("symbol", "")
        r2 = requests.get(
            f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}",
            params={"limit": 1, "apikey": FMP_KEY},
            timeout=TIMEOUT,
        )
        r2.raise_for_status()
        stmts = r2.json()
        if not stmts:
            return {"found": False, "reason": "No statements"}
        s = stmts[0]
        return {
            "found": True,
            "source": f"FMP · {hits[0].get('exchangeShortName','')}:{ticker}",
            "revenue": s.get("revenue", 0) or 0,
            "net_income": s.get("netIncome", 0) or 0,
            "fiscal_year": s.get("calendarYear", ""),
            "currency_note": f"Reported in {s.get('reportedCurrency','USD')}",
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# UPLOADED PDF — MANUAL FALLBACK
# ─────────────────────────────────────────────────────────────

def extract_uploaded_pdf(pdf_bytes: bytes, use_ocr: bool = False) -> dict:
    """
    Run extraction against a user-uploaded PDF.

    Two-pass design:
      Pass 1 (always): pdfplumber text extraction. Fast (1-2 seconds). Works for
      modern text-based PDFs. If this succeeds, return immediately.

      Pass 2 (only if use_ocr=True): Tesseract OCR on the same PDF. Slow
      (20-100 seconds depending on page count) but handles scanned/image-only
      PDFs that pdfplumber cannot read.

    The two-pass design lets the UI offer OCR as an explicit opt-in: first try
    the fast path, then prompt the user "this looks like a scan, want to run
    OCR?" before incurring the time cost.

    Returns the same shape of dict as the Companies House path so the UI can
    render it identically. On scan-only PDFs (with use_ocr=False), returns
    found=False with a flag `is_scan_only=True` so the UI can prompt for OCR.
    """
    try:
        from utils.pdf_extract import extract_from_pdf
    except Exception as e:
        return {"found": False, "reason": f"PDF extractor unavailable: {e}"}

    # ── Pass 1: text extraction (fast) ───────────────────────
    result = extract_from_pdf(pdf_bytes)

    if result.found:
        rate, rate_src = get_gbp_to_eur_rate()
        turnover = result.revenue_gbp
        net_profit = result.net_profit_gbp
        return {
            "found": True,
            "source": "Uploaded PDF (text extraction)",
            "extraction_method": result.extraction_method,
            "revenue":        round(turnover * rate)   if turnover   is not None else None,
            "revenue_gbp":    turnover,
            "net_income":     round(net_profit * rate) if net_profit is not None else None,
            "net_income_gbp": net_profit,
            "currency_note": (
                f"Extracted from uploaded PDF at scale ×{result.scale_detected} "
                f"({result.scale_label}). Assumed GBP. "
                f"GBP→EUR at {rate:.3f} ({rate_src}). Verify against the PDF before use."
            ),
            "fiscal_year": "",
            "pdf_extracted": True,
            "pdf_source_line_revenue": result.revenue_line,
            "pdf_source_line_profit":  result.profit_line,
            "raw_text": result.raw_text,
            "page_count": result.page_count,
        }

    # ── Text extraction failed — is it a scan? ───────────────
    is_scan_only = "scanned image" in (result.error or "").lower()

    if not use_ocr:
        # Return diagnostic so the UI can offer OCR as an opt-in
        return {
            "found": False,
            "reason": result.error or "No recognisable revenue/profit figures in the uploaded PDF.",
            "raw_text": result.raw_text,
            "page_count": result.page_count,
            "is_scan_only": is_scan_only,
            "ocr_available": True,  # tell the UI we can try OCR
        }

    # ── Pass 2: OCR (slow, opt-in) ───────────────────────────
    try:
        from utils.ocr_extract import extract_from_pdf_ocr, OCR_AVAILABLE
    except Exception as e:
        return {
            "found": False,
            "reason": f"OCR module not available: {e}",
            "raw_text": result.raw_text,
            "page_count": result.page_count,
        }

    if not OCR_AVAILABLE:
        return {
            "found": False,
            "reason": (
                "OCR is not available in this deployment environment. "
                "Enter the figures manually."
            ),
            "raw_text": result.raw_text,
            "page_count": result.page_count,
        }

    ocr_result = extract_from_pdf_ocr(pdf_bytes)

    if not ocr_result.found:
        return {
            "found": False,
            "reason": ocr_result.error or "OCR completed but no figures were extracted.",
            "raw_text": ocr_result.raw_text or result.raw_text,
            "page_count": ocr_result.page_count,
            "ocr_attempted": True,
        }

    rate, rate_src = get_gbp_to_eur_rate()
    turnover = ocr_result.revenue_gbp
    net_profit = ocr_result.net_profit_gbp
    turnover_prior = ocr_result.revenue_gbp_prior
    net_profit_prior = ocr_result.net_profit_gbp_prior
    warnings = ocr_result.ocr_warnings or []

    # Build a trend array in the same shape as the iXBRL path produces it. The
    # existing UI already knows how to render a "trend" list with fiscal_year +
    # revenue/net_profit fields, so by mirroring that shape we get the year-on-
    # year comparison and growth direction for free.
    trend_out = []
    if turnover is not None or net_profit is not None:
        trend_out.append({
            "fiscal_year":    ocr_result.fiscal_year,
            "filing_date":    "",
            "revenue_gbp":    turnover,
            "revenue_eur":    round(turnover * rate) if turnover else None,
            "net_profit_gbp": net_profit,
            "net_profit_eur": round(net_profit * rate) if net_profit else None,
        })
    if turnover_prior is not None or net_profit_prior is not None:
        trend_out.append({
            "fiscal_year":    ocr_result.fiscal_year_prior,
            "filing_date":    "",
            "revenue_gbp":    turnover_prior,
            "revenue_eur":    round(turnover_prior * rate) if turnover_prior else None,
            "net_profit_gbp": net_profit_prior,
            "net_profit_eur": round(net_profit_prior * rate) if net_profit_prior else None,
        })

    # Compute YoY direction if we have both years' revenue
    yoy_pct = None
    trend_label = "single_year"
    if turnover is not None and turnover_prior:
        yoy_pct = (turnover - turnover_prior) / abs(turnover_prior)
        if   yoy_pct >=  0.05: trend_label = "growing"
        elif yoy_pct <= -0.05: trend_label = "declining"
        else:                  trend_label = "flat"

    return {
        "found": True,
        "source": f"Uploaded PDF (OCR, page {ocr_result.ocr_page})",
        "extraction_method": "ocr",
        "revenue":        round(turnover * rate)   if turnover   is not None else None,
        "revenue_gbp":    turnover,
        "net_income":     round(net_profit * rate) if net_profit is not None else None,
        "net_income_gbp": net_profit,
        "currency_note": (
            f"OCR-extracted from uploaded PDF at scale ×{ocr_result.scale_detected} "
            f"({ocr_result.scale_label}). Assumed GBP. "
            f"GBP→EUR at {rate:.3f} ({rate_src}). "
            f"⚠ OCR can misread digits, please verify against the source document."
        ),
        "fiscal_year":     ocr_result.fiscal_year,
        "trend":           trend_out,
        "yoy_pct":         yoy_pct,
        "trend_label":     trend_label,
        "pdf_extracted":   True,
        "ocr_extracted":   True,
        "ocr_warnings": warnings,
        "ocr_page": ocr_result.ocr_page,
        "pdf_source_line_revenue": ocr_result.revenue_line,
        "pdf_source_line_profit":  ocr_result.profit_line,
        "raw_text": ocr_result.raw_text,
        "page_count": ocr_result.page_count,
    }


# ─────────────────────────────────────────────────────────────
# MASTER FINANCIAL FETCHER
# ─────────────────────────────────────────────────────────────

def fetch_financials(name: str, jurisdiction: str = "", company_number: str = "") -> dict:
    """
    Tries in order:
      1. Companies House iXBRL — UK companies (automatic, free, with trend + health)
      2. FMP — listed companies anywhere (automatic, free tier)
      3. Fallback — manual lookup link for the right country registry
    """
    cc = jurisdiction.upper()[:2] if jurisdiction else ""

    if cc == "GB" and company_number:
        ch = fetch_financials_companies_house(company_number)
        if ch.get("found"):
            return ch
        return {
            **ch,
            "manual_source": "Companies House",
            "manual_url": ch.get("filing_url") or f"https://find-and-update.company-information.service.gov.uk/company/{company_number}/filing-history",
        }

    fmp = fetch_financials_fmp(name)
    if fmp.get("found"):
        return fmp

    source_name, source_url = FINANCIAL_SOURCE_URLS.get(cc, ("public registry / annual report", ""))
    return {
        "found": False,
        "reason": "Financials not available automatically",
        "manual_source": source_name,
        "manual_url": source_url,
    }


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def company_age_years(date_str: str):
    if not date_str:
        return None
    try:
        return (datetime.now() - datetime.strptime(date_str[:10], "%Y-%m-%d")).days / 365.25
    except Exception:
        return None


def country_risk_tier(country_code: str) -> int:
    return COUNTRY_RISK.get((country_code or "").upper(), 2)
