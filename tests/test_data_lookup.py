"""Records, lookup with optional ranking, and catalog over captured data sources."""

import json
import time

from web_scout.scraping._data_sources import DataSource
from web_scout.tools.data_lookup import lookup_records, records_from_source, render_catalog


def src(body, kind="response_json", source_id="D1"):
    return DataSource(source_id, "https://data.example.org/api/data.json", kind, "application/json", body, len(body))


def test_nested_records_keep_ancestor_context():
    body = json.dumps(
        {
            "periods": [
                {
                    "startDate": {"year": 2025},
                    "endDate": {"year": 2026},
                    "features": [{"loi": 2, "loiname": 19124, "areas": [{"type": 1, "area": 155.79}]}],
                }
            ]
        }
    )
    rec = records_from_source(src(body))[0]
    assert rec["loiname"] == 19124 and rec["_context"]["endDate"] == {"year": 2026}


def test_columnar_json_is_zipped():
    body = json.dumps({"values": [1.5, 2.5], "years": [2019, 2020], "entities": [76, 76]})
    assert records_from_source(src(body)) == [
        {"values": 1.5, "years": 2019, "entities": 76},
        {"values": 2.5, "years": 2020, "entities": 76},
    ]


def test_json_stat_values_keep_labels():
    body = json.dumps(
        {
            "class": "dataset",
            "id": ["geo", "time"],
            "size": [1, 2],
            "value": [11.7, 11.8],
            "dimension": {
                "geo": {"category": {"index": {"BE": 0}, "label": {"BE": "Belgium"}}},
                "time": {"category": {"index": {"2023": 0, "2024": 1}}},
            },
        }
    )
    assert {"geo": "Belgium", "time": "2024", "value": 11.8} in records_from_source(src(body))


def test_json_stat_1_dimension_ids_and_malformed_datasets():
    dimension = {"id": ["time"], "size": [2], "time": {"category": {"index": ["2023", "2024"]}}}
    body = json.dumps({"dataset": {"dimension": dimension, "value": [1.5, 2.5]}})
    assert records_from_source(src(body)) == [{"time": "2023", "value": 1.5}, {"time": "2024", "value": 2.5}]
    bad = {**dimension, "size": [1]}
    assert records_from_source(src(json.dumps({"dataset": {"dimension": bad, "value": [1.5, 2.5]}}))) == []


def test_json_stat_dict_index_missing_codes_yields_no_records():
    body = json.dumps(
        {
            "id": ["time"],
            "size": [2],
            "value": [1.5, 2.5],
            "dimension": {"time": {"category": {"index": {"2024": 1}}}},
        }
    )
    assert records_from_source(src(body)) == []


def test_bare_scalar_list_yields_no_records():
    assert records_from_source(src(json.dumps([1, 2, 3, 4]))) == []


def test_csv_rows():
    assert records_from_source(src("year,value\n2025,3335\n", kind="response_csv")) == [
        {"year": "2025", "value": "3335"}
    ]


def test_lookup_is_accent_and_case_insensitive_and_unsorted():
    body = json.dumps({"lois": [{"gid": 1, "loiname": "Lábrea"}, {"gid": 2, "loiname": "Altamira"}]})
    res = lookup_records(src(body), "labrea")
    assert res.total_matches == 1 and res.records[0]["gid"] == 1


def test_catalog_is_short():
    text = render_catalog([src(json.dumps({"rows": [{"a": i} for i in range(5000)]}))] * 8)
    assert len(text) <= 2500 and "D1" in text


# Controller rulings and constraint caps beyond the brief's examples.


def test_xlsx_markdown_table_rows_become_records():
    body = "## Sheet1\n\n| year | value |\n|------|-------|\n| 2025 | 3335 |\n| 2026 | 4210 |\n"
    assert records_from_source(src(body, kind="download_xlsx"))[1] == {
        "year": "2026",
        "value": "4210",
        "_context": {"section": "Sheet1"},
    }
    assert records_from_source(src("", kind="download_xlsx")) == []


