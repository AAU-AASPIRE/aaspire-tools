"""Phase 2 of the execution plan: modreg writes what a capability needs and changes in the execution model, modplan reads it,
and the module's planning domain and the plans are the same as from the constraints of the ConstraintSet (the old form).

For every module spec of the line: `read` (the old form, `modplan.read`) and `read_execution` (the new) give the same Domain up to the
normalization below, and `plan` finds the same steps, arguments included, in both.

**Normalization.** A Domain's constraints are about state variables (`Var`), and the two forms name them otherwise; nothing else
differs. (1) The subject of a variable: the old form names the data point of the module that is the same property (the position
of the linear axis is `LinearAxis_ActualPosition`, its being homed `LinearAxis_Homed`) or the component (`Scale`); the
new form the AAS of the subject (`FillingLinearAxisAAS`). The old subject is taken to the AAS of the component it starts with.
That is sound where no two states of the domain become one: a component has at most one state of each meaning, and the test
checks that the mapping of the old variables is one to one. A property of the product is the products' `PRODUCT` in both.
(2) What a name in an expression is: the old form names the properties of the capability (a state too: `LinearAxisPosition`),
the new one the aliases of the Uses; each name is replaced by what it is, a parameter, a value, or the (normalized) state variable,
so that the names do not matter and what they stand for does. (3) A property of the old form that is no state (a named value as
a variable of the component or the module) is no variable in the new form: it is in the parameters or the values, which are
compared, and every state the old form has is compared (`states`). (4) The constraint's own name is compared only where the
planner reads it, for `given` (the parameter). (5) An Invariant `= formula` that no Post sets is a During effect in the new
form; the Domain has it as the Invariant it is, in the order of the constraints of its kind otherwise as the model has them.
"""
from __future__ import annotations

import functools
from pathlib import Path
import sys

import pytest

from modsync.described import described

from modplan.check import check
from modplan.discover import discover, task
from modplan.domain import PRODUCT, Domain, Var
from modplan.expression import Expression, Formula
from modplan.plan import NoPlan, plan, reach
from modplan.read import read, read_execution
from modplan.vocabulary import Vocabulary

from modreg.profile import component_identity

sys.path.insert(0, str(Path(__file__).parent))
from test_modreg_execution_form import NAMES, built as aases                                     # noqa: E402

SYRINGE = {"InnerDiameter": 6.35, "InnerBottomHeight": 11.0, "RimHeight": 66.0, "MinImmersion": -2.0, "MaxImmersion": 3.0}
CONTENT = Var(PRODUCT, "ContentVolume")
# What the tests of modplan and the evaluation of the planners (cell/eval/planners.py) ask: (capability, values, results).
CASES = {
    "filling": [("Filling", {"FillVolume": 2.0}, ("FillWeight",)), ("Filling", {"FillVolume": 15.0}, ("FillWeight",)),
                ("Filling", {"FillVolume": 0.5}, ()), ("Filling", {"FillVolume": 1.0}, ("FillWeight",)),
                ("Filling", {"FillVolume": 2.0}, ("Weight",)),
                ("BottomUpFilling", {"FillVolume": 1.0, **SYRINGE}, ("FillWeight",)),
                ("BottomUpFilling", {"FillVolume": 1.0, **SYRINGE}, ()),
                ("BottomUpFilling", {"FillVolume": 0.25, **SYRINGE}, ()),
                ("BottomUpFilling", {"FillVolume": 0.5, **SYRINGE, "MinImmersion": -1.5, "MaxImmersion": 1.0}, ()),
                ("BottomUpFilling", {"FillVolume": 0.5, **SYRINGE, "MinImmersion": 0.0, "MaxImmersion": 1.0}, ())],
    "stoppering": [("Stoppering", {}, ())],
    "capping": [("Capping", {}, ())],
}
CASES["filling-2"] = CASES["filling"]


@functools.cache
def built(name: str):
    """The spec, the AASs of its module and components in each form (the module's first), and the Domain read from each."""
    spec, old, new = aases(name)
    return spec, old, new, read(old[0], old[1:]), read_execution(new[0], new[1:])


