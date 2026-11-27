"""The execution model as modreg writes it from a module spec (`modreg.execution_form`; `modreg profile --execution`), and what its
validator (`modreg.execution.validate`) says of it. The round trip to modplan's Domain is in test_modplan_roundtrip."""
from __future__ import annotations

import copy
import functools
from pathlib import Path

import pytest

from modgen import load
from modgen.spec import CapabilityUse

from modplan.execution import Vocabulary, VocabularyProperty
from modreg import capabilities, model, profile as profiles
from modreg.__main__ import main
from modreg.execution import validate
from modreg.execution_form import Execution

SPECS = Path(__file__).resolve().parents[2] / "aseptic-line" / "cell" / "modules"
NAMES = ["filling", "capping", "stoppering", "filling-2"]
RELATIONS = {"Subjects", "ConditionSet", "EffectSet", "Duration"}


@functools.cache
def built(name: str):
    """The spec of a module of the line and the AASs (the module's first) of its module and components, as the constraints say what
    their capabilities need and change (the old form, the default) and as the execution model says it."""
    spec = load(SPECS / f"{name}.yaml")
    target = next(iter(spec.targets))
    old, new = ([model.build(p) for p in profiles.describe_all(spec, target, execution=execution)] for execution in (False, True))
    return spec, old, new


def containers(envs):
    """Every capability container of the AASs: (the AAS's idShort, the set's, its own, the container's elements by idShort)."""
    for env in envs:
        for submodel in env["submodels"]:
            if submodel["idShort"] == "CapabilityDescription":
                for held in submodel["submodelElements"]:
                    for box in held["value"]:
                        yield env["assetAdministrationShells"][0]["idShort"], held["idShort"], box["idShort"], \
                            {e["idShort"]: e for e in box["value"]}


def relations(box: dict) -> dict:
    return {e["idShort"]: e for e in box["CapabilityRelations"]["value"]}


def merged(envs) -> dict:
    return {key: [item for env in envs for item in env[key]] for key in ("assetAdministrationShells", "submodels")}


@pytest.mark.parametrize("name", NAMES)
def test_the_default_is_the_old_form_and_the_new_form_adds_the_relations(name):
    _, old, new = built(name)
    for _, _, _, box in containers(old):
        assert not RELATIONS & set(relations(box))
    expected = {(aas, held, own) for aas, held, own, _ in containers(old)}
    assert {(aas, held, own) for aas, held, own, _ in containers(new)} == expected
    assert any(RELATIONS & set(relations(box)) for *_, box in containers(new))
    for aas, held, own, box in containers(new):
        assert "ConditionSet" in relations(box) or "EffectSet" in relations(box) or held == "OfferedCapabilities", (aas, own)


@pytest.mark.parametrize("name", NAMES)
def test_the_constraint_set_keeps_only_what_is_for_matching(name):
    """No property of a capability is a state (no value, no range) and no constraint names one (rule constraint.state)."""
    _, old, new = built(name)
    assert any("ConstraintSet" in relations(box) for *_, box in containers(old))
    for aas, held, own, box in containers(new):
        assert "ConstraintSet" not in relations(box), (aas, own)
        for prop in box.get("PropertySet", {"value": []})["value"]:
            value = next(e for e in prop["value"] if e["idShort"] == "Value")
            assert value["modelType"] == "Range" or value.get("value") is not None, (aas, own, prop["idShort"])
        assert not any("SameProperty" in {e["idShort"] for e in prop["value"]} for prop in box.get("PropertySet", {"value": []})["value"])


@pytest.mark.parametrize("name", NAMES)
def test_every_state_a_term_reads_or_changes_is_declared_in_the_aas_of_its_subject(name):
    """The validator finds no declaration.missing and no effect.target, and the Knowledge declares what the terms are about, in the
    components (the module's own states are none here)."""
    _, _, new = built(name)
    findings = validate(merged(new))
    assert not [f for f in findings if f.code in ("declaration.missing", "effect.target")]
    declared = {env["assetAdministrationShells"][0]["idShort"]: [e["idShort"] for s in env["submodels"] if s["idShort"] == "Variables"
                                                                 for k in s["submodelElements"] if k["idShort"] == "Knowledge"
                                                                 for e in k["value"] if e["idShort"] != "Target"] for env in new}
    assert declared[next(a for a in declared if a.endswith("LinearAxisAAS"))] == ["Position", "Referenced"]
    assert not declared[next(a for a in declared if a.endswith("ModuleAAS"))]


