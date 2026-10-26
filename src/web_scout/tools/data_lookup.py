"""Records, lookup and catalog over a page's captured data sources.

``records_from_source`` turns a ``DataSource`` body into flat records, ``lookup_records`` finds
the records that mention a term, optionally ranked by one numeric field, and ``render_catalog``
summarises a page's sources for the extractor.

Web Scout may quote only numbers a source stores in its own data, so nothing here sums, averages
or derives values: records carry the source's values exactly as stored, and ranking only orders them.
"""

import csv
import io
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import islice
from typing import Any

from web_scout.scraping._data_sources import DataSource, _decode_json

_MAX_LOOKUP_RECORDS = 30
_MAX_RECORD_CHARS = 600
_MAX_CATALOG_CHARS = 2_500
# A list item above this many JSON nodes that holds nested records is a container rather than a record.
_RECORD_MAX_NODES = 50
_CONTEXT_DICT_MAX_KEYS = 8
# Long strings (descriptions, notes) would be repeated in every record's context.
_CONTEXT_STRING_MAX_CHARS = 200
_CATALOG_FIELD_SAMPLE = 20
_CATALOG_SAMPLE_RECORDS = 2
# A truncated record keeps this much room for the ``_truncated`` marker naming omitted fields.
_TRUNCATION_MARKER_RESERVE = 150
# Context is repeated in every record of a container, so its size is capped.
_CONTEXT_MAX_CHARS = 300

_COMBINING_MARKS = re.compile(r"[\u0300-\u036f]")
_MARKDOWN_SEPARATOR_ROW = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_NUMERIC_TEXT = re.compile(r"[-+]?\d[\d.,]*")
_GROUPED = {sep: re.compile(r"[-+]?\d{1,3}(?:" + re.escape(sep) + r"\d{3})+") for sep in ".,"}


@dataclass(frozen=True)
class LookupResult:
    """Records of one source matching a term.

    ``total_matches`` counts every record passing the ``where`` filter and matching the term.
    ``records`` holds at most 30 of them, each serialising to at most 600 characters: in source
    order when ``ranked_by`` is ``None``, otherwise ordered by that field (``descending`` or
    ascending), leaving out records where the field is missing or not numeric. ``where`` is a
    copy of the exact-match filter applied (lists of accepted values as lists), or ``None``.
    """

    source_id: str
    url: str
    total_matches: int
    records: list[dict]
    ranked_by: str | None
    descending: bool
    where: dict[str, str | int | float | list[str | int | float]] | None


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _is_context_value(value: Any) -> bool:
    if isinstance(value, str):
        return len(value) <= _CONTEXT_STRING_MAX_CHARS
    if isinstance(value, dict):
        return len(value) <= _CONTEXT_DICT_MAX_KEYS and all(_is_scalar(v) for v in value.values())
    return _is_scalar(value)


def _holds_records(value: Any) -> bool:
    """Whether a field value can yield records of its own: a list with an object or list item, or a large object."""
    if isinstance(value, list):
        return any(isinstance(item, (dict, list)) for item in value)
    return isinstance(value, dict) and not _is_context_value(value)


def _exceeds_nodes(value: Any, limit: int) -> bool:
    """Whether a JSON value has more than ``limit`` nodes; stops after ``limit + 1`` so the cost stays bounded."""
    seen = 0
    stack = [value]
    while stack:
        item = stack.pop()
        seen += 1
        if seen > limit:
            return True
        if isinstance(item, dict):
            stack.extend(islice(item.values(), limit))
        elif isinstance(item, list):
            stack.extend(islice(item, limit))
    return False


def _is_json_stat(value: dict) -> bool:
    dimension = value.get("dimension")
    return isinstance(dimension, dict) and "value" in value and isinstance(_json_stat_field(value, "id"), list)


def _json_stat_field(dataset: dict, key: str) -> Any:
    # JSON-stat 2.0 puts ``id`` and ``size`` on the dataset; 1.x puts them under ``dimension``.
    return dataset[key] if key in dataset else dataset["dimension"].get(key)


