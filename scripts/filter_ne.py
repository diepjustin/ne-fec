"""Stream-decode FEC bulk files and filter to Nebraska rows only.

FEC bulk files are pipe-delimited, headerless -- the column order is
published separately in `data_dictionaries/<dataset>_header_file.csv`.
Headers below are transcribed from a real pull (2026-09-15):

    https://www.fec.gov/files/bulk-downloads/data_dictionaries/cn_header_file.csv
    https://www.fec.gov/files/bulk-downloads/data_dictionaries/cm_header_file.csv
    https://www.fec.gov/files/bulk-downloads/data_dictionaries/indiv_header_file.csv

not guessed from the FEC's format-description PDFs, which is the same
lesson `ne-campaign-finance/scripts/validate.py`'s module docstring
describes for NADC's layout PDFs disagreeing with the real export.

Filter rule (this repo's whole reason to exist -- see `ne-connect/PLAN.md`
Phase 3 and the coordinator note that this pipeline must never hold or
publish national-scale data):

    indiv: STATE == "NE"                                  (contributor's state)
    cm:    CMTE_ST == "NE"                                 (committee's state)
    cn:    CAND_ST == "NE" or CAND_OFFICE_ST == "NE"        (candidate's mailing
                                                              state or the state
                                                              of the office sought)

`iter_ne_individuals` is the one that matters for memory: `indiv<yy>.zip` is
multi-GB, so it is opened as a zip member and iterated line by line via
`io.TextIOWrapper` -- at no point does the whole file, or even the whole
NE-filtered slice, live in memory at once as a list. Callers that need a
list should build one themselves and accept the size tradeoff; this module
only ever hands back a generator.

`iter_all_committees` (no state filter) exists because `cm<yy>.zip` is small
(under 1 MB per cycle) and every individual contribution's recipient
committee needs a name lookup regardless of the committee's own state --
loading that one small file whole to build an id->name dict does not
violate the never-hold-the-national-dataset rule, since nothing from it
beyond `cmte_id -> cmte_nm` is kept once `normalize.py` is done with it.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path
from typing import IO, Iterator

DELIMITER = "|"

# Real header order, confirmed against a live pull -- see module docstring.
CN_HEADER = [
    "CAND_ID",
    "CAND_NAME",
    "CAND_PTY_AFFILIATION",
    "CAND_ELECTION_YR",
    "CAND_OFFICE_ST",
    "CAND_OFFICE",
    "CAND_OFFICE_DISTRICT",
    "CAND_ICI",
    "CAND_STATUS",
    "CAND_PCC",
    "CAND_ST1",
    "CAND_ST2",
    "CAND_CITY",
    "CAND_ST",
    "CAND_ZIP",
]

CM_HEADER = [
    "CMTE_ID",
    "CMTE_NM",
    "TRES_NM",
    "CMTE_ST1",
    "CMTE_ST2",
    "CMTE_CITY",
    "CMTE_ST",
    "CMTE_ZIP",
    "CMTE_DSGN",
    "CMTE_TP",
    "CMTE_PTY_AFFILIATION",
    "CMTE_FILING_FREQ",
    "ORG_TP",
    "CONNECTED_ORG_NM",
    "CAND_ID",
]

INDIV_HEADER = [
    "CMTE_ID",
    "AMNDT_IND",
    "RPT_TP",
    "TRANSACTION_PGI",
    "IMAGE_NUM",
    "TRANSACTION_TP",
    "ENTITY_TP",
    "NAME",
    "CITY",
    "STATE",
    "ZIP_CODE",
    "EMPLOYER",
    "OCCUPATION",
    "TRANSACTION_DT",
    "TRANSACTION_AMT",
    "OTHER_ID",
    "TRAN_ID",
    "FILE_NUM",
    "MEMO_CD",
    "MEMO_TEXT",
    "SUB_ID",
]

HEADERS = {"cn": CN_HEADER, "cm": CM_HEADER, "indiv": INDIV_HEADER}


def _open_sole_member(zip_path: Path) -> tuple[zipfile.ZipFile, str]:
    """Pick the one member holding the complete dataset.

    Most bulk zips (cn<yy>.zip, cm<yy>.zip, and most indiv<yy>.zip files)
    hold exactly one member. The largest indiv<yy>.zip files instead ship a
    top-level <name>.txt holding the COMPLETE cycle *plus* a redundant
    by_date/ breakdown of the same rows (split by date range, presumably a
    courtesy for people who don't want the whole file) and a
    by_date/..._invalid_dates.txt catch-all for rows that didn't sort into a
    date bucket -- confirmed on a real 2024-cycle pull (2026-09-16):
    indiv24.zip has itcont.txt (11,040,734,781 bytes uncompressed) plus 32
    by_date/ members whose sizes sum to exactly that same total, byte for
    byte. Reading by_date/*.txt IN ADDITION to the top-level file would
    double-count every row, so this always prefers the sole top-level
    (no "/" in the name) member when there is exactly one.
    """
    zf = zipfile.ZipFile(zip_path)
    names = zf.namelist()
    top_level = [n for n in names if "/" not in n]
    if len(top_level) == 1:
        return zf, top_level[0]
    if len(names) == 1:
        return zf, names[0]
    zf.close()
    raise ValueError(
        f"expected exactly one top-level member in {zip_path}, found {names!r}"
    )


def _iter_rows(zip_path: Path, header: list[str]) -> Iterator[dict]:
    """Yield each data line as a dict, decoded and split, never materialized as a list.

    FEC bulk exports are Windows-1252 (Latin-1 superset) in practice, same
    finding as `ne-campaign-finance/scripts/download_extracts.py` made for
    NADC's export -- try utf-8 first per line group is wasteful, so decode
    the stream as cp1252 directly (a strict superset of ASCII, so this does
    not break the common case).
    """
    zf, member = _open_sole_member(zip_path)
    try:
        with zf.open(member) as raw:
            text_stream: IO[str] = io.TextIOWrapper(raw, encoding="cp1252", newline="")
            reader = csv.reader(text_stream, delimiter=DELIMITER)
            for fields in reader:
                if not fields or (len(fields) == 1 and fields[0] == ""):
                    continue
                if len(fields) != len(header):
                    raise ValueError(
                        f"{zip_path}: row has {len(fields)} fields, header has "
                        f"{len(header)} ({header!r}): {fields!r}"
                    )
                yield dict(zip(header, fields))
    finally:
        zf.close()


def iter_ne_candidates(zip_path: Path) -> Iterator[dict]:
    for row in _iter_rows(Path(zip_path), CN_HEADER):
        if row["CAND_ST"] == "NE" or row["CAND_OFFICE_ST"] == "NE":
            yield row


def iter_ne_committees(zip_path: Path) -> Iterator[dict]:
    for row in _iter_rows(Path(zip_path), CM_HEADER):
        if row["CMTE_ST"] == "NE":
            yield row


def iter_ne_individuals(zip_path: Path) -> Iterator[dict]:
    """Stream `indiv<yy>.zip` line by line; only NE-state rows are ever yielded.

    Never call `list()` on this in production code for a real cycle -- the
    point is that the caller (normalize.py) writes each surviving row
    straight into the small NE-only CSV and lets the rest fall out of scope
    immediately.
    """
    for row in _iter_rows(Path(zip_path), INDIV_HEADER):
        if row["STATE"] == "NE":
            yield row


def iter_all_committees(zip_path: Path) -> Iterator[dict]:
    """No state filter -- see module docstring for why this one small file is exempt."""
    yield from _iter_rows(Path(zip_path), CM_HEADER)
