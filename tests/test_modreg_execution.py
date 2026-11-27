"""modreg's validator of the execution model (``modreg.execution``) against the shared fixtures in aas-model
(``tests/fixtures/execution/``, sections 11 and 15 of its ``docs/execution-model.md``), aas-model's example AASs, and the edge
cases of its rules that the fixtures do not reach. Needs aas-model beside this repository (or $AAS_MODEL), the lab vocabulary
and rdflib. `translation.coverage` (fixture d27) is the line planner's compiler's, not modreg's."""
from __future__ import annotations

import pytest

from test_modplan_execution import EXAMPLES, FIXTURES, INVALID, VALID, _json, _vocabulary          # noqa: E402

from modplan.execution import (                                                                    # noqa: E402
    Capability, Condition, Effect, FixedSubject, Literal, OneOf, Outcome, Reading, RuntimeSubject, SubjectRef, Vocabulary,
)
from modreg import execution                                                                       # noqa: E402
from modreg.__main__ import report_execution                                                       # noqa: E402
from modreg.environment import Environment                                                         # noqa: E402
from modreg.execution import validate                                                              # noqa: E402
from modreg.execution_levels import _correspondence, _inside, _interval, _within                    # noqa: E402
from modreg.ontology import Blueprint                                                              # noqa: E402
from modreg.service import Refused, Registry                                                       # noqa: E402
from test_modreg_ontology import TINY                                                              # noqa: E402

DOSING = "CapabilityDescription/AuxiliaryCapabilities/Dosing/CapabilityRelations"


def _findings(name: str) -> list[dict]:
    return [f.to_canonical() for f in validate(_json(FIXTURES / name / "aas.json"), _vocabulary(name))]


# ── the fixtures ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", VALID)
def test_a_valid_fixture_has_no_findings(name):
    assert _findings(name) == []


# What the shared fixtures test that is not modreg's: the Translations' coverage is checked by the line planner's compiler.
NOT_MODREGS = {"d27-translation-coverage"}


@pytest.mark.parametrize("name", [n for n in INVALID if n not in NOT_MODREGS])
def test_an_invalid_fixture_has_exactly_its_finding(name):
    assert _findings(name) == _json(FIXTURES / name / "expected.json")["findings"]


def test_every_code_of_section_11_but_level_derived_has_a_fixture_and_modreg_checks_all_but_coverage():
    codes = {_json(FIXTURES / n / "expected.json")["findings"][0]["code"] for n in INVALID}
    assert len(codes) == len(INVALID) == 29
    assert codes - {"translation.coverage"} <= set(execution.RULES)


# ── the examples ─────────────────────────────────────────────────────────────

def _examples() -> dict:
    environments = [_json(path) for path in sorted(EXAMPLES.glob("*.json"))]
    return {key: [item for env in environments for item in env.get(key, [])] for key in ("assetAdministrationShells", "submodels")}


def test_the_example_aases_have_the_findings_they_are_known_to_have():
    """The module's Dosing, Weighing and Positioning do not carry the End effects of the pump's, the scale's and the axis's
    capabilities they generalize (section 10: no superset until Phase 13's generator writes them), and nothing else is wrong:
    the declarations resolve, the formulas are linear. A change of this list shows here."""
    base = "CapabilityDescription/AuxiliaryCapabilities"
    assert [(f.code, f.severity, f.path) for f in validate(_examples(), _vocabulary())] == [
        ("level.incomplete", "warning", f"{base}/Dosing/CapabilityRelations/EffectSet/End"),
        ("level.incomplete", "warning", f"{base}/Positioning/CapabilityRelations/EffectSet/End"),
        ("level.incomplete", "warning", f"{base}/Weighing/CapabilityRelations/EffectSet/End"),
    ]


# ── operators, operands ──────────────────────────────────────────────────────