def _is_container(item: dict) -> bool:
    """Whether a list item holds records rather than being one.

    It does when it nests objects that themselves hold records (a period whose features each list
    their areas), or when it is large and holds records at all. Its own fields become context.
    """
    if _is_json_stat(item):
        return True
    nested = [value for value in item.values() if _holds_records(value)]
    if not nested:
        return False
    for value in nested:
        children = value if isinstance(value, list) else [value]
        if any(isinstance(child, dict) and any(map(_holds_records, child.values())) for child in children):
            return True
    return _exceeds_nodes(item, _RECORD_MAX_NODES)


def _merge_context(ancestors: dict, own: dict) -> dict:
    """Context for an object's nested records, at most ``_CONTEXT_MAX_CHARS`` serialised characters.

    The object's own fields come first in the budget, being nearest to the records, then the
    ancestors' fields they do not override; the result keeps ancestors-then-own order.
    """
    used = 2
    kept: set = set()
    for key, value in [*own.items(), *((k, v) for k, v in ancestors.items() if k not in own)]:
        cost = _field_chars(key, value)
        if used + cost > _CONTEXT_MAX_CHARS:
            # Stop at the first field that does not fit, so a page cannot make this loop long.
            break
        kept.add(key)
        used += cost
    merged = {key: value for key, value in ancestors.items() if key in kept and key not in own}
    merged.update((key, value) for key, value in own.items() if key in kept)
    return merged


def _with_context(record: dict, context: dict) -> dict:
    return {**record, "_context": context} if context else record


def _json_stat_records(dataset: dict) -> list[dict]:
    """Expand every JSON-stat cell to its dimension labels plus ``value``; malformed datasets give ``[]``."""
    try:
        dim_ids = list(_json_stat_field(dataset, "id"))
        sizes = [int(size) for size in _json_stat_field(dataset, "size")]
        labels: list[list[str]] = []
        for dim_id, size in zip(dim_ids, sizes, strict=True):
            category = dataset["dimension"][dim_id].get("category", {})
            index = category.get("index")
            names = category.get("label", {})
            if isinstance(index, dict):
                codes = [None] * size
                for code, position in index.items():
                    if not 0 <= position < size:
                        return []
                    codes[position] = code
                if None in codes:
                    return []
            elif isinstance(index, list):
                codes = list(index)
            else:
                codes = list(names)
            if len(codes) != size:
                return []
            labels.append([str(names.get(code, code)) for code in codes])
        values = dataset["value"]
        cells = values.items() if isinstance(values, dict) else enumerate(values)
        records = []
        for flat, value in cells:
            if value is None:
                continue
            # Row-major order: the last dimension varies fastest.
            position = int(flat)
            record: dict[str, Any] = {}
            for dim_id, size, dim_labels in zip(reversed(dim_ids), reversed(sizes), reversed(labels)):
                position, offset = divmod(position, size)
                record[dim_id] = dim_labels[offset]
            if position:
                return []
            records.append({**dict(reversed(record.items())), "value": value})
        return records
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, ZeroDivisionError):
        return []


def _columnar_rows(obj: dict) -> list[dict]:
    """Zip the scalar lists of ``obj`` that share the most common length, if at least two do."""
    columns = {
        key: value for key, value in obj.items() if isinstance(value, list) and value and all(map(_is_scalar, value))
    }
    if len(columns) < 2:
        return []
    length, count = Counter(map(len, columns.values())).most_common(1)[0]
    if count < 2:
        return []
    keys = [key for key, value in columns.items() if len(value) == length]
    return [dict(zip(keys, row)) for row in zip(*(columns[key] for key in keys))]


