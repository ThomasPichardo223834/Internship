# IFC Prospect Lookup

**Author:** Thomas Pichardo
**Organisation:** International Furan Chemicals (IFC)
**Institution:** Breda University of Applied Sciences (BUaS)
**Programme:** Data Science & AI
**Year:** 2026

> A data-driven credit-risk screening tool for IFC's Finance & Control team.
> Designed as a graduation-internship project to let IFC assess new B2B prospects
> without subscribing to a commercial credit bureau (Graydon, Creditsafe, Orbis).

---

## What it does

A single-page Streamlit app with one workflow:

**Search → Pick company → Enter financials → Get assessment.**

1. User types a company name (optionally picks a country).
2. OpenCorporates returns up to 5 matches — user picks the right one (company number is shown to disambiguate).
3. The tool fetches, **in parallel**, from: GLEIF, EU Sanctions list, Companies House (UK only), and NewsData.io.
4. For UK companies: revenue, net profit, and up to 3 years of trend are extracted automatically from iXBRL accounts. Health flags (overdue accounts, liquidation, administration) are surfaced from the Companies House profile endpoint.
5. For non-UK companies: a direct link to the correct national registry is shown for manual entry.
6. A 0–100 score and one of three verdicts (**Proceed** / **Caution** / **Do Not Proceed**) is produced, with a plain-language summary and a downloadable JSON of the full assessment for audit.

---

## UK-first scoping — deliberate choice

Automatic financial extraction is full-service for UK-registered entities via the Companies House Document API. This is a deliberate scoping decision driven by what is freely available:

- **UK:** Companies House publishes iXBRL-tagged accounts via a free, unlimited API. Revenue, profit, and multi-year trend are all machine-readable.
- **NL, DE, FR, etc.:** public registries exist (KVK, Bundesanzeiger, Infogreffe) but either block automation, provide PDF-only filings, or sit behind paid access.

For non-UK prospects the tool therefore operates in a **semi-automated** mode: identity, sanctions, news and corporate-registry data are auto-fetched, and the user pastes revenue/profit from the company's annual report.

---

## Risk scoring methodology

Total possible score: **100**. Revenue and profit together account for 60 points — reflecting the supervisor's direction that financial standing is the primary signal.

| Component        | Weight | Source                                               |
|------------------|-------:|------------------------------------------------------|
| Annual Revenue   |     40 | Companies House iXBRL · FMP · Manual entry           |
| Net Result       |     20 | Companies House iXBRL · FMP · Manual entry           |
| Legal Status     |     15 | OpenCorporates                                       |
| Company Age      |     10 | OpenCorporates (incorporation date)                  |
| Country Risk     |     10 | Jurisdiction tier (1 = low, 2 = medium, 3 = high)   |
| LEI Verification |      5 | GLEIF                                                |

### Modifiers (can reduce the score)

| Modifier                                | Effect   |
|-----------------------------------------|----------|
| Revenue declining YoY (≥5%)             | −5 pts   |
| Accounts overdue at Companies House     | −8 pts   |
| Confirmation statement overdue          | −3 pts   |
| Prior insolvency history                | −5 pts   |
| ≥2 credit-negative news articles        | −5 pts   |
| 1 credit-negative news article          | −2 pts   |

### Hard stops (immediate *Do Not Proceed*)

- Sanctioned entity (EU Consolidated List, exact normalised-name match)
- Currently in liquidation or administration
- Dissolved or otherwise inactive

### Verdict bands

| Score   | Verdict                 |
|---------|-------------------------|
| 70–100  | ✅ Proceed              |
| 40–69   | ⚠️ Proceed with Caution |
| 0–39    | 🚫 Do Not Proceed       |

If revenue is missing, the verdict is capped at **Caution** regardless of other signals.

---

## Data sources

| Source                              | Used for                                    | Auth          | Free?    |
|-------------------------------------|---------------------------------------------|---------------|----------|
| OpenCorporates                      | Company identity, legal status, incorporation | API key     | 500/day  |
| GLEIF                               | LEI verification                            | None          | Unlimited |
| Companies House (UK)                | iXBRL accounts, overdue flags, insolvency   | API key       | Unlimited |
| Financial Modeling Prep             | Listed-company revenue fallback             | API key       | Free tier |
| NewsData.io                         | Recent news headlines                       | API key       | 200/day  |
| EU Consolidated Sanctions List      | Sanctions screening                         | None          | Unlimited |
| ECB Foreign Exchange Reference Rates| Live GBP→EUR conversion                     | None          | Unlimited |

---

## Project structure

```
IFC-Dashboard/
├── app.py                   # Single-page Streamlit app — search/pick/enter/score
├── utils/
│   ├── auth.py              # Shared password gate
│   └── fetchers.py          # All external API calls
├── .streamlit/
│   ├── config.toml          # IFC navy/teal theme
│   └── secrets.toml         # API keys (gitignored)
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Running locally

```bash
git clone <repo>
cd IFC-Dashboard
pip install -r requirements.txt
# Fill in .streamlit/secrets.toml with API keys (see below)
streamlit run app.py
```

Opens at `http://localhost:8501`.

---

## Deploying to Streamlit Community Cloud

1. Push the repo to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create a new app pointing to `app.py`.
3. Under **Settings → Secrets**, paste the contents of `.streamlit/secrets.toml`.
4. Deploy.

The app is protected by a shared password (`DASHBOARD_PASSWORD` secret). Rate-limiting caps each browser session at 20 lookups to protect the 200/day NewsData quota.

---

## Thesis context

**Research question:**
> How can a data-driven credit-risk assessment tool be built for a B2B specialty-chemicals distributor using free public data sources, to support prospect-screening decisions without reliance on commercial credit-bureau subscriptions?

**Key methodological contribution:**
An iXBRL extraction pipeline against the Companies House Document API that handles (a) the multi-step document-metadata→content redirect, (b) iXBRL `scale` and `sign` attributes that cause raw-value misreads if ignored, and (c) PDF-only filings (common for UK micro-entities) with a graceful fallback to manual entry.

---

*Last updated: April 2026*
