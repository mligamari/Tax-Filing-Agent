"""The model's answer is untrusted: it is checked locally before anything uses it."""
import pytest

from taxfiling.mapper import check_proposal, prepare
from taxfiling.mapper.heuristic import normalize

from .conftest import MESSY_WORKBOOK, example


@pytest.fixture(scope="module")
def messy():
    return prepare(MESSY_WORKBOOK)


def ids(prepared) -> dict[str, str]:
    """header -> column id in the first sheet"""
    return {c.header: c.id for c in prepared.profile.sheets[0].columns}


def answer(prepared, columns: dict[str, str], box12=()) -> dict:
    col = ids(prepared)
    return {"sheet": "S1", "notes": [],
            "columns": [{"field": f, "column": col[h], "confidence": "high", "reason": "test"} for f, h in columns.items()],
            "box12": [{"code_column": col[c], "amount_column": col[a]} for c, a in box12]}


def test_a_correct_answer_becomes_a_reader_mapping(messy):
    from examples.va_2025_messy.make_messy_workbook import EXPECTED
    mapping, review = check_proposal(answer(messy, EXPECTED, [("12A Cd", "12A Amt")]), messy.profile)
    assert review.complete and not review.warnings, review.warnings
    assert mapping["sheet"] == "YE Export" and mapping["header_row"] == 4
    assert mapping["columns"]["ssn"] == "Social Sec #"
    assert mapping["box12"] == [{"code": "12A Cd", "amount": "12A Amt"}]


@pytest.mark.parametrize("bad,error", [
    ({"sheet": "S9"}, "not in the workbook"),
    ({"columns": [{"field": "ssn", "column": "S1.C99", "confidence": "high", "reason": ""}]}, "not in the workbook"),
    ({"columns": [{"field": "password", "column": "S1.C06", "confidence": "high", "reason": ""}]}, "unknown field"),
    ({"columns": [{"field": "ssn", "column": "S1.C06", "confidence": "high", "reason": ""},
                  {"field": "ssn", "column": "S1.C07", "confidence": "high", "reason": ""}]}, "mapped twice"),
    ({"columns": [{"field": "first_name", "column": "S1.C03", "confidence": "high", "reason": ""},
                  {"field": "last_name", "column": "S1.C03", "confidence": "high", "reason": ""}]}, "used for both"),
    ("not an object", "not a JSON object"),
])
def test_bad_answers_are_refused(messy, bad, error):
    base = {"sheet": "S1", "columns": [], "box12": [], "notes": []}
    ans = {**base, **bad} if isinstance(bad, dict) else bad
    mapping, review = check_proposal(ans, messy.profile)
    assert mapping is None
    assert any(error in e for e in review.errors), review.errors


def test_a_column_from_another_sheet_is_refused():
    prepared = prepare(example(2025)[0])
    other = prepared.profile.sheets[1].columns[0].id
    ans = {"sheet": "S1", "columns": [{"field": "control_number", "column": other, "confidence": "high",
                                       "reason": ""}], "box12": [], "notes": []}
    mapping, review = check_proposal(ans, prepared.profile)
    assert mapping is None and "not the chosen sheet" in review.errors[0]


def test_local_checks_flag_a_column_that_does_not_fit_its_field(messy):
    """A model that swaps two columns is caught by their shapes, not trusted."""
    _, review = check_proposal(answer(messy, {"ssn": "Last", "last_name": "Social Sec #", "state": "Dept",
                                              "box1_wages": "City", "retirement_plan": "Hire Date"}), messy.profile)
    text = " ".join(review.warnings)
    for field in ("ssn", "last_name", "state", "box1_wages", "retirement_plan"):
        assert f"{field} <-" in text


def test_missing_required_fields_make_the_mapping_incomplete(messy):
    mapping, review = check_proposal(answer(messy, {"ssn": "Social Sec #"}), messy.profile)
    assert mapping is not None and not review.complete
    assert "box1_wages" in review.missing_required


def test_model_text_is_cut_to_size(messy):
    ans = answer(messy, {"ssn": "Social Sec #"})
    ans["columns"][0]["reason"] = "x" * 5000
    ans["notes"] = ["y" * 5000]
    _, review = check_proposal(ans, messy.profile)
    assert len(review.rows[0]["reason"]) == 200 and len(review.notes[0]) == 300


@pytest.mark.parametrize("header,expected", [
    ("Fed W/H", "federal withheld"), ("SS Wgs", "social security wages"), ("Social Sec #", "social security number"),
    ("3rd Party Sick", "third party sick"), ("12A Cd", "12a code"), ("Co Zip", "company zip"),
])
def test_header_normalization(header, expected):
    assert normalize(header) == expected
