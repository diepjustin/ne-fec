"""Turn NE-filtered FEC bulk rows into the three processed CSVs.

    fec_contributions_ne.csv   sub_id, cmte_id, cmte_name, amndt_ind, rpt_tp,
                                transaction_tp, entity_tp, name, city, state,
                                zip, transaction_dt, transaction_amt, other_id,
                                tran_id, file_num, image_num, cycle,
                                source_url, source_snapshot
    fec_committees_ne.csv      cmte_id, cmte_name, treasurer_name, city, state,
                                zip, cmte_designation, cmte_type,
                                cmte_party_affiliation, org_type,
                                connected_org_name, cand_id, cycle,
                                source_url, source_snapshot
    fec_candidates_ne.csv      cand_id, cand_name, cand_party_affiliation,
                                cand_election_yr, cand_office_st, cand_office,
                                cand_office_district, cand_ici, cand_status,
                                cand_pcc, city, state, zip, cycle,
                                source_url, source_snapshot

Privacy (docs/PRIVACY.md rule 2, and `ne-connect/PLAN.md` Phase 3's explicit
instruction): EMPLOYER and OCCUPATION never reach `fec_contributions_ne.csv`.
They exist in the raw zip (immutable, untouched) and nowhere else this
project writes. Home street addresses (CAND_ST1/2, CMTE_ST1/2) are likewise
dropped down to city/state/zip in the processed candidate and committee
tables, same rule applied to organizations' registered addresses as
`ne-campaign-finance` applies to individual donors.

`entity_tp == "IND"` rows are real people, not organizations -- this module
does not decide how the hub treats them (that's `ne-connect/ingest/sources.py`
`load_fec_contributors()`, explicitly out of scope for this pass per the
task brief), it just carries `entity_tp` through unchanged so the hub can.

Dedup, in order:

1. **Amendment supersession** (the `include_in_total`/`AMNDT_IND` analogue
   `ne-campaign-finance/scripts/normalize.py` implements for NADC, applied
   to FEC's actual mechanism instead): group contribution rows by
   `(cmte_id, tran_id)` where `tran_id` is non-empty; within a group, keep
   only the row(s) with the highest `FILE_NUM` -- a later filing (higher
   file number) amending an earlier one supersedes it. Rows with an empty
   `tran_id` (some older cycles don't populate it) skip this step entirely
   and pass straight to step 2.
2. **Identity dedup**: within the survivors, drop exact repeats of the
   `sub_id` key; where `sub_id` is empty (pre-2004 cycles didn't have it),
   fall back to `(cycle, image_num, tran_id)`.

Rewritten from raw every run -- this module holds no accumulated state of
its own; `data/raw/` is the only thing that persists. `--cycle` accepts one
or more cycles (default: every cycle with raw data on disk) and combines
them into one write per output file -- calling this once per cycle used to
silently overwrite the shared output files with only the last cycle's rows
(a real bug, found on the first real multi-cycle pull, 2026-09-16); see
run()'s docstring.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from filter_ne import iter_all_committees, iter_ne_candidates, iter_ne_committees, iter_ne_individuals

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"

CONTRIBUTIONS_COLUMNS = [
    "sub_id",
    "cmte_id",
    "cmte_name",
    "amndt_ind",
    "rpt_tp",
    "transaction_tp",
    "entity_tp",
    "name",
    "city",
    "state",
    "zip",
    "transaction_dt",
    "transaction_amt",
    "other_id",
    "tran_id",
    "file_num",
    "image_num",
    "cycle",
    "source_url",
    "source_snapshot",
]

COMMITTEES_COLUMNS = [
    "cmte_id",
    "cmte_name",
    "treasurer_name",
    "city",
    "state",
    "zip",
    "cmte_designation",
    "cmte_type",
    "cmte_party_affiliation",
    "org_type",
    "connected_org_name",
    "cand_id",
    "cycle",
    "source_url",
    "source_snapshot",
]

CANDIDATES_COLUMNS = [
    "cand_id",
    "cand_name",
    "cand_party_affiliation",
    "cand_election_yr",
    "cand_office_st",
    "cand_office",
    "cand_office_district",
    "cand_ici",
    "cand_status",
    "cand_pcc",
    "city",
    "state",
    "zip",
    "cycle",
    "source_url",
    "source_snapshot",
]

FEC_IMAGE_BASE_URL = "https://docquery.fec.gov/cgi-bin/fecimg/?"
# Candidates and committees don't carry a per-row IMAGE_NUM; point at the
# public detail page instead, which is the closest thing FEC publishes to
# "the primary record" for a registration rather than a single filing image.
FEC_CANDIDATE_URL = "https://www.fec.gov/data/candidate/{cand_id}/"
FEC_COMMITTEE_URL = "https://www.fec.gov/data/committee/{cmte_id}/"


def supersede_amendments(rows: list[dict]) -> list[dict]:
    """Within each (cmte_id, tran_id) group, keep only the newest file_num."""
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    passthrough: list[dict] = []
    for row in rows:
        tran_id = row.get("TRAN_ID", "")
        if not tran_id:
            passthrough.append(row)
            continue
        grouped[(row["CMTE_ID"], tran_id)].append(row)

    survivors = list(passthrough)
    for group in grouped.values():
        if len(group) == 1:
            survivors.append(group[0])
            continue

        def file_num_key(r: dict) -> int:
            try:
                return int(r.get("FILE_NUM") or 0)
            except ValueError:
                return 0

        newest = max(file_num_key(r) for r in group)
        survivors.extend(r for r in group if file_num_key(r) == newest)
    return survivors


def dedup_by_identity(rows: list[dict], cycle: int) -> list[dict]:
    """Drop repeats of sub_id, falling back to (cycle, image_num, tran_id)."""
    seen: set[tuple] = set()
    out = []
    for row in rows:
        sub_id = row.get("SUB_ID", "")
        key = ("sub_id", sub_id) if sub_id else ("fallback", cycle, row.get("IMAGE_NUM", ""), row.get("TRAN_ID", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def build_contributions(
    raw_rows: Iterable[dict],
    *,
    cycle: int,
    cmte_names: dict[str, str],
    retrieved_at: str,
) -> list[dict]:
    rows = list(raw_rows)
    rows = supersede_amendments(rows)
    rows = dedup_by_identity(rows, cycle)

    out = []
    for r in rows:
        image_num = r.get("IMAGE_NUM", "")
        out.append(
            {
                "sub_id": r.get("SUB_ID", ""),
                "cmte_id": r["CMTE_ID"],
                "cmte_name": cmte_names.get(r["CMTE_ID"], ""),
                "amndt_ind": r.get("AMNDT_IND", ""),
                "rpt_tp": r.get("RPT_TP", ""),
                "transaction_tp": r.get("TRANSACTION_TP", ""),
                "entity_tp": r.get("ENTITY_TP", ""),
                "name": r.get("NAME", ""),
                "city": r.get("CITY", ""),
                "state": r.get("STATE", ""),
                "zip": r.get("ZIP_CODE", ""),
                "transaction_dt": r.get("TRANSACTION_DT", ""),
                "transaction_amt": r.get("TRANSACTION_AMT", ""),
                "other_id": r.get("OTHER_ID", ""),
                "tran_id": r.get("TRAN_ID", ""),
                "file_num": r.get("FILE_NUM", ""),
                "image_num": image_num,
                "cycle": cycle,
                "source_url": f"{FEC_IMAGE_BASE_URL}{image_num}" if image_num else "",
                "source_snapshot": retrieved_at,
            }
        )
    return out


def build_committees(raw_rows: Iterable[dict], *, cycle: int, retrieved_at: str) -> list[dict]:
    out = []
    for r in raw_rows:
        out.append(
            {
                "cmte_id": r["CMTE_ID"],
                "cmte_name": r.get("CMTE_NM", ""),
                "treasurer_name": r.get("TRES_NM", ""),
                "city": r.get("CMTE_CITY", ""),
                "state": r.get("CMTE_ST", ""),
                "zip": r.get("CMTE_ZIP", ""),
                "cmte_designation": r.get("CMTE_DSGN", ""),
                "cmte_type": r.get("CMTE_TP", ""),
                "cmte_party_affiliation": r.get("CMTE_PTY_AFFILIATION", ""),
                "org_type": r.get("ORG_TP", ""),
                "connected_org_name": r.get("CONNECTED_ORG_NM", ""),
                "cand_id": r.get("CAND_ID", ""),
                "cycle": cycle,
                "source_url": FEC_COMMITTEE_URL.format(cmte_id=r["CMTE_ID"]),
                "source_snapshot": retrieved_at,
            }
        )
    return out


def build_candidates(raw_rows: Iterable[dict], *, cycle: int, retrieved_at: str) -> list[dict]:
    out = []
    for r in raw_rows:
        out.append(
            {
                "cand_id": r["CAND_ID"],
                "cand_name": r.get("CAND_NAME", ""),
                "cand_party_affiliation": r.get("CAND_PTY_AFFILIATION", ""),
                "cand_election_yr": r.get("CAND_ELECTION_YR", ""),
                "cand_office_st": r.get("CAND_OFFICE_ST", ""),
                "cand_office": r.get("CAND_OFFICE", ""),
                "cand_office_district": r.get("CAND_OFFICE_DISTRICT", ""),
                "cand_ici": r.get("CAND_ICI", ""),
                "cand_status": r.get("CAND_STATUS", ""),
                "cand_pcc": r.get("CAND_PCC", ""),
                "city": r.get("CAND_CITY", ""),
                "state": r.get("CAND_ST", ""),
                "zip": r.get("CAND_ZIP", ""),
                "cycle": cycle,
                "source_url": FEC_CANDIDATE_URL.format(cand_id=r["CAND_ID"]),
                "source_snapshot": retrieved_at,
            }
        )
    return out


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _discover_cycles(raw_dir: Path) -> list[int]:
    """Every cycle this repo has ever pulled raw data for, oldest first."""
    if not raw_dir.exists():
        return []
    return sorted(int(p.name) for p in raw_dir.iterdir() if p.is_dir() and p.name.isdigit())


def run(
    cycles: Iterable[int], *, retrieved_at: str, raw_dir: Path = RAW_DIR, out_dir: Path = None
) -> list[dict]:
    """Combine every given cycle's NE-filtered rows into one set of processed
    CSVs, in a single write per file.

    Still "rewritten from raw every run, no accumulated state of its own"
    (see module docstring) -- multi-cycle here means reading every requested
    cycle's raw files together in one pass, not merging with a PRIOR run's
    output. Real bug found on the first real multi-cycle pull (2026-09-16):
    calling this once per cycle used to truncate-and-overwrite the shared
    output files each time, so `--cycle 2024` followed by `--cycle 2026`
    silently discarded 2024's 250,610 contribution rows and left only
    2026's. Pass every cycle you want in the output in one call.
    """
    out_dir = out_dir or DATA_DIR
    all_candidates: list[dict] = []
    all_committees: list[dict] = []
    all_contributions: list[dict] = []
    results = []

    for cycle in cycles:
        cycle_dir = raw_dir / str(cycle)

        cn_zip = cycle_dir / f"cn{cycle % 100:02d}.zip"
        cm_zip = cycle_dir / f"cm{cycle % 100:02d}.zip"
        indiv_zip = cycle_dir / f"indiv{cycle % 100:02d}.zip"

        candidates_raw = list(iter_ne_candidates(cn_zip)) if cn_zip.exists() else []
        committees_raw = list(iter_ne_committees(cm_zip)) if cm_zip.exists() else []

        cmte_names: dict[str, str] = {}
        if cm_zip.exists():
            # Whole (small) cm file, for cmte_id -> name lookup only -- see
            # filter_ne.iter_all_committees' docstring for why this is
            # exempt from the never-hold-the-national-file rule.
            cmte_names = {r["CMTE_ID"]: r.get("CMTE_NM", "") for r in iter_all_committees(cm_zip)}

        contributions_raw = list(iter_ne_individuals(indiv_zip)) if indiv_zip.exists() else []

        candidates = build_candidates(candidates_raw, cycle=cycle, retrieved_at=retrieved_at)
        committees = build_committees(committees_raw, cycle=cycle, retrieved_at=retrieved_at)
        contributions = build_contributions(
            contributions_raw, cycle=cycle, cmte_names=cmte_names, retrieved_at=retrieved_at
        )

        all_candidates += candidates
        all_committees += committees
        all_contributions += contributions
        results.append(
            {
                "cycle": cycle,
                "candidates": len(candidates),
                "committees": len(committees),
                "contributions": len(contributions),
            }
        )

    write_csv(out_dir / "fec_candidates_ne.csv", CANDIDATES_COLUMNS, all_candidates)
    write_csv(out_dir / "fec_committees_ne.csv", COMMITTEES_COLUMNS, all_committees)
    write_csv(out_dir / "fec_contributions_ne.csv", CONTRIBUTIONS_COLUMNS, all_contributions)

    return results


def main(argv=None) -> int:
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cycle", type=int, nargs="+", default=None,
        help="one or more cycles to combine (default: every cycle with raw data under data/raw/)",
    )
    args = parser.parse_args(argv)

    cycles = args.cycle or _discover_cycles(RAW_DIR)
    if not cycles:
        print("no cycles found under data/raw/ -- nothing to normalize", file=sys.stderr)
        return 1

    results = run(cycles, retrieved_at=datetime.now(timezone.utc).isoformat())
    for result in results:
        print(
            f"cycle {result['cycle']}: {result['candidates']} candidates, "
            f"{result['committees']} committees, {result['contributions']} contributions"
        )
    print(
        f"combined: {sum(r['contributions'] for r in results)} contributions "
        f"across {len(results)} cycle(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
