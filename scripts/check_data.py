"""Assert key uniqueness on this project's outputs.

Same contract as `ne-campaign-finance/scripts/validate.py` and
`ne-lobbying/scripts/check_data.py`: rerunning `normalize.py` must never
leave a duplicate key in any processed CSV, since the hub joins on these
keys. Runs before `build_site.py` would run (out of scope this pass) and in
CI once the workflow lands.

    fec_contributions_ne.csv   sub_id (falls back to (cycle, image_num, tran_id)
                                when sub_id is blank -- pre-2004 cycles)
    fec_committees_ne.csv      (cmte_id, cycle)
    fec_candidates_ne.csv      (cand_id, cycle)

Also asserts the privacy rule directly: EMPLOYER/OCCUPATION must not appear
as columns in fec_contributions_ne.csv at all, not just be blank -- a column
existing invites a future writer to populate it without re-reading
docs/PRIVACY.md.

Usage:
    python scripts/check_data.py            # data/ under this project
    python scripts/check_data.py DIR        # any directory of the same files
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FORBIDDEN_CONTRIBUTION_COLUMNS = {"employer", "occupation", "EMPLOYER", "OCCUPATION"}


def read_rows(path: Path):
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def contribution_key(row: dict) -> tuple:
    if row.get("sub_id"):
        return ("sub_id", row["sub_id"])
    return ("fallback", row.get("cycle", ""), row.get("image_num", ""), row.get("tran_id", ""))


def duplicate_keys(rows: list[dict], key_fn) -> list[tuple]:
    counts = Counter(key_fn(r) for r in rows)
    return sorted((k, n) for k, n in counts.items() if n > 1)


def check(data_dir: Path = None) -> dict:
    data_dir = Path(data_dir or DATA_DIR)
    report = {"ok": True, "files": {}}

    contributions_path = data_dir / "fec_contributions_ne.csv"
    if contributions_path.exists():
        rows = read_rows(contributions_path)
        dups = duplicate_keys(rows, contribution_key)
        forbidden = FORBIDDEN_CONTRIBUTION_COLUMNS & set(rows[0].keys() if rows else [])
        report["files"]["fec_contributions_ne.csv"] = {
            "rows": len(rows),
            "duplicate_keys": len(dups),
            "sample": dups[:3],
            "forbidden_columns_present": sorted(forbidden),
        }
        if dups or forbidden:
            report["ok"] = False

    committees_path = data_dir / "fec_committees_ne.csv"
    if committees_path.exists():
        rows = read_rows(committees_path)
        dups = duplicate_keys(rows, lambda r: (r.get("cmte_id", ""), r.get("cycle", "")))
        report["files"]["fec_committees_ne.csv"] = {
            "rows": len(rows),
            "duplicate_keys": len(dups),
            "sample": dups[:3],
        }
        if dups:
            report["ok"] = False

    candidates_path = data_dir / "fec_candidates_ne.csv"
    if candidates_path.exists():
        rows = read_rows(candidates_path)
        dups = duplicate_keys(rows, lambda r: (r.get("cand_id", ""), r.get("cycle", "")))
        report["files"]["fec_candidates_ne.csv"] = {
            "rows": len(rows),
            "duplicate_keys": len(dups),
            "sample": dups[:3],
        }
        if dups:
            report["ok"] = False

    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    data_dir = Path(argv[0]) if argv else DATA_DIR
    report = check(data_dir)

    for name, info in report["files"].items():
        print(f"{name}: {info}")

    if not report["ok"]:
        print("FAIL: duplicate keys or forbidden columns found", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
