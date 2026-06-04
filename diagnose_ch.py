"""
diagnose_ch.py
Run this once against a known company number to see exactly what
Companies House returns and where our extraction succeeds/fails.

Usage:
    python3 diagnose_ch.py 02763384
    python3 diagnose_ch.py <any-UK-company-number>

Prints every step of the fetch pipeline so you can see where it breaks.
"""

import os
import sys
import json
import re
from pathlib import Path

# Load CH key from .streamlit/secrets.toml if present, otherwise from env
def _load_key():
    p = Path(".streamlit/secrets.toml")
    if p.exists():
        for line in p.read_text().splitlines():
            m = re.match(r'\s*COMPANIES_HOUSE_API_KEY\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    return os.getenv("COMPANIES_HOUSE_API_KEY", "")

API_KEY = _load_key()
if not API_KEY:
    sys.exit("No COMPANIES_HOUSE_API_KEY found in .streamlit/secrets.toml or env")

import requests

if len(sys.argv) < 2:
    sys.exit("Usage: python3 diagnose_ch.py <company-number>")

company_number = sys.argv[1].strip()
print(f"\n{'='*70}")
print(f"Diagnosing company {company_number}")
print(f"{'='*70}\n")

# ── Step 1 — filing history ──────────────────────────────────
print("STEP 1: Filing history (accounts only, 25 most recent)")
r = requests.get(
    f"https://api.company-information.service.gov.uk/company/{company_number}/filing-history",
    params={"category": "accounts", "items_per_page": 25},
    auth=(API_KEY, ""), timeout=15,
)
print(f"  Status: {r.status_code}")
if r.status_code != 200:
    print(f"  Body: {r.text[:500]}")
    sys.exit(1)
items = r.json().get("items", [])
print(f"  Found {len(items)} accounts filings\n")
for i, f in enumerate(items[:10]):
    print(f"  [{i}] {f.get('date','?')}  type={f.get('type','?'):8s}  desc={f.get('description','')[:60]}")
    print(f"        document_metadata: {'YES' if (f.get('links') or {}).get('document_metadata') else 'NO'}")

# ── Step 2 — for each filing, inspect document metadata ─────
print("\nSTEP 2: Per-filing document metadata inspection")
for i, f in enumerate(items[:5]):
    meta_url = (f.get("links") or {}).get("document_metadata", "")
    if not meta_url:
        print(f"  [{i}] No document_metadata link — skip")
        continue
    print(f"  [{i}] {f.get('date','?')}  type={f.get('type','?')}")
    print(f"        meta_url: {meta_url}")
    rm = requests.get(meta_url, auth=(API_KEY, ""), timeout=15)
    print(f"        meta status: {rm.status_code}")
    if rm.status_code != 200:
        print(f"        body: {rm.text[:200]}")
        continue
    meta = rm.json()
    resources = meta.get("resources", {}) or {}
    print(f"        available content_types: {list(resources.keys())}")
    doc_url = (meta.get("links") or {}).get("document", "")
    print(f"        document URL: {doc_url}")

    has_ixbrl = any("xhtml" in ct.lower() or "xbrl" in ct.lower() for ct in resources)
    has_pdf   = "application/pdf" in resources
    print(f"        has_ixbrl: {has_ixbrl}   has_pdf: {has_pdf}")

    # ── Step 3 — try to fetch the iXBRL content ───────────
    if has_ixbrl and doc_url:
        print(f"        → fetching iXBRL content…")
        rc = requests.get(
            doc_url, auth=(API_KEY, ""),
            headers={"Accept": "application/xhtml+xml"},
            timeout=30,
        )
        print(f"          status: {rc.status_code}")
        print(f"          content-type: {rc.headers.get('Content-Type','?')}")
        print(f"          content length: {len(rc.content):,} bytes")
        if rc.status_code == 200 and rc.content:
            text = rc.text
            # Probe for common turnover/profit tags
            tags_found = {}
            for tag in ["Turnover", "Revenue", "TurnoverRevenue",
                        "ProfitLossOnOrdinaryActivitiesBeforeTax",
                        "ProfitLoss", "ProfitLossForPeriod",
                        # Small-company filings often have these instead:
                        "FixedAssets", "CurrentAssets", "TotalEquity",
                        "ShareholderFunds"]:
                matches = re.findall(
                    rf'<[^>]*\bname="[^"]*:?{tag}"[^>]*>([^<]+)<', text, re.IGNORECASE,
                )
                if matches:
                    tags_found[tag] = matches[:3]
            if tags_found:
                print(f"          iXBRL tags present:")
                for tag, vals in tags_found.items():
                    print(f"            {tag}: {vals}")
            else:
                print(f"          ⚠️  no recognisable iXBRL tags in this document")
                # Show first 500 chars to debug
                print(f"          first 500 chars: {text[:500]!r}")
    print()

print("="*70)
print("DONE")
print("="*70)
