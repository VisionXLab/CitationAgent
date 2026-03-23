"""Unit tests for self-citation counting in _load_citing_data."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
from pathlib import Path
from core.dashboard_generator import DashboardGenerator


def _make_gen():
    gen = DashboardGenerator.__new__(DashboardGenerator)
    gen.log = lambda *a: None
    return gen


def _make_excel(tmp_path, rows):
    df = pd.DataFrame(rows)
    p = tmp_path / "test.xlsx"
    df.to_excel(p, index=False)
    return p


def test_self_citation_count_zero(tmp_path):
    gen = _make_gen()
    rows = [
        {"PageID": 1, "PaperID": 1, "Paper_Title": "P1", "Paper_Year": 2023,
         "Paper_Link": "", "Citations": 5, "First_Author_Country": "China",
         "First_Author_Institution": "MIT", "Authors_with_Profile": "",
         "Searched Author-Affiliation": "", "Citing_Description": "", "Citing_Paper": "",
         "Is_Self_Citation": False},
    ]
    p = _make_excel(tmp_path, rows)
    *_, self_count = gen._load_citing_data(p)
    assert self_count == 0


def test_self_citation_count_one(tmp_path):
    gen = _make_gen()
    rows = [
        {"PageID": 1, "PaperID": 1, "Paper_Title": "P1", "Paper_Year": 2023,
         "Paper_Link": "", "Citations": 5, "First_Author_Country": "",
         "First_Author_Institution": "", "Authors_with_Profile": "",
         "Searched Author-Affiliation": "", "Citing_Description": "", "Citing_Paper": "",
         "Is_Self_Citation": True},
    ]
    p = _make_excel(tmp_path, rows)
    *_, self_count = gen._load_citing_data(p)
    assert self_count == 1


def test_self_citation_deduplicated_by_paper_key(tmp_path):
    gen = _make_gen()
    rows = [
        {"PageID": 1, "PaperID": 1, "Paper_Title": "P1", "Paper_Year": 2023,
         "Paper_Link": "", "Citations": 5, "First_Author_Country": "",
         "First_Author_Institution": "", "Authors_with_Profile": "",
         "Searched Author-Affiliation": "", "Citing_Description": "desc1", "Citing_Paper": "CP1",
         "Is_Self_Citation": True},
        {"PageID": 1, "PaperID": 1, "Paper_Title": "P1", "Paper_Year": 2023,
         "Paper_Link": "", "Citations": 5, "First_Author_Country": "",
         "First_Author_Institution": "", "Authors_with_Profile": "",
         "Searched Author-Affiliation": "", "Citing_Description": "desc2", "Citing_Paper": "CP2",
         "Is_Self_Citation": True},
    ]
    p = _make_excel(tmp_path, rows)
    *_, self_count = gen._load_citing_data(p)
    assert self_count == 1


def test_return_is_6_tuple(tmp_path):
    gen = _make_gen()
    rows = [
        {"PageID": 1, "PaperID": 1, "Paper_Title": "P1", "Paper_Year": 2023,
         "Paper_Link": "", "Citations": 5, "First_Author_Country": "",
         "First_Author_Institution": "", "Authors_with_Profile": "",
         "Searched Author-Affiliation": "", "Citing_Description": "", "Citing_Paper": "",
         "Is_Self_Citation": False},
    ]
    p = _make_excel(tmp_path, rows)
    result = gen._load_citing_data(p)
    assert len(result) == 6
