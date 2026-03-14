"""Unit tests for utils.py"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from utils import make_stable_id, deduplicate, truncate_text, clean_text, chunk_list, safe_get


def test_make_stable_id_passthrough():
    assert make_stable_id("US", "US12345B2") == "US12345B2"

def test_make_stable_id_generic():
    result = make_stable_id("WO", "WO_abc123")
    assert result.startswith("WO_")

def test_deduplicate_removes_dupes():
    patents = [
        {"id": "US123", "abstract": "short"},
        {"id": "US123", "abstract": "much longer abstract wins"},
        {"id": "EP456", "abstract": "unique"},
    ]
    result = deduplicate(patents)
    assert len(result) == 2
    us = next(p for p in result if p["id"] == "US123")
    assert us["abstract"] == "much longer abstract wins"

def test_deduplicate_empty():
    assert deduplicate([]) == []

def test_truncate_text_no_op():
    assert truncate_text("hello", 100) == "hello"

def test_truncate_text_word_boundary():
    result = truncate_text("hello world foo", 8)
    assert result.endswith("…")
    assert "hello" in result

def test_clean_text_strips_html():
    assert clean_text("<b>Hello</b> <i>world</i>") == "Hello world"

def test_clean_text_normalizes_whitespace():
    assert clean_text("foo   bar\n\tbaz") == "foo bar baz"

def test_chunk_list():
    chunks = list(chunk_list([1, 2, 3, 4, 5], 2))
    assert chunks == [[1, 2], [3, 4], [5]]

def test_chunk_list_exact():
    chunks = list(chunk_list([1, 2, 3, 4], 2))
    assert len(chunks) == 2

def test_safe_get_nested():
    d = {"a": {"b": {"c": "found"}}}
    assert safe_get(d, "a", "b", "c") == "found"

def test_safe_get_missing():
    d = {"a": {}}
    assert safe_get(d, "a", "b", "c", default="nope") == "nope"

def test_safe_get_not_dict():
    assert safe_get("not a dict", "key", default="x") == "x"