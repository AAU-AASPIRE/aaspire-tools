"""The planning domain read from the execution model (`modplan.read.read_execution`): what it reads of a capability, and what it refuses.
(That it is the domain the constraints give is test_modplan_roundtrip.)"""
import copy
from pathlib import Path
import sys

import pytest

from aas_model.constants import EXECUTION_INVARIANT, EXECUTION_POST
from modplan.domain import PRODUCT, Var
from modplan.read import Unsupported, read_execution

sys.path.insert(0, str(Path(__file__).parent))
from test_modreg_execution_form import built                                                      # noqa: E402


@pytest.fixture(scope="module")
def filling():
    _, _, envs = built("filling")
    return envs


def test_a_capability_is_read_from_its_terms(filling):
    domain = read_execution(filling[0], filling[1:])
    axis, dosing = Var("FillingLinearAxisAAS", "Position"), domain.capabilities["Dosing"]
    assert sorted(domain.capabilities) == ["Dosing", "Filling", "Positioning", "Referencing", "Taring", "Weighing"]
    assert (dosing.skill, dosing.component) == ("Dispense", "Pump")
    assert [(c.var, str(c.expression)) for c in dosing.pre] == [(axis, ">= FillPosition")]
    assert [(c.var, str(c.expression)) for c in dosing.post] == [(Var(PRODUCT, "ContentVolume"), "+= Volume"),
                                                                 (Var(PRODUCT, "Weighed"), "= false")]
    # A During effect is a definition that holds while it runs: an Invariant "=" the formula.
    assert [str(c.expression) for c in dosing.invariant if c.var.meaning == "OutletImmersion"][0].startswith("= LinearAxisPosition - NestPosition")
    # The Rate of the End effect, and the Duration.
    assert dosing.rates == {Var(PRODUCT, "ContentVolume"): "FlowRate"} and str(dosing.after) == "= Volume / FlowRate"
    assert dosing.variables["LinearAxisPosition"] == axis and dosing.variables["RimHeight"] == Var(PRODUCT, "RimHeight")
    # What the Post gives the skill is what it is given.
    assert [(c.prop, str(c.expression)) for c in domain.capabilities["Positioning"].post] == [("Position", "given")]
    # The rest state is what Resetting leaves; the results are the vocabulary's.
    assert domain.rest == {axis: 0.0, Var("FillingLinearAxisAAS", "Referenced"): True, Var("FillingScaleAAS", "Tared"): True}
    assert list(domain.capabilities["Filling"].results) == ["FillWeight"]


def refuse(envs, change) -> str:
    changed = copy.deepcopy(envs)

    def walk(x):
        if isinstance(x, dict):
            change(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(changed)
    with pytest.raises(Unsupported) as e:
        read_execution(changed[0], changed[1:])
    return str(e.value)


def test_an_operator_the_domain_cannot_hold_is_refused_by_name(filling):
    """modplan supports conjunctions and the comparisons, assignments and increases of its Domain (section 12 of the specification);
    another operator is refused, naming the capability and the operator."""
    done = []

    def contains(x):
        if x.get("idShort") == "Operator" and x.get("value") == "equal" and not done:
            x["value"] = "contains"
            done.append(x)
    found = refuse(filling, contains)
    assert "does not plan with the operator contains" in found and found.startswith(("Referencing", "Positioning", "Dosing", "Taring", "Weighing", "Filling"))


def test_a_condition_that_is_checked_not_caused_is_refused(filling):
    """A Post condition (ConditionSet/Post) is checked at the end, where the Domain's Post is what a capability does."""
    def postcondition(x):
        if x.get("idShort") == "Invariant" and (x.get("semanticId") or {}).get("keys", [{}])[0].get("value") == EXECUTION_INVARIANT:
            x["idShort"] = "Post"
            x["semanticId"] = {"type": "ExternalReference", "keys": [{"type": "GlobalReference", "value": EXECUTION_POST}]}
    assert "a Post condition" in refuse(filling, postcondition)