# ── the normalization ────────────────────────────────────────────────────────

def fixed(var: Var, spec, old: bool) -> Var | None:
    """The variable in the new form's terms (1): None for what is no state of anything (3)."""
    if not old or var.subject == PRODUCT:
        return var
    for item in spec.equipment:
        if var.subject == item or var.subject.startswith(f"{item}_"):
            return Var(component_identity(spec, item)[0], var.meaning)
    return None


def canonical(domain: Domain, spec, old: bool) -> dict:
    """Everything the planner reads of a domain, with the names of states and of properties replaced by what they stand for."""
    def fix(var):
        return fixed(var, spec, old)

    def resolve(cap, name):
        if name in cap.parameters:
            return "parameter", name
        if name in cap.values:
            return "value", name, repr(cap.values[name])
        if name in cap.variables:
            return "state", fix(cap.variables[name])
        return "unknown", name

    def tree(cap, node):
        if isinstance(node, str):
            return node if node == "pi" else resolve(cap, node)
        if isinstance(node, tuple):
            return node[0], *[tree(cap, part) for part in node[1:]]
        return node

    def unfold(cap, e: Expression):
        if isinstance(e.operand, Formula):
            return e.op, tree(cap, e.operand.tree)
        return e.op, resolve(cap, e.operand) if isinstance(e.operand, str) else e.operand

    def constraints(cap, found):
        said = [(fix(c.var), c.prop if c.expression.op == "given" else None, unfold(cap, c.expression)) for c in found]
        assert all(v is not None for v, _, _ in said), (cap.name, said)
        return said

    caps = {}
    for name, cap in domain.capabilities.items():
        states = {fix(var) for n, var in cap.variables.items() if n not in cap.parameters and n not in cap.values} - {None}
        caps[name] = {
            "meaning": cap.meaning, "skill": cap.skill, "component": cap.component,
            "parameters": {n: (p.minimum, p.maximum, p.default) for n, p in cap.parameters.items()},
            "values": {n: repr(v) for n, v in cap.values.items()},
            "pre": constraints(cap, cap.pre), "post": constraints(cap, cap.post),
            # (5) The order of the constraints of one kind is the planner's where it chooses; an Invariant is checked as a whole.
            "invariant": sorted(constraints(cap, cap.invariant), key=repr),
            "rates": {fix(var): resolve(cap, rate) for var, rate in cap.rates.items()},
            "after": None if cap.after is None else unfold(cap, cap.after),
            "results": {r: constraints(cap, found) for r, found in cap.results.items()},
            "states": sorted(states, key=repr),
        }
    return {"module": domain.module, "capabilities": caps, "rest": {fix(v): x for v, x in domain.rest.items()},
            "changed": {fix(v) for v in domain.changed()}, "nonlinear": domain.nonlinear(),
            "product": {v for cap in domain.capabilities.values() for v in cap.variables.values() if v.subject == PRODUCT}}


def variables(domain: Domain) -> set[Var]:
    """Every variable of a domain the planner could meet."""
    found = set(domain.rest) | domain.changed()
    for cap in domain.capabilities.values():
        found |= {c.var for c in (*cap.pre, *cap.post, *cap.invariant)} | set(cap.rates) | set(cap.variables.values())
    return found


# ── the domains ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", NAMES)
def test_the_domain_read_from_the_execution_model_is_the_one_read_from_the_constraints(name):
    spec, _, _, old, new = built(name)
    assert canonical(new, spec, False) == canonical(old, spec, True)


@pytest.mark.parametrize("name", NAMES)
def test_the_subject_of_a_variable_names_one_state_in_both_forms(name):
    """Normalization (1) is one to one: two states of the old form never become one of the new."""
    spec, _, _, old, new = built(name)
    mapping = {var: fixed(var, spec, True) for var in variables(old)}
    states = {var: found for var, found in mapping.items() if found is not None}
    assert len(set(states.values())) == len(states)
    assert {v for v in variables(new) if v.subject != PRODUCT} <= set(states.values())
    # What the old form does not map is the property of a component or the module that is no state: a named value or a parameter.
    for var, found in mapping.items():
        if found is None:
            assert var.subject == "module" or var.subject in spec.equipment, var