@pytest.mark.parametrize("op, cardinality, value_type, suits", [
    ("equal", "single", "string", True), ("notEqual", "single", "boolean", True), ("assign", "single", "reference", True),
    ("unchanged", "single", "number", True), ("measured", "single", "string", True), ("clear", "single", "reference", True),
    ("less", "single", "number", True), ("increase", "single", "number", True), ("decrease", "single", "number", True),
    ("less", "single", "string", False), ("greaterOrEqual", "single", "boolean", False), ("increase", "single", "string", False),
    ("contains", "single", "number", False), ("add", "single", "reference", False), ("remove", "single", "string", False),
    ("contains", "set", "reference", True), ("notContains", "set", "string", True), ("add", "set", "reference", True),
    ("remove", "set", "number", True),
    ("equal", "set", "reference", False), ("assign", "set", "reference", False), ("increase", "set", "number", False),
    ("unchanged", "set", "reference", False), ("clear", "set", "reference", False),
])
def test_an_operator_suits_the_cardinality_and_value_type_of_the_table_of_5_4(op, cardinality, value_type, suits):
    assert execution._suits(op, cardinality, value_type) is suits


# ── levels: tighter and wider, subjects by identity ──────────────────────────

def _condition(op: str, x: float) -> Condition:
    return Condition("p", "lab:Position", SubjectRef(0), op, Literal("number", x))


@pytest.mark.parametrize("general, specific, within", [
    (("greaterOrEqual", 3), ("greaterOrEqual", 5), True),        # a tighter bound
    (("greaterOrEqual", 3), ("greaterOrEqual", 3), True),        # an equal one
    (("greaterOrEqual", 5), ("greaterOrEqual", 3), False),       # a wider one
    (("greaterOrEqual", 3), ("greater", 3), True),
    (("greater", 3), ("greaterOrEqual", 3), False),              # 3 itself is admitted now
    (("greater", 3), ("greaterOrEqual", 3.5), True),
    (("lessOrEqual", 10), ("less", 10), True),
    (("less", 10), ("lessOrEqual", 10), False),
    (("lessOrEqual", 10), ("lessOrEqual", 12), False),
    (("greaterOrEqual", 3), ("equal", 4), True),                 # a value inside the bound
    (("greaterOrEqual", 3), ("equal", 2), False),                # a value outside it contradicts it
    (("equal", 1), ("equal", 1), True), (("equal", 1), ("equal", 2), False),
    (("greaterOrEqual", 3), ("lessOrEqual", 5), False),          # an upper bound does not tighten a lower one
    (("notEqual", 1), ("equal", 2), True), (("notEqual", 1), ("equal", 1), False), (("notEqual", 1), ("notEqual", 1), True),
])
def test_a_specific_bound_is_equal_or_tighter_where_it_admits_nothing_the_general_one_does_not(general, specific, within):
    assert _within(_condition(*general), _condition(*specific)) is within


def test_an_interval_is_inside_another_at_its_open_and_closed_ends():
    assert _inside(_interval(_condition("equal", 3)), _interval(_condition("greaterOrEqual", 3)))
    assert not _inside(_interval(_condition("greaterOrEqual", 3)), _interval(_condition("equal", 3)))
    assert _inside(_interval(_condition("less", 3)), _interval(_condition("lessOrEqual", 3)))


def test_subjects_correspond_by_identity_not_position():
    def capability(*subjects):
        return Capability("aas", "p", None, tuple(subjects))

    axis, pump = FixedSubject("This axis", "urn:axis"), FixedSubject("This pump", "urn:pump")
    unit, other = RuntimeSubject("Unit", "urn:unit"), RuntimeSubject("Shuttle", "urn:shuttle")
    general = capability(axis, unit, RuntimeSubject("Another unit", "urn:unit"))
    specific = capability(pump, other, RuntimeSubject("A unit", "urn:unit"), unit, axis)
    assert _correspondence(general, specific) == {0: 4, 1: 2, 2: 3}       # the n-th of a kind to the n-th
    assert _correspondence(capability(pump), capability(axis)) == {}


# ── effects ──────────────────────────────────────────────────────────────────

def _assign(path: str, value: bool, prop: str = "lab:Weighed") -> Effect:
    return Effect(path, prop, SubjectRef(0), "assign", Literal("boolean", value))