def test_geojson_feature_with_only_geometry_nesting_is_one_record():
    ring = [[float(i), float(i)] for i in range(60)]
    feature = {
        "type": "Feature",
        "properties": {"name": "Amapá", "area": 12.5},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }
    records = records_from_source(src(json.dumps({"type": "FeatureCollection", "features": [feature, feature]})))
    assert len(records) == 2 and records[0]["properties"] == {"name": "Amapá", "area": 12.5}
    assert lookup_records(src(json.dumps({"features": [feature]})), "amapa").records[0]["properties"]["area"] == 12.5


def test_invalid_or_deeply_nested_json_yields_no_records():
    assert records_from_source(src("{not json")) == []
    assert records_from_source(src("[" * 100_000 + "]" * 100_000)) == []


def test_lookup_searches_all_records_and_caps_results():
    rows = [{"gid": i, "name": "other"} for i in range(10_000)] + [{"gid": i, "name": "Altamira"} for i in range(40)]
    res = lookup_records(src(json.dumps({"rows": rows})), "altamira")
    assert res.total_matches == 40 and len(res.records) == 30
    assert [r["gid"] for r in res.records] == list(range(30))
    assert res.source_id == "D1" and res.url == "https://data.example.org/api/data.json"


def test_lookup_matches_whole_numbers_not_digits_inside_other_numbers():
    body = json.dumps({"rows": [{"gid": 19124}, {"gid": 191245}, {"area": 0.19124}, {"gid": 19124.5}]})
    assert lookup_records(src(body), "19124").total_matches == 1


def test_lookup_stays_fast_with_many_ancestor_scalars():
    body = json.dumps(
        {**{f"field_{i}": i for i in range(5_000)}, "rows": [{"name": "x", "v": i} for i in range(5_000)]}
    )
    start = time.perf_counter()
    res = lookup_records(src(body), "x")
    assert time.perf_counter() - start < 0.5
    assert res.total_matches == 5_000 and len(json.dumps(res.records[0]["_context"])) <= 300


def test_lookup_truncates_long_records_with_marker():
    body = json.dumps({"rows": [{"name": "Altamira", "notes": "x" * 2000, "total": 3335}]})
    rec = lookup_records(src(body), "altamira").records[0]
    assert len(json.dumps(rec, ensure_ascii=False)) <= 600
    assert rec["name"] == "Altamira" and rec["total"] == 3335 and "notes" in rec["_truncated"]


def test_lookup_truncation_keeps_context_and_bounds_wide_records():
    row = {f"column_{i}": i for i in range(5_000)}
    body = json.dumps({"periods": [{"year": 2026, "rows": [{"name": "Altamira", **row}] * 2}]})
    rec = lookup_records(src(body), "altamira").records[0]
    assert len(json.dumps(rec, ensure_ascii=False)) <= 600
    assert rec["name"] == "Altamira" and rec["_context"] == {"year": 2026} and rec["_truncated"].endswith("...")


# Ranking: values stay exactly as stored; only their order changes.

RANK_ROWS = [
    {"name": "a", "area": 5},
    {"name": "b", "area": 155.79},
    {"name": "c", "area": 12},
    {"name": "d", "area": 12.0},
]


def test_rank_descending_and_ascending_stable_for_ties():
    body = json.dumps({"rows": RANK_ROWS})
    res = lookup_records(src(body), rank_by="area")
    assert [r["name"] for r in res.records] == ["b", "c", "d", "a"]
    assert res.ranked_by == "area" and res.descending is True and res.records[0]["area"] == 155.79
    res = lookup_records(src(body), rank_by="area", descending=False)
    assert [r["name"] for r in res.records] == ["a", "c", "d", "b"] and res.descending is False


def test_source_order_kept_without_rank_by():
    res = lookup_records(src(json.dumps({"rows": RANK_ROWS})))
    assert [r["name"] for r in res.records] == ["a", "b", "c", "d"] and res.ranked_by is None


