# NE FEC (federal campaign finance, Nebraska slice)

**Status: recon done, scaffolding built, validated against one real cycle's small
files.** Terms-of-use gate cleared 2026-09-15: FEC's `robots.txt` does not disallow
`/files/bulk-downloads/`, and no terms-of-use page forbidding automated bulk download
was found — see "Terms of use" below. Pulled the real 2024-cycle `cn24.zip` (candidates,
356,397 bytes) and `cm24.zip` (committees, 883,457 bytes) and ran the full pipeline
end to end: **51 Nebraska candidates, 97 Nebraska committees**, `check_data.py` clean
(no duplicate keys, no `employer`/`occupation` columns). 21 tests, no network. The
multi-GB `indiv<yy>.zip` (itemized individual contributions — 4.24 GB for 2024 alone)
was deliberately **not** pulled tonight; see "What's still open" below. Not yet wired
into `ne-connect`'s hub — that's separate follow-up work.

A sibling to [`ne-campaign-finance`](https://github.com/diepjustin/ne-campaign-finance)
and [`ne-lobbying`](https://github.com/diepjustin/ne-lobbying) in the
[`ne-connect`](https://github.com/diepjustin/ne-connect) federation: same house rules
(plain Python, `requests` + stdlib `csv`, no framework, raw captures immutable, every
displayed fact carries a source URL and retrieval date), applied here to the FEC's own
public bulk-download files instead of a state agency's export.

**This repo currently has no GitHub remote.** It stays local until a human decides to
publish it.

## Where the data comes from

FEC bulk files, per two-year election cycle, at
`https://www.fec.gov/files/bulk-downloads/<YYYY>/`:

| file | contents | 2024-cycle size |
|---|---|---|
| `cn<yy>.zip` | candidate registry | 356 KB |
| `cm<yy>.zip` | committee registry | 883 KB |
| `indiv<yy>.zip` | itemized individual contributions (>$200) | **4.24 GB** |
| `pas2<yy>.zip` | committee-to-committee / candidate transactions | not yet pulled |
| `oth<yy>.zip` | other inter-committee transactions | not yet pulled |

No API key, no rate-limited API quota — this is FEC's own public reproducible-snapshot
mechanism, the same tier CalMatters-style scrapers use nationally. Column layouts come
from FEC's own `data_dictionaries/{cn,cm,indiv}_header_file.csv`, transcribed into
`scripts/filter_ne.py` from a live pull on 2026-09-15 (see that module's docstring for
the exact URLs) — not guessed from FEC's format-description PDFs, on the same theory
`ne-campaign-finance/scripts/validate.py` documents for why NADC's layout PDFs disagree
with its real export.

## Terms of use

Checked before writing a line of scraper code, per `CLAUDE.md` rule 6 (this project's
`ne-connect/docs/DATA_SOURCES.md` records the same finding):

- `https://www.fec.gov/robots.txt` has no `Disallow` covering `/files/bulk-downloads/`
  (its disallow list targets the FEC's *search* endpoints — `/data/candidates/?*`,
  `/data/receipts/?*`, etc. — not the static bulk-download tree).
- No terms-of-use page on `fec.gov` forbidding automated or bulk access was found.
  `api.open.fec.gov` (the FEC's *API*) has its own separate rate-limited-key terms —
  irrelevant here since this project uses the bulk-download files, not the API.
- The one substantive legal restriction that does apply: FEC's ["Sale or use of
  contributor information"](https://www.fec.gov/updates/sale-or-use-contributor-information/)
  notice (citing the Federal Election Campaign Act) prohibits selling or using
  individual-contributor information (name, address, employer, occupation) "for
  soliciting contributions ... or for any commercial purpose." It explicitly carves out
  "the use of individual contributor information in newspapers, magazines, books or
  similar communications, as long as the principal purpose ... is not to solicit
  contributions or conduct commercial activity" — the exact shape of this project. This
  restriction is also why `EMPLOYER`/`OCCUPATION` never reach processed output here
  (privacy parity with NADC handling, `ne-connect/docs/PRIVACY.md`), independent of
  whether the legal exemption applies.

## Scope

**In (this pass):** candidates, committees, and the pipeline mechanics for
contributions (built and tested, not yet run against a real `indiv<yy>.zip`).

**Out (this pass, explicitly):**
- Any real pull of `indiv<yy>.zip` — see "What's still open."
- `pas2`/`oth` (committee-to-committee transactions) — scaffolded filenames only,
  no parser yet.
- Hub integration (`ne-connect/ingest/sources.py` `load_fec_contributors()`, bit 16,
  the `ne-fec-weekly.yml` workflow) — separate follow-up, not touched here.

## The scale problem (indiv files)

`indiv24.zip` alone is 4.24 GB compressed. At typical broadband speeds that's roughly
10–60 minutes to *download* (11 min at 50 Mbps, 57 min at 10 Mbps) before any parsing —
and every cycle back to 2000 has its own `indiv<yy>.zip`, most similarly large in recent
cycles. `scripts/download_bulk.py` streams to disk in 1 MiB chunks and never buffers
the whole body (verified: `stream_download`'s peak memory is independent of file size),
so the *download* itself is safe to run unattended; `scripts/filter_ne.py`'s
`iter_ne_individuals` streams the unzipped member line by line for the same reason on
the parse side. What has **not** been measured yet: real wall-clock time to
stream-decode and filter a full `indiv<yy>.zip` end to end, or how many Nebraska rows
survive out of the (typically tens of millions) national rows in a modern cycle. Budget
a weekly, not nightly, cadence for this file per `ne-connect/PLAN.md`'s Phase 3 plan —
one polite GET per cycle, cached, never re-fetched unless the sha256 changes.

**Historical scope**: `.github/workflows/ne-fec-weekly.yml`'s `FEC_START_CYCLE` (2024)
is the earliest cycle this project pulls automatically — there is no other "cycles we
cover" constant anywhere in this repo, so this is the one place that decision lives.
Every run pulls every cycle from `FEC_START_CYCLE` up to the current one (each a no-op
once cached, per `download_bulk.py`'s own skip-if-exists check), so a fresh/evicted
cache self-heals on its own next run rather than silently staying short a cycle. Change
this value here (and nowhere else) if the intended historical range changes.

## Schema (processed output)

`fec_contributions_ne.csv`: `sub_id, cmte_id, cmte_name, amndt_ind, rpt_tp,
transaction_tp, entity_tp, name, city, state, zip, transaction_dt, transaction_amt,
other_id, tran_id, file_num, image_num, cycle, source_url, source_snapshot`.
`source_url` is `https://docquery.fec.gov/cgi-bin/fecimg/?<IMAGE_NUM>` — FEC's own
scanned-image viewer for the filing that reported the transaction, the closest thing to
"the primary record" a bulk row has.

`fec_committees_ne.csv` / `fec_candidates_ne.csv`: registry rows, city/state/zip only
(no street address — same privacy stance as individual contributors, applied to
organizations' own registered addresses), linking to
`https://www.fec.gov/data/committee/<CMTE_ID>/` / `.../candidate/<CAND_ID>/`.

**`EMPLOYER` and `OCCUPATION` are dropped before any processed CSV is written.** They
exist in the raw zip (untouched) and nowhere else `normalize.py` writes.

Dedup: `sub_id` primary key, falling back to `(cycle, image_num, tran_id)` for older
cycles that predate `SUB_ID`. Before that: amendment supersession — where the same
`(cmte_id, tran_id)` appears more than once (a filing amending an earlier one), only the
row with the highest `FILE_NUM` survives. `scripts/check_data.py` asserts both the key
uniqueness and that `employer`/`occupation` never appear as columns at all.

## Running it

```
python3 -m venv venv && venv/bin/pip install -r requirements.txt
venv/bin/python3 -m pytest tests -q                          # 21 tests, no network

venv/bin/python3 scripts/download_bulk.py --cycle 2024 --datasets cn cm
venv/bin/python3 scripts/normalize.py --cycle 2024
venv/bin/python3 scripts/check_data.py
```

Add `indiv` to `--datasets` only when you mean to pull the multi-GB file and have the
disk space and patience for it; it is not in the default dataset list.

## Architecture

```
scripts/
  download_bulk.py   streamed fetch -> data/raw/<cycle>/<dataset><yy>.zip, sha256'd
  filter_ne.py        stream-decode pipe-delimited rows, filter to NE
  normalize.py         NE-filtered raw -> the three processed CSVs
  check_data.py        key-uniqueness + privacy-column gate
data/
  raw/<cycle>/          gitignored, immutable once pulled
  data_dictionaries/     (reserved; headers are hardcoded in filter_ne.py from a
                          real pull rather than fetched at runtime, matching
                          this project's convention of pinning schema to a
                          verified snapshot rather than trusting a live fetch)
  scrape_meta.json       sha256 + byte count + retrieval date per (cycle, dataset)
  fec_*_ne.csv            gitignored, rebuilt from raw every normalize.py run
tests/
  fixtures/               trimmed, real-shaped zips (not real FEC records)
```

## Guard rails

- Descriptive `User-Agent` naming the project and a contact email
  (`ne-fec-scraper/0.1 (...; contact: sdiepxj367@gmail.com)`), same pattern as
  `ne-campaign-finance/scripts/download_extracts.py`.
- Streamed downloads, chunked hashing — no full-file buffering regardless of size.
- A polite gap between files in the same run, exponential backoff on network errors,
  and a `Retry-After`-aware pause on 429/503.
- `data/raw/`, `data/processed/`, and every `fec_*_ne.csv` are gitignored — big,
  regenerable, and (for `indiv`) potentially multi-GB.
- Never load `indiv<yy>.zip` into memory as a list — `iter_ne_individuals` is a
  generator all the way through; `normalize.py`'s in-memory dedup step happens only
  on the already-NE-filtered slice, which is Nebraska-scale, not national-scale.

## What's still open

- A real pull and timing of a full `indiv<yy>.zip` — download time, stream-decode
  time, and the real NE row count are all unmeasured. Budget this as its own
  bounded run, not part of routine scaffolding work.
- `pas2`/`oth` parsing (committee-to-committee and other inter-committee transactions).
- Hub integration: `ne-connect/ingest/sources.py` `load_fec_contributors()`, source
  bit 16, `LAST, FIRST` name parsing per `sources.py:130-146`, the `IND`-never-auto-merge
  rule, and the `ne-fec-weekly.yml` workflow. All explicitly out of scope for this pass.
- No GitHub remote yet — this repo stays local-only until a human decides to publish it.