def _json_records(root: Any) -> list[dict]:
    """Walk parsed JSON iteratively, in source order, and collect records with ancestor context."""
    records: list[dict] = []
    # Tasks are (action, value, context), pushed reversed to keep source order. "container" visits a
    # list item that holds records and queues a "close" check after its children: a container that
    # yielded nothing (a GeoJSON feature whose only nesting is its geometry) becomes a record itself.
    stack: list[tuple[str, Any, Any]] = [("visit", root, {})]
    while stack:
        action, value, context = stack.pop()
        if action == "emit":
            records.append(_with_context(value, context))
            continue
        if action == "close":
            mark, item_context = context
            if len(records) == mark:
                records.append(_with_context(value, item_context))
            continue
        mark = len(records)
        tasks: list[tuple[str, Any, Any]] = []
        if isinstance(value, list):
            for item in value:
                if isinstance(item, list):
                    tasks.append(("visit", item, context))
                elif isinstance(item, dict):
                    tasks.append(("container" if _is_container(item) else "emit", item, context))
        elif isinstance(value, dict):
            if _is_json_stat(value):
                records.extend(_with_context(record, context) for record in _json_stat_records(value))
            else:
                own = {key: item for key, item in value.items() if _is_context_value(item)}
                # Shared, not copied per record: records only read their context.
                child_context = _merge_context(context, own) if own else context
                rows = _columnar_rows(value)
                records.extend(_with_context(row, child_context) for row in rows)
                nested = [item for item in value.values() if _holds_records(item)]
                if not nested and not rows and own:
                    records.append(_with_context(value, context))
                tasks += [("visit", item, child_context) for item in nested]
        if action == "container":
            # Last in ``tasks``, so it is popped after all of the container's children.
            tasks.append(("close", value, (mark, context)))
        stack.extend(reversed(tasks))
    return records


def _csv_records(body: str) -> list[dict]:
    header = body.partition("\n")[0]
    delimiter = max(",;\t|", key=header.count)
    reader = csv.DictReader(io.StringIO(body), delimiter=delimiter, restkey="_extra")
    records = []
    try:
        for row in reader:
            records.append(row)
    # Oversized or malformed fields end parsing; the rows read so far are kept.
    except csv.Error:
        pass
    return records