def test_rank_by_context_field():
    groups = [{"year": year, "rows": [{"n": n, "detail": [{"k": 1}]}]} for year, n in [(2019, "x"), (2026, "y")]]
    res = lookup_records(src(json.dumps({"groups": groups})), rank_by="year")
    assert [r["n"] for r in res.records] == ["y", "x"] and res.records[0]["_context"] == {"year": 2026}


def test_rank_skips_missing_and_non_numeric_fields():
    rows = [{"name": "a", "area": "n/a"}, {"name": "b"}, {"name": "c", "area": 3}, {"name": "d", "area": True}]
    res = lookup_records(src(json.dumps({"rows": rows})), rank_by="area")
    assert [r["name"] for r in res.records] == ["c"] and res.total_matches == 4


def test_term_filter_then_rank_by_field_of_nested_object():
    periods = [
        {"endDate": {"year": year}, "features": [{"loiname": gid, "areas": [{"type": 1, "area": area}]}]}
        for year, gid, area in [(2026, 1, 5.5), (2025, 2, 900.0), (2026, 3, 155.79), (2026, 4, 12.25)]
    ]
    res = lookup_records(src(json.dumps({"periods": periods})), "2026", rank_by="area")
    assert res.total_matches == 3 and [r["loiname"] for r in res.records] == [3, 4, 1]
    assert res.records[0]["areas"] == [{"type": 1, "area": 155.79}]


def test_rank_by_ambiguous_nested_field_is_skipped():
    rows = [{"n": "x", "areas": [{"area": 1}, {"area": 2}]}, {"n": "y", "areas": [{"area": 3}]}]
    assert [r["n"] for r in lookup_records(src(json.dumps({"rows": rows})), rank_by="area").records] == ["y"]


def test_rank_csv_numeric_strings_by_value():
    body = "name;value\na;3335\nb;4,210\nc;512\n"
    res = lookup_records(src(body, kind="response_csv"), rank_by="value")
    assert [r["name"] for r in res.records] == ["b", "a", "c"]
    assert res.records[0]["value"] == "4,210"
    # "1.309,6" shows "," as this column's decimal mark, so "4,210" now reads as 4.21.
    res = lookup_records(src(body + "d;1.309,6\n", kind="response_csv"), rank_by="value")
    assert [r["name"] for r in res.records] == ["a", "d", "c", "b"]


def test_limit_applies_after_ranking():
    body = json.dumps({"rows": [{"gid": i, "area": i} for i in range(100)]})
    res = lookup_records(src(body), rank_by="area", limit=3)
    assert [r["gid"] for r in res.records] == [99, 98, 97] and res.total_matches == 100
    assert len(lookup_records(src(body), rank_by="area", limit=100).records) == 30


# Exact-match field filter.

KIND_ROWS = [
    {"kind": "uf", "name": "Pará", "area": 1146.3},
    {"kind": "mun", "name": "Altamira", "area": 155.79},
    {"kind": "MUN", "name": "Colniza", "area": 108.17},
    {"kind": "consunit", "name": "Triunfo", "area": 119.04},
    {"name": "no kind", "area": 999.0},
]


def test_where_alone_keeps_source_order():
    res = lookup_records(src(json.dumps({"rows": KIND_ROWS})), where={"kind": "mun"})
    assert [r["name"] for r in res.records] == ["Altamira", "Colniza"]
    assert res.total_matches == 2 and res.where == {"kind": "mun"} and res.ranked_by is None


def test_where_with_rank_by_returns_only_matching_kind():
    res = lookup_records(src(json.dumps({"rows": KIND_ROWS})), where={"kind": "mun"}, rank_by="area", descending=False)
    assert [r["name"] for r in res.records] == ["Colniza", "Altamira"]
    assert lookup_records(src(json.dumps({"rows": KIND_ROWS}))).where is None


