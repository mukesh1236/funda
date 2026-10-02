# 4. SEC EDGAR as the fund fact-sheet source

- **Status:** Accepted
- **Date:** 2026-08-10
- **Implemented in:** `ace51f5` (EDGAR service), `c473b6d` (summary)

## Context

The first "fund RAG" retrieved passages the app had itself generated from structured
numbers, so the "grounded in the fund's filing" claim was false. A real source
document was needed for US funds that is authoritative, free, and available for
every fund.

## Decision

Use **SEC EDGAR** filings, best first: **497K** (Summary Prospectus), then **497**,
then **485BPOS**, with **N-CSR** for manager commentary. Parse by the section
headings **Form N-1A mandates**, and cite the filing in answers.

A 497K is 4 to 10 pages and Items 2 to 8 of Form N-1A require exactly the sections a
retail reader needs, so it *is* the fact sheet.

## Consequences

- Free and authoritative, with a stable identity (`accession`) that keys the stored
  summary so it regenerates only when the fund files something new.
- Mandated headings make parsing tractable, but filings vary. A trust files one
  `485BPOS` covering dozens of series, so the parser must prove which span belongs
  to the requested fund or refuse (`locate_fund_span`). **A wrong-fund summary is
  the worst possible failure**, so refusal is the intended outcome of doubt.
- US only. India needs a separate service (AMFI NAV and portfolio disclosures); the
  two share only the `DocFetcher` protocol.
- **The sandbox cannot reach `sec.gov`.** Form coverage and parsing are proven
  against fixtures; live fetching has only been verified in production.

## Alternatives considered

- **Scraping AMC factsheet PDFs:** inconsistent layouts per provider, legally and
  technically fragile.
- **yfinance descriptions only:** thin, unattributed, not a primary source.
- **A third-party filings API:** a paid dependency for data that is free at the
  source.