@pytest.mark.parametrize("name", NAMES)
def test_the_validator_finds_nothing_but_the_cardinality_the_vocabulary_lacks(name):
    """Referenced, Tared and Weight have a value type in the lab vocabulary but no cardinality, so a term about them is an error
    (declaration.vocabulary, D14) until ontologies gives them one."""
    _, _, new = built(name)
    findings = validate(merged(new))
    assert findings and {f.code for f in findings} == {"declaration.vocabulary"} and {f.severity for f in findings} == {"error"}
    lacking = {iri.rsplit("/", 1)[-1] for iri, p in Vocabulary.default().properties.items() if p.cardinality is None}
    assert {"Referenced", "Tared", "Weight"} <= lacking


def test_with_that_cardinality_the_aases_have_no_finding_at_all(monkeypatch):
    """The only finding is the vocabulary's gap: as if the vocabulary said `single` for the three, the declarations written from it
    and the terms are as it says, and the validator finds nothing (no errors, no warnings), on the module with all of them."""
    vocabulary = Vocabulary.default()
    said = {iri: VocabularyProperty(p.value_type, "single", p.derived) for iri, p in vocabulary.properties.items()
            if p.cardinality is None and iri.rsplit("/", 1)[-1] in ("Referenced", "Tared", "Weight")}
    complete = Vocabulary({**vocabulary.properties, **said})
    monkeypatch.setattr(Vocabulary, "default", classmethod(lambda cls: complete))
    spec = load(SPECS / "filling-2.yaml")
    envs = [model.build(p) for p in profiles.describe_all(spec, next(iter(spec.targets)), execution=True)]
    assert validate(merged(envs), complete) == []


def test_the_cli_writes_the_new_form(tmp_path):
    assert main(["profile", str(SPECS / "stoppering.yaml"), "--execution", "--out", str(tmp_path)]) == 0
    text = (tmp_path / "StopperingPistonAAS.json").read_text(encoding="utf-8")
    assert "Subjects" in text and "ConditionSet" in text and "EffectSet" in text
    assert main(["profile", str(SPECS / "stoppering.yaml"), "--out", str(tmp_path / "old")]) == 0
    assert "Subjects" not in (tmp_path / "old" / "StopperingPistonAAS.json").read_text(encoding="utf-8")


def test_a_name_that_is_a_parameter_and_a_state_must_be_named_by_its_item():
    """`Position` of the axis is a parameter of MoveAxis and its position: a Pre about it does not say which of the two is meant,
    so it is refused in the execution model (as <Item>.Position it is the state)."""
    spec = load(SPECS / "filling.yaml")
    spec = copy.deepcopy(spec)
    given = spec.skills["MoveAxis"]
    bare = given.model_copy(update={"capability": CapabilityUse(precondition={"Position": ">= 1"})})
    capabilities.component_capability(spec, "LinearAxis", "MoveAxis", bare, "Positioning")            # the old form takes it
    with pytest.raises(ValueError, match="a parameter and a state"):
        capabilities.component_capability(spec, "LinearAxis", "MoveAxis", bare, "Positioning", Execution())
    through = given.model_copy(update={"capability": CapabilityUse(precondition={"LinearAxis.Position": ">= 1"})})
    capabilities.component_capability(spec, "LinearAxis", "MoveAxis", through, "Positioning", Execution())


def test_a_post_given_needs_the_parameter():
    spec = load(SPECS / "filling.yaml")
    decl = spec.skills["MoveAxis"].model_copy(update={"parameters": {}})
    with pytest.raises(ValueError, match="no parameter Position"):
        capabilities.component_capability(spec, "LinearAxis", "MoveAxis", decl, "Positioning", Execution())