def test_where_compares_numbers_numerically_and_strings_accent_insensitively():
    rows = [
        {"loi": 2, "n": "a"},
        {"loi": 2.0, "n": "b"},
        {"loi": "2", "n": "c"},
        {"loi": 3, "n": "d"},
        {"loi": "x", "n": "e"},
    ]
    body = json.dumps({"rows": rows})
    for value in (2, 2.0, "2"):
        assert [r["n"] for r in lookup_records(src(body), where={"loi": value}).records] == ["a", "b", "c"]
    assert [
        r["name"] for r in lookup_records(src(json.dumps({"rows": KIND_ROWS})), where={"name": "PARA"}).records
    ] == ["Pará"]


def test_where_missing_field_excludes_record():
    res = lookup_records(src(json.dumps({"rows": KIND_ROWS})), where={"kind": "uf", "area": 1146.3})
    assert [r["name"] for r in res.records] == ["Pará"]
    assert lookup_records(src(json.dumps({"rows": KIND_ROWS})), where={"absent": 1}).total_matches == 0


def test_where_term_and_rank_by_combined_on_context_and_nested_fields():
    periods = [
        {"endDate": {"year": year}, "features": [{"loi": loi, "loiname": gid, "areas": [{"type": 1, "area": area}]}]}
        for year, loi, gid, area in [
            (2026, 1, 18281, 1146.3),
            (2026, 2, 19137, 155.79),
            (2025, 2, 19999, 900.0),
            (2026, 2, 19101, 108.17),
            (2026, 3, 18009, 119.04),
        ]
    ]
    body = json.dumps({"periods": periods})
    res = lookup_records(src(body), "2026", where={"loi": 2}, rank_by="area")
    assert [r["loiname"] for r in res.records] == [19137, 19101] and res.total_matches == 2
    assert lookup_records(src(body), where={"type": 1, "loi": "2"}).total_matches == 3


# Separator reading per field, truncation that keeps ranked fields, multi-word terms.


def test_rank_dot_decimals_with_three_digit_fractions():
    rows = [
        {"n": "A", "rate": "0.125"},
        {"n": "B", "rate": "12.5"},
        {"n": "C", "rate": "3.75"},
        {"n": "D", "rate": "1.500"},
    ]
    res = lookup_records(src(json.dumps({"rows": rows})), rank_by="rate")
    assert [r["n"] for r in res.records] == ["B", "C", "D", "A"] and res.records[2]["rate"] == "1.500"


def test_rank_field_with_decimal_evidence_reads_ambiguous_values_as_decimals():
    rows = [{"n": "x", "v": "12.345"}, {"n": "y", "v": "12.5"}, {"n": "z", "v": "12.34"}]
    assert [r["n"] for r in lookup_records(src(json.dumps({"rows": rows})), rank_by="v").records] == ["y", "x", "z"]


def test_leading_zero_group_is_a_decimal():
    rows = [{"n": "a", "v": "-0,250"}, {"n": "b", "v": "-1"}, {"n": "c", "v": "0"}]
    assert [r["n"] for r in lookup_records(src(json.dumps({"rows": rows})), rank_by="v").records] == ["c", "a", "b"]


def test_csv_column_without_decimal_evidence_reads_thousands():
    body = "name;value\na;4.210\nb;1.309\nc;512\n"
    res = lookup_records(src(body, kind="response_csv"), rank_by="value")
    assert [r["name"] for r in res.records] == ["a", "b", "c"]


def test_where_uses_the_field_separator_reading():
    rows = [{"n": "a", "rate": 1.5}, {"n": "b", "rate": 0.25}, {"n": "c", "rate": "1500"}]
    res = lookup_records(src(json.dumps({"rows": rows})), where={"rate": "1.500"})
    assert [r["n"] for r in res.records] == ["a"]


