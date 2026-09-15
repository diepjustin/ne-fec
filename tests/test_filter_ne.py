"""Header/schema parsing and NE-only filter logic, against real-shaped fixtures."""

from pathlib import Path

import pytest
from filter_ne import (
    CM_HEADER,
    CN_HEADER,
    INDIV_HEADER,
    iter_all_committees,
    iter_ne_candidates,
    iter_ne_committees,
    iter_ne_individuals,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Transcribed directly from a real pull of
# https://www.fec.gov/files/bulk-downloads/data_dictionaries/{cn,cm,indiv}_header_file.csv
# on 2026-09-15. A change here means the FEC changed its published schema.
REAL_CN_HEADER = (
    "CAND_ID,CAND_NAME,CAND_PTY_AFFILIATION,CAND_ELECTION_YR,CAND_OFFICE_ST,CAND_OFFICE,"
    "CAND_OFFICE_DISTRICT,CAND_ICI,CAND_STATUS,CAND_PCC,CAND_ST1,CAND_ST2,CAND_CITY,CAND_ST,CAND_ZIP"
).split(",")
REAL_CM_HEADER = (
    "CMTE_ID,CMTE_NM,TRES_NM,CMTE_ST1,CMTE_ST2,CMTE_CITY,CMTE_ST,CMTE_ZIP,CMTE_DSGN,CMTE_TP,"
    "CMTE_PTY_AFFILIATION,CMTE_FILING_FREQ,ORG_TP,CONNECTED_ORG_NM,CAND_ID"
).split(",")
REAL_INDIV_HEADER = (
    "CMTE_ID,AMNDT_IND,RPT_TP,TRANSACTION_PGI,IMAGE_NUM,TRANSACTION_TP,ENTITY_TP,NAME,CITY,STATE,"
    "ZIP_CODE,EMPLOYER,OCCUPATION,TRANSACTION_DT,TRANSACTION_AMT,OTHER_ID,TRAN_ID,FILE_NUM,MEMO_CD,"
    "MEMO_TEXT,SUB_ID"
).split(",")


def test_headers_match_real_dictionary_pull():
    assert CN_HEADER == REAL_CN_HEADER
    assert CM_HEADER == REAL_CM_HEADER
    assert INDIV_HEADER == REAL_INDIV_HEADER


def test_ne_candidates_filters_on_cand_st_or_cand_office_st():
    rows = list(iter_ne_candidates(FIXTURES / "cn_sample.zip"))
    ids = {r["CAND_ID"] for r in rows}
    assert ids == {"H2NE01100", "S6NE00013"}
    assert "H8CA05100" not in ids  # California candidate, dropped


def test_ne_committees_filters_on_cmte_st():
    rows = list(iter_ne_committees(FIXTURES / "cm_sample.zip"))
    ids = {r["CMTE_ID"] for r in rows}
    assert ids == {"C00458964", "C00812345"}
    assert "C00111222" not in ids  # California PAC, dropped


def test_ne_individuals_filters_on_contributor_state():
    rows = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    names = {r["NAME"] for r in rows}
    # SMITH (x2, original+amendment), BROWN -- all NE contributors, even
    # though BROWN gave to an out-of-state committee (C00999999).
    assert names == {"SMITH, JANE", "BROWN, PAT"}
    assert "DOE, JOHN" not in names  # California contributor, dropped
    assert all(r["STATE"] == "NE" for r in rows)


def test_iter_all_committees_has_no_state_filter():
    rows = list(iter_all_committees(FIXTURES / "cm_sample.zip"))
    ids = {r["CMTE_ID"] for r in rows}
    assert ids == {"C00458964", "C00812345", "C00111222"}  # CA one included


def test_row_field_count_must_match_header():
    from filter_ne import _iter_rows

    # A malformed line (wrong field count) must fail loudly, not silently
    # misalign columns -- same principle as ne-campaign-finance's hard
    # failure on a header/column mismatch.
    import zipfile

    bad_zip = FIXTURES / "_bad_field_count.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("cn24.txt", "TOO|FEW|FIELDS\n")
    try:
        with pytest.raises(ValueError):
            list(_iter_rows(bad_zip, CN_HEADER))
    finally:
        bad_zip.unlink()
