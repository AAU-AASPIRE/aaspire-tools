"""modplan's reader of the execution model (``modplan.execution``) against the shared fixtures in aas-model
(``tests/fixtures/execution/``, section 15 of its ``docs/execution-model.md``), the formula parser, and aas-model's
example AASs. Needs aas-model beside this repository (or $AAS_MODEL), the lab vocabulary (ontologies beside it, or
$LAB_VOCABULARY) and rdflib."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from modplan.execution import FormulaSyntaxError, Vocabulary, aliases, degree, parse, read
from modplan.execution.model import SEVERITIES
from modplan.execution.vocabulary import DEFAULT_FILE

AAS_MODEL = Path(os.environ.get("AAS_MODEL") or Path(__file__).resolve().parents[2] / "aas-model")
FIXTURES = AAS_MODEL / "tests" / "fixtures" / "execution"
EXAMPLES = AAS_MODEL / "examples" / "aas"
if not FIXTURES.is_dir():
    pytest.skip(f"no shared execution fixtures at {FIXTURES} (clone aas-model beside aaspire-tools)", allow_module_level=True)
NAMES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir() and p.name != "__pycache__")
VALID = [n for n in NAMES if n[0] in "abc"]
INVALID = [n for n in NAMES if n[0] == "d"]


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _vocabulary(name: str = "") -> Vocabulary:
    """The lab vocabulary and, where the fixture has one, its own terms."""
    pytest.importorskip("rdflib")
    lab = Path(DEFAULT_FILE)
    if not lab.is_file():
        pytest.skip(f"no lab vocabulary at {lab} (clone AAU-AASPIRE/ontologies beside aaspire-tools)")
    own = FIXTURES / name / "vocabulary.ttl"
    return Vocabulary.from_turtle(lab, *([own] if own.is_file() else []))


def _reading(name: str):
    return read(_json(FIXTURES / name / "aas.json"), _vocabulary(name))


# ── the fixtures ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", VALID)
def test_a_valid_fixture_reads_as_its_expected_json_says(name):
    assert _reading(name).to_canonical() == _json(FIXTURES / name / "expected.json")


@pytest.mark.parametrize("name", [n for n in INVALID if _json(FIXTURES / n / "expected.json")["findings"][0]["code"] in SEVERITIES])
def test_an_invalid_fixture_of_the_readers_codes_reads_with_exactly_its_finding(name):
    assert _reading(name).to_canonical()["findings"] == _json(FIXTURES / name / "expected.json")["findings"]


@pytest.mark.parametrize("name", [n for n in INVALID if _json(FIXTURES / n / "expected.json")["findings"][0]["code"] not in SEVERITIES])
def test_an_invalid_fixture_of_the_validators_codes_reads_without_a_finding_of_the_readers(name):
    reading = _reading(name)
    assert [f.code for f in reading.findings if f.code in SEVERITIES] == []


def test_a_reading_has_what_the_validator_needs_beside_the_canonical_form():
    reading = _reading("d26-translation-realized-by")
    (dosing,) = reading.capabilities
    assert dosing.translation is not None and "call" in dosing.translation.call and dosing.realized_by == ()
    specialization = _reading("c04-specialization-superset").capability("CapabilityDescription/AuxiliaryCapabilities/NeedlePositioning")
    (general,) = specialization.generalized_by
    assert general.target == ("https://smartproductionlab.aau.dk/aas/fixtures/c04-specialization-superset/component/"
                              "submodels/CapabilityDescription#AuxiliaryCapabilities/Positioning/Capability")
    assert specialization.pre[0].path.endswith("ConditionSet/Pre/Term_00")
    mappings = {k.property.rsplit("/", 1)[-1]: k.mappings for k in _reading("c02-knowledge").knowledge}
    assert mappings == {"Location": 0, "ContentVolume": 0, "AbsoluteX": 1}


def test_the_reader_does_not_raise_on_what_is_not_an_environment():
    for broken in ({}, {"submodels": [{"id": "x"}]}, {"submodels": [{"id": "x", "submodelElements": [{"modelType": "SubmodelElementCollection"}]}]}):
        assert read(broken, Vocabulary()).to_canonical()["capabilities"] == {}


# ── the formula parser ───────────────────────────────────────────────────────

def _ast(text: str) -> dict:
    return parse(text).to_canonical()


def n(x: float) -> dict:
    return {"number": x}


def test_multiplication_binds_tighter_than_addition_and_brackets_override():
    assert _ast("1 + 2 * 3") == {"+": [n(1), {"*": [n(2), n(3)]}]}
    assert _ast("(1 + 2) * 3") == {"*": [{"+": [n(1), n(2)]}, n(3)]}


def test_operators_of_one_level_are_left_associative():
    assert _ast("8 - 3 - 2") == {"-": [{"-": [n(8), n(3)]}, n(2)]}
    assert _ast("8 / 4 / 2") == {"/": [{"/": [n(8), n(4)]}, n(2)]}
    assert _ast("a * b / c") == {"/": [{"*": [{"alias": "a"}, {"alias": "b"}]}, {"alias": "c"}]}


def test_a_unary_minus_belongs_to_the_factor_after_it():
    assert _ast("-2 * 3") == {"*": [{"neg": n(2)}, n(3)]}
    assert _ast("2 - -3") == {"-": [n(2), {"neg": n(3)}]}
    assert _ast("--x") == {"neg": {"neg": {"alias": "x"}}}


def test_pi_is_not_an_alias_but_a_name_that_begins_with_it_is():
    assert _ast("2 * pi") == {"*": [n(2), {"pi": True}]}
    assert _ast("pi2") == {"alias": "pi2"}
    assert aliases(parse("pi * r * r + r")) == ("r",)


@pytest.mark.parametrize("text, value", [("1.5e-3", 0.0015), ("2E+2", 200.0), ("1e3", 1000.0), ("007", 7.0), ("0.25", 0.25)])
def test_numbers_have_a_fraction_and_an_exponent(text, value):
    assert _ast(text) == n(value)


def test_whitespace_between_tokens_is_ignored():
    assert _ast("  a+\tb \n") == _ast("a + b")


@pytest.mark.parametrize("text, position", [
    ("", 0), ("2 +* 3", 3), ("(1 + 2", 6), ("1 2", 2), ("1 + 2)", 5), ("2 $ 3", 2), (".5", 0), ("2.", 1), ("2e", 1),
    ("a +", 3), ("+ 1", 0), ("* 2", 0), ("3x", 1),
])
def test_a_syntax_error_carries_its_position(text, position):
    with pytest.raises(FormulaSyntaxError) as raised:
        parse(text)
    assert raised.value.position == position


def test_the_degree_is_in_the_aliases_that_vary():
    varying = {"V", "W"}
    assert degree(parse("2 * V + K"), varying) == 1
    assert degree(parse("K * L / 4"), varying) == 0
    assert degree(parse("V * W"), varying) == 2
    assert degree(parse("K / V"), varying) == 2
    assert degree(parse("V / K"), varying) == 1
    assert degree(parse("-(V + 1) * K"), varying) == 1
    assert degree(parse("pi * 3"), varying) == 0


def test_the_aliases_are_listed_in_order_each_once():
    assert aliases(parse("b + a * b - c")) == ("b", "a", "c")


# ── the examples ─────────────────────────────────────────────────────────────

def test_the_example_aases_read_without_findings():
    pytest.importorskip("rdflib")
    environments = {path.name: _json(path) for path in sorted(EXAMPLES.glob("*.json"))}
    merged = {key: [item for env in environments.values() for item in env.get(key, [])]
              for key in ("assetAdministrationShells", "submodels")}
    reading = read(merged, Vocabulary.default())
    assert reading.capabilities and reading.goals and reading.knowledge
    assert [f.to_canonical() for f in reading.findings] == []
    for name, environment in environments.items():
        read(environment)
