"""
Unit tests for normalizer.py
Run with: pytest tests/test_normalizer.py -v
"""

import sys
import os

# Add src to path so imports work without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from normalizer import normalize_patent, _clean_date, _first_truthy


# ─── _clean_date ─────────────────────────────────────────────────────────────

def test_clean_date_iso():
    assert _clean_date("2023-05-15") == "2023-05-15"

def test_clean_date_yyyymmdd():
    assert _clean_date("20230515") == "2023-05-15"

def test_clean_date_slash():
    assert _clean_date("15/05/2023") == "2023-05-15"

def test_clean_date_embedded():
    assert _clean_date("Filed 2022-03-01 (provisional)") == "2022-03-01"

def test_clean_date_empty():
    assert _clean_date("") == ""

def test_clean_date_none():
    assert _clean_date(None) == ""


# ─── _first_truthy ────────────────────────────────────────────────────────────

def test_first_truthy_basic():
    assert _first_truthy(["", None, "Alibaba"]) == "Alibaba"

def test_first_truthy_all_empty():
    assert _first_truthy(["", "", None]) == ""

def test_first_truthy_first_wins():
    assert _first_truthy(["Google", "Apple"]) == "Google"


# ─── USPTO normalizer ─────────────────────────────────────────────────────────

SAMPLE_USPTO = {
    "_source": "USPTO",
    "_source_api": "PatentsView",
    "patent_id": "US11256551B2",
    "patent_title": "Blockchain-based virtual resource allocation",
    "patent_abstract": "A system and method for allocating virtual resources on a blockchain.",
    "patent_date": "2022-02-22",
    "application_filing_date": "2020-02-28",
    "assignees": [{"assignee_organization": "Alibaba Group Holding Limited"}],
    "inventors": [
        {"inventor_first_name": "Li", "inventor_last_name": "Zhang"},
        {"inventor_first_name": "Wei", "inventor_last_name": "Chen"},
    ],
    "cpcs": [{"cpc_group": "G06Q 50/18"}, {"cpc_group": "H04L 9/00"}],
}

def test_normalize_uspto_id():
    result = normalize_patent(SAMPLE_USPTO)
    assert result["id"] == "US11256551B2"

def test_normalize_uspto_title():
    result = normalize_patent(SAMPLE_USPTO)
    assert "Blockchain" in result["title"]

def test_normalize_uspto_jurisdiction():
    result = normalize_patent(SAMPLE_USPTO)
    assert result["jurisdictions"] == ["US"]

def test_normalize_uspto_assignee():
    result = normalize_patent(SAMPLE_USPTO)
    assert result["assignee"] == "Alibaba Group Holding Limited"

def test_normalize_uspto_inventors():
    result = normalize_patent(SAMPLE_USPTO)
    assert "Li Zhang" in result["inventors"]
    assert "Wei Chen" in result["inventors"]

def test_normalize_uspto_classifications():
    result = normalize_patent(SAMPLE_USPTO)
    assert "G06Q 50/18" in result["classifications"]

def test_normalize_uspto_dates():
    result = normalize_patent(SAMPLE_USPTO)
    assert result["publication_date"] == "2022-02-22"
    assert result["filing_date"] == "2020-02-28"

def test_normalize_uspto_source_url():
    result = normalize_patent(SAMPLE_USPTO)
    assert "US11256551B2" in result["source_url"]

def test_normalize_uspto_embedding_empty():
    result = normalize_patent(SAMPLE_USPTO)
    assert result["embedding"] == []


# ─── EPO normalizer ───────────────────────────────────────────────────────────

SAMPLE_EPO = {
    "_source": "EPO",
    "_source_api": "OPS",
    "patent_id": "EP3945623A1",
    "_ep_country": "EP",
    "_ep_number": "3945623",
    "_ep_kind": "A1",
    "_ep_title": "Neural network acceleration",
    "_ep_abstract": "A method of accelerating neural network inference.",
    "_ep_filing_date": "20210301",
    "_ep_pub_date": "20220115",
    "_ep_assignee": "DeepMind Technologies",
    "_ep_inventors": ["Jeff Dean"],
    "_ep_cpcs": ["G06N 3/04"],
}

def test_normalize_epo_id():
    result = normalize_patent(SAMPLE_EPO)
    assert result["id"] == "EP3945623A1"

def test_normalize_epo_jurisdiction():
    result = normalize_patent(SAMPLE_EPO)
    assert "EP" in result["jurisdictions"]

def test_normalize_epo_dates():
    result = normalize_patent(SAMPLE_EPO)
    assert result["filing_date"] == "2021-03-01"
    assert result["publication_date"] == "2022-01-15"

def test_normalize_epo_source_url():
    result = normalize_patent(SAMPLE_EPO)
    assert "espacenet" in result["source_url"]


# ─── WIPO normalizer ──────────────────────────────────────────────────────────

SAMPLE_WIPO = {
    "_source": "WIPO",
    "_source_api": "PATENTSCOPE_RSS",
    "patent_id": "2022056789",
    "_wo_title": "mRNA vaccine delivery system",
    "_wo_abstract": "A lipid nanoparticle system for mRNA vaccine delivery.",
    "_wo_pub_date": "15 Jan 2022",
    "_wo_link": "https://patentscope.wipo.int/search/en/detail.jsf?docId=WO2022056789",
}

def test_normalize_wipo_id_prefix():
    result = normalize_patent(SAMPLE_WIPO)
    assert result["id"].startswith("WO")

def test_normalize_wipo_jurisdiction():
    result = normalize_patent(SAMPLE_WIPO)
    assert result["jurisdictions"] == ["WO"]

def test_normalize_wipo_source_url():
    result = normalize_patent(SAMPLE_WIPO)
    assert "patentscope" in result["source_url"]


# ─── Unknown source fallback ──────────────────────────────────────────────────

def test_normalize_unknown_source():
    raw = {
        "_source": "UNKNOWN",
        "patent_id": "XX12345",
        "title": "Test patent",
        "abstract": "Test abstract",
    }
    result = normalize_patent(raw)
    assert result is not None
    assert result["id"] == "XX12345"