@pytest.mark.parametrize("name", NAMES)
def test_the_variables_are_keyed_by_the_aas_of_the_subject_and_the_meaning(name):
    spec, old, new, _, domain = built(name)
    components = {component_identity(spec, item)[0] for item in spec.equipment}
    assert {v.subject for v in variables(domain)} <= components | {PRODUCT}
    assert variables(domain) & set(domain.rest) == set(domain.rest)


# ── the plans ────────────────────────────────────────────────────────────────

def outcome(domain: Domain, capability: str, values: dict, results: tuple, start: dict | None = None):
    """The steps (capability, arguments and what runs along) or why there are none."""
    if capability not in domain.capabilities:
        domain = Domain(domain.module, {**domain.capabilities, capability: task(Vocabulary(), capability)}, domain.rest)
    try:
        steps = plan(domain, capability, values, results, start=start)
    except NoPlan as e:
        return "no plan", str(e)
    along = lambda s: [(a.capability, a.args, along(a)) for a in s.along]                # noqa: E731
    return "plan", [(s.capability, s.args, along(s)) for s in steps]


@pytest.mark.parametrize("name", NAMES)
def test_the_plans_are_the_same_step_for_step(name):
    _, _, _, old, new = built(name)
    for capability, values, results in CASES[name]:
        found = outcome(new, capability, values, results)
        assert found == outcome(old, capability, values, results), (capability, values, results)
    assert any(outcome(new, *case)[0] == "plan" for case in CASES[name])


def test_the_cases_plan_what_the_tests_of_modplan_expect():
    """Beside the comparison: the plans are plans (the NoPlans are the cases the tests of modplan state as impossible)."""
    _, _, _, _, domain = built("filling")
    kind = {(c, v.get("FillVolume"), r): outcome(domain, c, v, r)[0] for c, v, r in CASES["filling"]}
    assert kind[("Filling", 2.0, ("FillWeight",))] == kind[("Filling", 15.0, ("FillWeight",))] == "plan"
    assert kind[("BottomUpFilling", 1.0, ("FillWeight",))] == "plan"
    assert kind[("BottomUpFilling", 0.25, ())] == "no plan"
    steps = outcome(domain, "Filling", {"FillVolume": 2.0}, ("FillWeight",))[1]
    assert [(c, a) for c, a, _ in steps] == [("Positioning", {"Position": 40.0}), ("Dosing", {"FlowRate": 1.0, "Volume": 2.0}),
                                               ("Referencing", {}), ("Weighing", {})]


@pytest.mark.parametrize("name", NAMES)
def test_the_state_a_plan_starts_from_and_ends_in_gives_the_same_steps(name):
    _, _, _, old, new = built(name)
    both = []
    for domain in (old, new):
        found = [(s.capability, s.args) for s in reach(domain, list(domain.rest.items()))]
        wrong = outcome(domain, "Filling", {"FillVolume": 2.0}, (), start={**domain.rest, CONTENT: 1.0}) if "Filling" in domain.capabilities else None
        both.append((found, wrong))
    assert both[0] == both[1]


@pytest.mark.parametrize("name", NAMES)
def test_the_capabilities_a_plan_exists_for_are_the_same(name):
    _, _, _, old, new = built(name)
    assert repr(discover(new, Vocabulary())) == repr(discover(old, Vocabulary()))


@pytest.mark.parametrize("name", NAMES)
def test_the_skills_of_the_module_pass_the_check_alike(name):
    _, old, new, domain_old, domain_new = built(name)
    for composite_name, composite in described(old[0], old[1:]).composites.items():
        found = [[f.step for f in check(domain, composite)] for domain in (domain_old, domain_new)]
        assert found[0] == found[1], composite_name