def test_where_does_not_match_null_or_booleans_by_their_string_form():
    rows = [{"n": "a", "f": None}, {"n": "b", "f": True}, {"n": "c", "f": "None"}]
    body = json.dumps({"rows": rows})
    assert [r["n"] for r in lookup_records(src(body), where={"f": "None"}).records] == ["c"]
    assert lookup_records(src(body), where={"f": "True"}).total_matches == 0


def test_where_is_stored_as_a_copy():
    where = {"kind": "mun"}
    res = lookup_records(src(json.dumps({"rows": KIND_ROWS})), where=where)
    where["kind"] = "uf"
    assert res.where == {"kind": "mun"}


def test_truncation_keeps_ranked_and_filtered_fields():
    row = {**{f"field_{i:02}": f"value {i}" for i in range(60)}, "kind": "mun", "value": 7}
    body = json.dumps({"rows": [row, {**row, "value": 9}]})
    rec = lookup_records(src(body), rank_by="value", where={"kind": "mun"}).records[0]
    assert rec["value"] == 9 and rec["kind"] == "mun" and len(json.dumps(rec, ensure_ascii=False)) <= 600
    # Few, long fields: the row stays one record (a container needs over 50 nodes).
    nested = {**{f"field_{i:02}": "v" * 60 for i in range(12)}, "areas": [{"type": 1, "area": 3.5}]}
    rec = lookup_records(src(json.dumps({"rows": [nested, nested]})), rank_by="area").records[0]
    assert rec["areas"] == [{"type": 1, "area": 3.5}]


def test_multi_word_term_matches_words_in_any_field_and_order():
    rows = [
        {"country": "Rwanda", "commodity": "Maize (white)", "market": "Kigali"},
        {"country": "Rwanda", "commodity": "Beans", "market": "Huye"},
        {"country": "Kenya", "commodity": "Maize", "market": "Nairobi"},
    ]
    body = json.dumps({"rows": rows})
    assert [r["market"] for r in lookup_records(src(body), "Rwanda maize").records] == ["Kigali"]
    assert [r["market"] for r in lookup_records(src(body), "maize  rwanda").records] == ["Kigali"]
    assert lookup_records(src(body), "Rwanda maize sorghum").total_matches == 0


def test_where_matches_floats_by_their_printed_form():
    body = json.dumps({"rows": [{"n": "a", "area": 155.79}, {"n": "b", "area": 155.8}]})
    assert [r["n"] for r in lookup_records(src(body), where={"area": "155.79"}).records] == ["a"]
    assert [r["n"] for r in lookup_records(src(body), where={"area": 155.79}).records] == ["a"]
    assert [r["n"] for r in lookup_records(src(body), where={"area": "155.8"}).records] == ["b"]


def test_rank_mixed_floats_and_numeric_strings():
    rows = [
        {"n": "a", "v": 0.1},
        {"n": "b", "v": "0.15"},
        {"n": "c", "v": 2},
        {"n": "d", "v": "0.10"},
        {"n": "e", "v": 1.25},
    ]
    res = lookup_records(src(json.dumps({"rows": rows})), rank_by="v")
    assert [r["n"] for r in res.records] == ["c", "e", "b", "a", "d"]


def test_where_any_of_matches_listed_values():
    body = json.dumps(
        {"lois": [{"gid": 1, "loiname": "Lábrea"}, {"gid": 2, "loiname": "Altamira"}, {"gid": 3, "loiname": "Apuí"}]}
    )
    res = lookup_records(src(body), where={"gid": ["1", 3]})
    assert [r["gid"] for r in res.records] == [1, 3] and res.total_matches == 2
    assert [r["gid"] for r in lookup_records(src(body), where={"loiname": ("LABREA", "apui")}).records] == [1, 3]
    assert lookup_records(src(body), where={"gid": []}).total_matches == 0


def test_where_any_of_is_stored_as_a_copy():
    values = ["1", "2"]
    res = lookup_records(src(json.dumps({"rows": [{"gid": 1}, {"gid": 2}]})), where={"gid": values})
    values.append("3")
    assert res.where == {"gid": ["1", "2"]}
