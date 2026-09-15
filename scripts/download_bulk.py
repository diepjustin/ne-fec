"""Download FEC bulk-data files, streamed, never held fully in memory.

Fetches `<dataset><yy>.zip` from `https://www.fec.gov/files/bulk-downloads/<YYYY>/`
(the public, non-API bulk-download tree -- confirmed 2026-09-15 against
`https://www.fec.gov/robots.txt` (no `Disallow` covering `/files/bulk-downloads/`)
and the FEC's "Sale or use of contributor information" notice
(`https://www.fec.gov/updates/sale-or-use-contributor-information/`), which
restricts commercial/soliciting use of individual-contributor data but
explicitly carves out "newspapers, magazines, books or similar communications"
-- this project is exactly that use. No terms-of-use page forbidding
automated bulk download was found; see `ne-connect/docs/DATA_SOURCES.md`.

`indiv<yy>.zip` (itemized individual contributions) is multi-GB -- as of the
2024 cycle, 4.24 GB for that one file alone. This script streams every
download straight to disk in chunks (`requests.get(..., stream=True)`) and
never calls `.content` or `.read()` on the whole body, so peak memory stays
flat regardless of file size.

Writes to `data/raw/<cycle>/<dataset><yy>.zip`, recording sha256 + byte count
+ retrieval date per file in `data/scrape_meta.json`. A rerun with an
unchanged sha256 is skipped (`--force` overrides); the FEC updates these
files continuously as filings are amended, so a changed sha256 on a rerun is
expected, not an error -- it's the state's own data changing under us, same
as `ne-campaign-finance/scripts/download_legacy.py`'s handling of its zip.

Usage:
    python scripts/download_bulk.py --cycle 2024 --datasets cn cm
    python scripts/download_bulk.py --cycle 2024 --datasets indiv --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://www.fec.gov/files/bulk-downloads"

# Matches the pattern in ne-campaign-finance/scripts/download_extracts.py:
# descriptive, identifies the project, and gives FEC a contact address.
DEFAULT_USER_AGENT = (
    "ne-fec-scraper/0.1 " "(https://github.com/diepjustin/ne-fec; contact: sdiepxj367@gmail.com)"
)

# dataset short name -> filename template (FEC's own naming, `<name><yy>.zip`)
FILENAME_TEMPLATES = {
    "cn": "cn{yy}.zip",  # candidates
    "cm": "cm{yy}.zip",  # committees
    "indiv": "indiv{yy}.zip",  # individual contributions -- multi-GB
    "pas2": "pas2{yy}.zip",  # committee-to-committee / candidate transactions
    "oth": "oth{yy}.zip",  # other transactions between committees
}
DEFAULT_DATASETS = ["cn", "cm"]  # the two small, always-useful ones

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
META_PATH = DATA_DIR / "scrape_meta.json"

CHUNK_SIZE = 1024 * 1024  # 1 MiB
BE_POLITE_DELAY_SECONDS = 2.0  # between files, on top of any Retry-After
MAX_RETRIES = 3


def cycle_to_yy(cycle: int) -> str:
    """2024 -> "24". FEC bulk filenames use the two-digit cycle-ending year."""
    return f"{cycle % 100:02d}"


def bulk_url(cycle: int, dataset: str) -> str:
    filename = FILENAME_TEMPLATES[dataset].format(yy=cycle_to_yy(cycle))
    return f"{BASE_URL}/{cycle}/{filename}"


def load_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text())
    return {}


def save_meta(meta: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")


def stream_download(url: str, dest: Path, *, user_agent: str, timeout: int = 120) -> dict:
    """GET `url`, writing chunks to `dest` as they arrive. Returns sha256/size.

    Never buffers the whole body: `iter_content` yields chunks off the wire,
    each chunk is written and hashed immediately, then dropped. Peak memory
    is O(CHUNK_SIZE), not O(file size) -- the property this whole script
    exists to guarantee for the multi-GB indiv files.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    sha256 = hashlib.sha256()
    total_bytes = 0

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with requests.get(
                url, headers={"User-Agent": user_agent}, stream=True, timeout=timeout
            ) as resp:
                if resp.status_code == 429 or resp.status_code == 503:
                    retry_after = float(resp.headers.get("Retry-After", 10))
                    print(f"    rate-limited ({resp.status_code}), sleeping {retry_after}s")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                sha256 = hashlib.sha256()
                total_bytes = 0
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        sha256.update(chunk)
                        total_bytes += len(chunk)
            tmp.replace(dest)
            return {"sha256": sha256.hexdigest(), "bytes": total_bytes}
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt == MAX_RETRIES:
                raise
            backoff = BE_POLITE_DELAY_SECONDS * (2**attempt)
            print(f"    network error ({exc}), retry {attempt}/{MAX_RETRIES} in {backoff}s")
            time.sleep(backoff)
    raise RuntimeError(f"exhausted retries for {url}")


def download_dataset(
    cycle: int,
    dataset: str,
    *,
    user_agent: str,
    force: bool,
    run_date: date,
) -> dict:
    url = bulk_url(cycle, dataset)
    filename = FILENAME_TEMPLATES[dataset].format(yy=cycle_to_yy(cycle))
    dest = RAW_DIR / str(cycle) / filename

    meta = load_meta()
    cycle_meta = meta.setdefault(str(cycle), {})
    previous = cycle_meta.get(dataset)

    if dest.exists() and not force:
        print(f"  {dataset} {cycle}: already have {dest.name}, skipping (--force to refetch)")
        return {"dataset": dataset, "cycle": cycle, "skipped": True, "path": str(dest)}

    print(f"  {dataset} {cycle}: GET {url}")
    result = stream_download(url, dest, user_agent=user_agent)

    if previous and previous["sha256"] == result["sha256"]:
        print(f"    unchanged (sha256 {result['sha256'][:12]}...)")
    elif previous:
        print(
            f"    sha256 changed since last pull ({previous['sha256'][:12]}... ->"
            f" {result['sha256'][:12]}...) -- the FEC updates these files continuously"
            " as filings are amended; this is expected, not an error"
        )

    cycle_meta[dataset] = {
        "url": url,
        "sha256": result["sha256"],
        "bytes": result["bytes"],
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "run_date": run_date.isoformat(),
    }
    save_meta(meta)
    print(f"    {result['bytes']:,} bytes, sha256 {result['sha256']}")
    return {"dataset": dataset, "cycle": cycle, "skipped": False, "path": str(dest), **result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", type=int, required=True, help="election cycle, e.g. 2024")
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=DEFAULT_DATASETS,
        choices=sorted(FILENAME_TEMPLATES),
        help=f"default: {DEFAULT_DATASETS} (cn, cm are small; indiv is multi-GB)",
    )
    parser.add_argument("--force", action="store_true", help="refetch even if already present")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    args = parser.parse_args(argv)

    run_date = date.today()
    for i, dataset in enumerate(args.datasets):
        if i > 0:
            time.sleep(BE_POLITE_DELAY_SECONDS)  # polite gap between files, per CLAUDE.md rule 6
        download_dataset(
            args.cycle, dataset, user_agent=args.user_agent, force=args.force, run_date=run_date
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
