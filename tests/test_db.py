import json

import pytest

import cfs


def test_load_db_valid(db_file):
    db = cfs.load_db(str(db_file))
    assert db["result"]["count"] == 2


def test_load_db_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        cfs.load_db(str(p))
    assert exc.value.code == cfs.EXIT_DB


def test_save_db(tmp_path, mock_db):
    p = tmp_path / "out.json"
    cfs.save_db(str(p), mock_db)
    loaded = json.loads(p.read_text())
    assert loaded == mock_db


def test_find_custom_entries(mock_db):
    custom = cfs.find_custom_entries(mock_db)
    assert len(custom) == 1
    assert custom[0]["base"]["id"] == "99001"


def test_find_custom_entries_empty(tmp_path):
    db = {"result": {"list": [], "count": 0, "version": 1}}
    assert cfs.find_custom_entries(db) == []


def test_next_free_id_empty_db():
    db = {"result": {"list": [], "count": 0, "version": 1}}
    assert cfs.next_free_id(db, 99001) == 99001


def test_next_free_id_with_entries(mock_db):
    assert cfs.next_free_id(mock_db, 99001) == 99002


def test_next_free_id_gap(mock_db):
    # add 99003, expect 99002 still free
    mock_db["result"]["list"].append({"base": {"id": "99003"}})
    assert cfs.next_free_id(mock_db, 99001) == 99002


def test_find_entry_by_id(mock_db):
    e = cfs.find_entry(mock_db, "01001")
    assert e["base"]["name"] == "Hyper PLA"


def test_find_entry_not_found(mock_db):
    assert cfs.find_entry(mock_db, "99999") is None