def _conflicts(*end, subjects=(FixedSubject("This", "urn:a"),)) -> list[str]:
    capability = Capability("aas", "C", None, subjects, end=tuple(end))
    model = execution._Model(Environment({}), Reading((capability,), (), (), ()), Vocabulary())
    return [f.path for f in execution.effect_conflicts(model)]


def test_two_assigns_of_one_variable_with_different_operands_conflict_at_the_later():
    assert _conflicts(_assign("a", True), _assign("b", False)) == ["b"]
    assert _conflicts(_assign("a", True), _assign("b", False), _assign("c", False)) == ["b", "c"]


def test_the_same_assign_twice_and_assigns_of_different_variables_do_not_conflict():
    assert _conflicts(_assign("a", True), _assign("b", True)) == []
    assert _conflicts(_assign("a", True), _assign("b", False, "lab:Capped")) == []
    assert _conflicts(Effect("a", "lab:ContentVolume", SubjectRef(0), "assign", Literal("number", 1.0)),
                      Effect("b", "lab:ContentVolume", SubjectRef(0), "increase", Literal("number", 2.0))) == []


def test_the_outcomes_of_a_one_of_are_apart_but_each_is_applied_with_the_effects_that_always_happen():
    one = OneOf("lab:FillResult", (Outcome("In", None, (_assign("i", True),)), Outcome("Out", None, (_assign("o", False),))), "one")
    assert _conflicts(one) == []
    assert _conflicts(_assign("a", True), one) == ["o"]


# ── the command line and the service ─────────────────────────────────────────

def test_the_command_line_reports_the_findings_of_an_environment_and_fails_on_an_error(capsys):
    assert report_execution([_json(FIXTURES / "d21-effect-target" / "aas.json")], partial=False) is False
    out = capsys.readouterr().out
    assert out.startswith("execution model\n  error: ") and "effect.target: " in out
    assert report_execution([_json(FIXTURES / "d15-operator-equal-measured" / "aas.json")], partial=False) is True
    assert "warning: " in capsys.readouterr().out
    assert report_execution([_json(FIXTURES / "a01-literal-number" / "aas.json")], partial=False) is True
    assert capsys.readouterr().out == ""


def test_a_partial_environment_leaves_out_the_references_that_lead_out_of_it():
    environment = _json(FIXTURES / "d08-reference-unresolved" / "aas.json")
    assert [l.message.split(":")[0] for l in execution.check(environment, _vocabulary())] == ["reference.unresolved"]
    assert execution.check(environment, _vocabulary(), partial=True) == []


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "ontology").mkdir()
    (tmp_path / "ontology" / "tiny.ttl").write_text(TINY, encoding="utf-8")
    return Registry(tmp_path / "registry", Blueprint(tmp_path / "ontology"))


PROFILE = {"aas_type": "ModuleTypeAAS", "id_short": "EmptyModuleAAS"}


def test_the_service_refuses_an_aas_with_an_error_of_the_execution_model(registry, monkeypatch):
    monkeypatch.setattr("modreg.service.check_execution", lambda env, partial: execution.check(
        _json(FIXTURES / "d21-effect-target" / "aas.json"), partial=partial))
    with pytest.raises(Refused) as refused:
        registry.register(PROFILE)
    assert refused.value.status == 422 and refused.value.step == "check"
    assert "effect.target" in refused.value.reasons[-1]


def test_the_service_registers_an_aas_with_a_warning_and_says_so(registry, monkeypatch):
    monkeypatch.setattr("modreg.service.check_execution", lambda env, partial: execution.check(
        _json(FIXTURES / "d15-operator-equal-measured" / "aas.json"), partial=partial))
    registration = registry.register(PROFILE)
    assert len(registration.warnings) == 1 and registration.warnings[0].startswith("warning: ")
    assert registry.register(PROFILE).unchanged


def test_a_profile_of_a_module_alone_registers_without_findings_of_the_execution_model(registry):
    assert registry.register(PROFILE).warnings == []