def _markdown_cells(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _markdown_records(body: str) -> list[dict]:
    """Rows of every pipe table in a markdown document, keyed by the table's header row."""
    records: list[dict] = []
    lines = body.splitlines()
    header: list[str] | None = None
    context: dict = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        heading = _MARKDOWN_HEADING.match(stripped)
        if heading:
            context = {"section": heading.group(1)}
            header = None
        elif not stripped.startswith("|"):
            header = None
        elif _MARKDOWN_SEPARATOR_ROW.match(stripped):
            continue
        elif header is None:
            if i + 1 < len(lines) and _MARKDOWN_SEPARATOR_ROW.match(lines[i + 1].strip()):
                header = _markdown_cells(stripped)
        else:
            records.append(_with_context(dict(zip(header, _markdown_cells(stripped))), context))
    return records


def records_from_source(source: DataSource) -> list[dict]:
    """Turn a captured data source into flat records, in source order.

    JSON: each object in a list is a record, with the scalar and small-object fields of its
    enclosing objects under ``_context``; a list item holding many nested records is treated as a
    container instead, its own fields becoming context. An object with two or more equal-length
    scalar lists is zipped into rows. A JSON-stat dataset gives one record per cell with its
    dimension labels and ``value``. Bare scalar lists give no records.
    CSV gives one dict per row. ``download_xlsx`` bodies hold the document's markdown export, whose
    pipe-table rows become records; an empty body gives ``[]``. Invalid JSON gives ``[]``.
    """
    if not source.body:
        return []
    if source.kind in ("response_csv", "download_csv"):
        return _csv_records(source.body)
    if source.kind == "download_xlsx":
        return _markdown_records(source.body)
    decoded = _decode_json(source.body.strip())
    if decoded is None:
        return []
    return _json_records(decoded[0])


def _fold(text: str) -> str:
    """Lowercase and strip accents so ``Lábrea`` matches ``labrea``."""
    text = text.casefold()
    if text.isascii():
        return text
    return _COMBINING_MARKS.sub("", unicodedata.normalize("NFKD", text))


def _serialize(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)


def _field_chars(key: Any, value: Any) -> int:
    """Characters a field adds to a serialised record, including the ``, `` separator."""
    return len(_serialize(str(key))) + 2 + len(_serialize(value)) + 2


def _owning_key(record: dict, field: str) -> str | None:
    """The top-level key under which ``field`` is stored, resolved like ``_field_value``.

    Returns the field itself, ``None`` when it lives in ``_context`` (always kept) or nowhere, or
    the key nesting it.
    """
    if field in record:
        return field
    context = record.get("_context")
    if isinstance(context, dict) and field in context:
        return None
    for key, value in record.items():
        if key != "_context" and isinstance(value, (dict, list)) and _nested_values({key: value}, field):
            return key
    return None


def _truncate(record: dict, priority_fields: Sequence[str] = ()) -> dict:
    """Keep whole fields in order while the record fits 600 characters and name the omitted ones.

    Fields are kept or omitted whole, so no number is ever cut short. ``_context`` (at most
    ``_CONTEXT_MAX_CHARS``) is always kept, because it says which period or group a record belongs
    to; the top-level fields holding ``priority_fields`` (the ranked and filtered fields) are
    budgeted next, so a ranked value is not dropped. Other fields fill the remaining room in order.
    """
    if len(_serialize(record)) <= _MAX_RECORD_CHARS:
        return record
    budget = _MAX_RECORD_CHARS - _TRUNCATION_MARKER_RESERVE
    costs = {key: _field_chars(key, value) for key, value in record.items()}
    used = 2
    # ``_context`` is capped well below the budget, so it always fits.
    used += costs.get("_context", 0)
    kept_keys = {"_context"} & record.keys()
    for field in priority_fields:
        key = _owning_key(record, field)
        if key is not None and key not in kept_keys and used + costs[key] <= budget:
            kept_keys.add(key)
            used += costs[key]
    omitted: list[str] = []
    for key in record:
        if key in kept_keys:
            continue
        if used + costs[key] <= budget:
            kept_keys.add(key)
            used += costs[key]
        else:
            omitted.append(str(key))
    kept = {key: value for key, value in record.items() if key in kept_keys}
    room = _MAX_RECORD_CHARS - len(_serialize(kept)) - len(', "_truncated": ""')
    names = ", ".join(omitted)
    marker = "omitted fields: " + names
    if len(_serialize(marker)) - 2 > room:
        cut = names[: room - len("omitted fields: , ...") - 2].rpartition(", ")[0]
        marker = "omitted fields: " + (cut + ", ..." if cut else "...")
    return {**kept, "_truncated": marker}


def _term_pattern(term: str) -> re.Pattern:
    # Whole-token match: a numeric ID must not match inside a longer number or a decimal fraction.
    return re.compile(r"(?<![^\W_])(?<!\d\.)" + re.escape(term) + r"(?![^\W_])(?!\.\d)")


def _is_thousands_grouped(text: str, sep: str) -> bool:
    """Whether ``text`` is digits grouped in threes by ``sep``; a group led by ``0`` (``0.125``) is not."""
    return _GROUPED[sep].fullmatch(text) is not None and text.lstrip("+-")[0] != "0"


def _decimal_evidence(value: Any) -> str | None:
    """The separator a stored value shows to be a decimal mark, or ``None`` when it shows none.

    A non-integral JSON float shows ``.``; a numeric string shows the last of two different
    separators (``1.309,6``), or a single separator not followed by a group of three (``12.5``,
    ``0.125``).
    """
    if isinstance(value, float):
        return "." if value == value and not value.is_integer() else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _NUMERIC_TEXT.fullmatch(text) or not text[-1].isdigit():
        return None
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        return "." if text.rfind(".") > text.rfind(",") else ","
    if dots + commas == 1:
        sep = "." if dots else ","
        return None if _is_thousands_grouped(text, sep) else sep
    return None


def _decimal_marks(records: Sequence[dict], field: str) -> frozenset[str]:
    """Separators that some value of ``field`` shows to be a decimal mark, across ``records``."""
    marks = set()
    for record in records:
        value = _field_value(record, field)
        if value is not _MISSING and (mark := _decimal_evidence(value)) is not None:
            marks.add(mark)
    return frozenset(marks)


def _numeric_value(value: Any, decimal_marks: frozenset[str] = frozenset()) -> int | Decimal | None:
    """The number a stored value stands for, used only to order and compare; ``None`` if not numeric.

    Numeric strings may use ``.`` or ``,`` separators. With both, the last one is the decimal
    mark (``1.309,6``). A separator repeated in groups of three is a thousands separator. A single
    separator followed by three digits (``4,210``) is ambiguous: it is a decimal mark when it is in
    ``decimal_marks`` (other values of the same field use it so) or the leading group is ``0``,
    otherwise a thousands separator. Any other single separator is a decimal mark.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # The printed form, so a JSON 155.79 equals the string "155.79" (binary floats are inexact).
        return Decimal(repr(value)) if value == value and abs(value) != float("inf") else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _NUMERIC_TEXT.fullmatch(text) or not text[-1].isdigit():
        return None
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        decimal_sep = "." if text.rfind(".") > text.rfind(",") else ","
        whole, _, fraction = text.rpartition(decimal_sep)
        thousands_sep = "," if decimal_sep == "." else "."
        if not (_GROUPED[thousands_sep].fullmatch(whole) and fraction.isdigit()):
            return None
        text = whole.replace(thousands_sep, "") + "." + fraction
    elif dots or commas:
        sep = "." if dots else ","
        if text.count(sep) == 1 and (sep in decimal_marks or not _is_thousands_grouped(text, sep)):
            text = text.replace(sep, ".")
        elif _is_thousands_grouped(text, sep):
            text = text.replace(sep, "")
        else:
            return None
    return Decimal(text)


def _nested_values(record: dict, field: str) -> list[Any]:
    """Values of ``field`` in objects nested inside a record's fields (``_context`` excluded)."""
    found = []
    stack = [value for key, value in record.items() if key != "_context"]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if field in item:
                found.append(item[field])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


_MISSING = object()


def _field_value(record: dict, field: str) -> Any:
    """Value of ``field`` in a record, or ``_MISSING``.

    The field is looked up as a top-level key, then in ``_context``, then in nested objects
    (a feature's ``areas: [{"area": ...}]``), where it must occur exactly once to count.
    """
    if field in record:
        return record[field]
    context = record.get("_context")
    if isinstance(context, dict) and field in context:
        return context[field]
    nested = _nested_values(record, field)
    return nested[0] if len(nested) == 1 else _MISSING


def _rank_value(record: dict, field: str, decimal_marks: frozenset[str]) -> int | Decimal | None:
    """Numeric value of ``field`` for ranking a record, or ``None`` when missing or not numeric."""
    value = _field_value(record, field)
    return None if value is _MISSING else _numeric_value(value, decimal_marks)


def _field_equals(record: dict, field: str, expected: str | int | float, decimal_marks: frozenset[str]) -> bool:
    """Whether a record's ``field`` equals ``expected``: numerically when both are numbers, else as folded text.

    Both sides are read with the field's ``decimal_marks``. Null, booleans, objects and lists never match.
    """
    value = _field_value(record, field)
    if value is _MISSING or value is None or isinstance(value, (bool, dict, list)):
        return False
    number, wanted = _numeric_value(value, decimal_marks), _numeric_value(expected, decimal_marks)
    if number is not None and wanted is not None:
        return number == wanted
    return _fold(str(value).strip()) == _fold(str(expected).strip())


def lookup_records(
    source: DataSource,
    term: str = "",
    limit: int = 30,
    rank_by: str | None = None,
    descending: bool = True,
    where: Mapping[str, str | int | float | Sequence[str | int | float]] | None = None,
    *,
    records: Sequence[dict] | None = None,
) -> LookupResult:
    """Find every record of ``source`` whose serialised text contains each word of ``term`` as a whole token.

    ``where`` first keeps only records where every given field equals the given value, or any of
    the values when given a list or tuple (fields resolved as for ``rank_by``; numbers compared
    numerically so ``2 == 2.0 == "2"``, text compared ignoring case and accents; a record missing
    a field is excluded; an empty list matches no record). Term words may
    appear in any order and any field; matching ignores case and accents and searches all
    remaining records; an empty term matches every record. Ambiguous separators (``1.500``) are
    read per field: as decimal marks if any value of that field uses them so, else as thousands.
    ``total_matches`` counts records passing both filters. Without ``rank_by``, matches
    stay in source order. With ``rank_by``, matches are ordered by the numeric value of that
    field (see ``_rank_value``), largest first unless ``descending`` is false, ties in source
    order; records without a numeric value for it are left out. Values are returned exactly as
    stored. At most ``min(limit, 30)`` records are returned, each truncated to at most 600
    serialised characters with a ``_truncated`` field naming omitted fields; the ranked and
    filtered fields are kept. ``records`` are ``records_from_source(source)`` already parsed by the
    caller, so repeated lookups on one source parse it once.
    """
    limit = min(limit, _MAX_LOOKUP_RECORDS)
    patterns = [_term_pattern(word) for word in _fold(term).split()]
    if records is None:
        records = records_from_source(source)
    # Ambiguous separators are read per field, from evidence across all of the source's records.
    filters = {
        field: list(value) if isinstance(value, (list, tuple)) else value for field, value in (where or {}).items()
    }
    conditions = [
        (field, expected if isinstance(expected, list) else [expected], _decimal_marks(records, field))
        for field, expected in filters.items()
    ]
    matches = []
    for record in records:
        if not all(
            any(_field_equals(record, field, value, marks) for value in accepted)
            for field, accepted, marks in conditions
        ):
            continue
        if patterns:
            text = _fold(_serialize(record))
            if not all(pattern.search(text) for pattern in patterns):
                continue
        matches.append(record)
    selected = matches
    if rank_by is not None:
        marks = _decimal_marks(records, rank_by)
        keyed = [(value, record) for record in matches if (value := _rank_value(record, rank_by, marks)) is not None]
        # ``sorted`` is stable, also with ``reverse=True``, so ties keep source order.
        keyed = sorted(keyed, key=lambda pair: pair[0], reverse=descending)
        selected = [record for _, record in keyed]
    priority = ([rank_by] if rank_by is not None else []) + list(where or {})
    returned = [_truncate(record, priority) for record in selected[: max(limit, 0)]]
    return LookupResult(
        source.source_id,
        source.url,
        len(matches),
        returned,
        rank_by,
        descending,
        filters if where is not None else None,
    )


def _format_size(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.0f} KB"
    return f"{size} B"


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 3, 0)] + "..."


