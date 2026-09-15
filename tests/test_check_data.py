"""check_data.py catches duplicate keys and the employer/occupation privacy leak."""

import csv

from check_data import check


def _write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_clean_data_passes(tmp_path):
    _write_csv(
        tmp_path / "fec_contributions_ne.csv",
        ["sub_id", "cmte_id", "cycle", "image_num", "tran_id"],
        [
            {"sub_id": "1", "cmte_id": "C1", "cycle": "2024", "image_num": "i1", "tran_id": "t1"},
            {"sub_id": "2", "cmte_id": "C1", "cycle": "2024", "image_num": "i2", "tran_id": "t2"},
        ],
    )
    report = check(tmp_path)
    assert report["ok"] is True


def test_duplicate_sub_id_fails(tmp_path):
    _write_csv(
        tmp_path / "fec_contributions_ne.csv",
        ["sub_id", "cmte_id", "cycle", "image_num", "tran_id"],
        [
            {"sub_id": "1", "cmte_id": "C1", "cycle": "2024", "image_num": "i1", "tran_id": "t1"},
            {"sub_id": "1", "cmte_id": "C1", "cycle": "2024", "image_num": "i1", "tran_id": "t1"},
        ],
    )
    report = check(tmp_path)
    assert report["ok"] is False
    assert report["files"]["fec_contributions_ne.csv"]["duplicate_keys"] == 1


def test_employer_column_present_fails_even_with_unique_keys(tmp_path):
    _write_csv(
        tmp_path / "fec_contributions_ne.csv",
        ["sub_id", "cmte_id", "cycle", "image_num", "tran_id", "employer"],
        [{"sub_id": "1", "cmte_id": "C1", "cycle": "2024", "image_num": "i1", "tran_id": "t1", "employer": "ACME"}],
    )
    report = check(tmp_path)
    assert report["ok"] is False
    assert "employer" in report["files"]["fec_contributions_ne.csv"]["forbidden_columns_present"]


def test_duplicate_committee_key_fails(tmp_path):
    _write_csv(
        tmp_path / "fec_committees_ne.csv",
        ["cmte_id", "cycle"],
        [{"cmte_id": "C1", "cycle": "2024"}, {"cmte_id": "C1", "cycle": "2024"}],
    )
    report = check(tmp_path)
    assert report["ok"] is False


def test_missing_files_are_simply_skipped(tmp_path):
    report = check(tmp_path)
    assert report["ok"] is True
    assert report["files"] == {}
