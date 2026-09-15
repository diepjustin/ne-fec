"""sub_id dedup, amendment supersession, and the employer/occupation privacy rule."""

from pathlib import Path

from filter_ne import iter_ne_candidates, iter_ne_committees, iter_ne_individuals
from normalize import (
    CANDIDATES_COLUMNS,
    COMMITTEES_COLUMNS,
    CONTRIBUTIONS_COLUMNS,
    build_candidates,
    build_committees,
    build_contributions,
    dedup_by_identity,
    supersede_amendments,
)

FIXTURES = Path(__file__).parent / "fixtures"
RETRIEVED_AT = "2026-09-15T00:00:00+00:00"


def test_employer_and_occupation_never_in_contribution_columns():
    assert "employer" not in [c.lower() for c in CONTRIBUTIONS_COLUMNS]
    assert "occupation" not in [c.lower() for c in CONTRIBUTIONS_COLUMNS]


def test_employer_and_occupation_never_in_built_rows():
    raw = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    rows = build_contributions(raw, cycle=2024, cmte_names={}, retrieved_at=RETRIEVED_AT)
    assert rows  # sanity: fixture actually produced rows
    for row in rows:
        assert "employer" not in row
        assert "occupation" not in row
        assert set(row.keys()) == set(CONTRIBUTIONS_COLUMNS)


def test_amendment_supersession_keeps_only_newest_file_num():
    raw = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    smith_rows = [r for r in raw if r["NAME"] == "SMITH, JANE"]
    assert len(smith_rows) == 2  # fixture has an original (1700001) + amendment (1700099)

    survivors = supersede_amendments(raw)
    surviving_smith = [r for r in survivors if r["NAME"] == "SMITH, JANE"]
    assert len(surviving_smith) == 1
    assert surviving_smith[0]["FILE_NUM"] == "1700099"
    assert surviving_smith[0]["TRANSACTION_AMT"] == "300"  # the amended amount, not the original


def test_dedup_by_identity_drops_repeated_sub_id():
    rows = [
        {"SUB_ID": "1", "IMAGE_NUM": "a", "TRAN_ID": "x"},
        {"SUB_ID": "1", "IMAGE_NUM": "a", "TRAN_ID": "x"},  # exact repeat
        {"SUB_ID": "2", "IMAGE_NUM": "b", "TRAN_ID": "y"},
    ]
    out = dedup_by_identity(rows, cycle=2024)
    assert len(out) == 2
    assert {r["SUB_ID"] for r in out} == {"1", "2"}


def test_dedup_by_identity_fallback_key_when_sub_id_blank():
    rows = [
        {"SUB_ID": "", "IMAGE_NUM": "img1", "TRAN_ID": "t1"},
        {"SUB_ID": "", "IMAGE_NUM": "img1", "TRAN_ID": "t1"},  # same fallback key
        {"SUB_ID": "", "IMAGE_NUM": "img2", "TRAN_ID": "t1"},  # different image_num
    ]
    out = dedup_by_identity(rows, cycle=2024)
    assert len(out) == 2


def test_full_contributions_pipeline_row_count_after_dedup():
    raw = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    rows = build_contributions(raw, cycle=2024, cmte_names={}, retrieved_at=RETRIEVED_AT)
    # 2 NE contributors (SMITH via 2 rows collapsing to 1 after amendment
    # supersession, BROWN) -- California DOE already filtered by filter_ne.
    assert len(rows) == 2
    names = {r["name"] for r in rows}
    assert names == {"SMITH, JANE", "BROWN, PAT"}


def test_source_url_built_from_image_num():
    raw = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    rows = build_contributions(raw, cycle=2024, cmte_names={}, retrieved_at=RETRIEVED_AT)
    brown = next(r for r in rows if r["name"] == "BROWN, PAT")
    assert brown["source_url"] == f"https://docquery.fec.gov/cgi-bin/fecimg/?{brown['image_num']}"
    assert brown["source_snapshot"] == RETRIEVED_AT


def test_committees_drop_street_address_keep_city_state_zip():
    raw = list(iter_ne_committees(FIXTURES / "cm_sample.zip"))
    rows = build_committees(raw, cycle=2024, retrieved_at=RETRIEVED_AT)
    assert rows
    for row in rows:
        assert set(row.keys()) == set(COMMITTEES_COLUMNS)
        assert "cmte_st1" not in row
        assert "cmte_st2" not in row
        assert row["city"] and row["state"] and row["zip"]


def test_candidates_columns_shape():
    raw = list(iter_ne_candidates(FIXTURES / "cn_sample.zip"))
    rows = build_candidates(raw, cycle=2024, retrieved_at=RETRIEVED_AT)
    assert rows
    for row in rows:
        assert set(row.keys()) == set(CANDIDATES_COLUMNS)
        assert "cand_st1" not in row
        assert "cand_st2" not in row


def test_cmte_name_lookup_enriches_contribution_rows():
    raw = list(iter_ne_individuals(FIXTURES / "indiv_sample.zip"))
    rows = build_contributions(
        raw, cycle=2024, cmte_names={"C00458964": "FISCHER FOR SENATE"}, retrieved_at=RETRIEVED_AT
    )
    smith = next(r for r in rows if r["name"] == "SMITH, JANE")
    assert smith["cmte_name"] == "FISCHER FOR SENATE"
    brown = next(r for r in rows if r["name"] == "BROWN, PAT")
    assert brown["cmte_name"] == ""  # committee not in the (small) lookup we passed