def _catalog_line(source: DataSource, budget: int) -> str:
    if source.kind == "download_xlsx" and not source.body:
        return _cut(f"{source.source_id} (spreadsheet, opened on demand) {source.url}", budget)
    records = records_from_source(source)
    fields = list(dict.fromkeys(key for record in records[:_CATALOG_FIELD_SAMPLE] for key in record))
    context = list(records[0].get("_context", {})) if records else []
    line = f"{source.source_id} ({source.kind}, {_format_size(source.size)}, {len(records)} records) {source.url}"
    if fields:
        line += "; fields: " + ", ".join(str(field) for field in fields if field != "_context")
    if context:
        line += "; context: " + ", ".join(map(str, context))
    samples = [
        _serialize({key: value for key, value in record.items() if key != "_context"})
        for record in records[:_CATALOG_SAMPLE_RECORDS]
    ]
    if not samples:
        return _cut(line, budget)
    # Half of the share stays for samples, so long field lists cannot crowd them out.
    line = _cut(line, budget // 2) + "; samples: "
    room = (budget - len(line) - len(" | ") * (len(samples) - 1)) // len(samples)
    return line + " | ".join(_cut(sample, room) for sample in samples)


def render_catalog(sources: Sequence[DataSource]) -> str:
    """List each source as one line: ID, kind, size, record count, URL, field and context names, and samples.

    Samples are the source's first two records (without ``_context``). A lazily opened spreadsheet
    (``download_xlsx`` with no body yet) is listed as "spreadsheet, opened on demand". The whole catalog
    is at most 2,500 characters; each source gets an equal share, half of it kept for samples, and
    longer parts are cut with ``...``.
    """
    if not sources:
        return ""
    budget = (_MAX_CATALOG_CHARS + 1) // len(sources) - 1
    return "\n".join(_catalog_line(source, budget) for source in sources)
