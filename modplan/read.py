"""A module's planning domain from its AAS alone (ARSO 0.11; docs/skill-planning.md, sections 2 to 4).

- The Capability Descriptions: each component's, in its own AAS, holds in a set named after it the
  component's capabilities, the actions; the module's OfferedCapabilities hold the tasks. A
  capability is realized by the skill its CapabilityRealizedBy names. (A module's AAS written before
  the components had their own holds their sets itself; those are read as well.)
- A capability's properties: one with a Range is a parameter (its default the realizing skill's);
  one with a value is a named value; one a constraint is about is a state variable: the data point
  its SameProperty names, else the product's if its meaning is a property of the product, else the
  component's whose set holds it.
- Its constraints: the BasicConstraint of each PropertyConstraintContainer, about the property its
  ConstraintHasProperty names.
- The results a task can give: the vocabulary's properties of its class that say when they are
  known (a fill weight: once the container is weighed).
- The rest state: what the module's Reset procedure leaves (``Domain.after`` its steps).
- The same from the execution model instead (``read_execution``): Subjects, ConditionSet, EffectSet and Duration of each capability, as
  ``modplan.execution`` reads them; the properties, the vocabulary's results and the rest state are read as above.
- Time (vocabulary 0.10): a property whose meaning is the rate of another (a Speed, a FlowRate) makes
  the capability change the state of that meaning at that rate; a primitive's Completion ``After``
  (its skill in the component's Skills submodel) says how long it takes if no rate does.
"""
from __future__ import annotations

import re

from aas_model.constants import PRODUCT_ASSET_TYPE
from modsync.aasjson import at, children, skills_in, submodel
from modsync.described import described

from . import execution as model
from .domain import PRODUCT, Capability, Constraint, Domain, Parameter, Var
from .expression import Expression, Formula, formula, parse
from .vocabulary import Vocabulary, local

OFFERED_SET = "OfferedCapabilities"
RESET = "Resetting"


def _meaning(element: dict) -> str | None:
    ids = element.get("supplementalSemanticIds") or []
    return local(ids[0]["keys"][0]["value"]) if ids else None


def _value(element: dict):
    text, kind = element.get("value"), element.get("valueType")
    if text is None:
        return None
    if kind == "xs:boolean":
        return str(text).lower() == "true"
    return float(text) if kind in ("xs:double", "xs:float", "xs:decimal") else text


def read(module: dict, components: list[dict], vocabulary: Vocabulary | None = None) -> Domain:
    """The planning domain of the module ``module`` (its AAS, as JSON) with its ``components``."""
    voc = vocabulary or Vocabulary()
    spec = described(module, components)
    shell = module["assetAdministrationShells"][0]
    completions = {skill["idShort"]: after for env in components for skill in skills_in(submodel(env, "Skills"))
                   if (after := (at(skill, "Completion", "After") or {}).get("value")) not in (None, "")}
    caps = {}
    held = [s for env in (module, *components) for s in children(submodel(env, "CapabilityDescription"))]
    for capability_set in held:
        component = None if capability_set["idShort"] == OFFERED_SET else capability_set["idShort"]
        for container in children(capability_set):
            cap = _capability(container, component, spec, voc, completions)
            if cap is not None:
                caps[cap.name if cap.name not in caps else f"{component}{cap.name}"] = cap
    domain = Domain(shell["idShort"], caps)
    reset = spec.procedures.get(RESET, [])
    domain.rest = domain.after([(step.skill, {k: v for k, v in step.bind.items() if not isinstance(v, str)}) for step in reset])
    return domain


def _head(container: dict, component: str | None, spec) -> tuple[Capability, dict | None, dict[str, str], dict[str, tuple[dict, dict]]] | None:
    """What both forms say of a capability: its name, class and skill, its parameters (a Range: the realizing skill's default is the
    parameter's) and named values. Also its CapabilityRelations, the meaning of each property of its PropertySet, and the
    properties (the container and its value element), each by idShort."""
    element = at(container, "Capability")
    if element is None:
        return None
    relations = at(container, "CapabilityRelations")
    realized = [r for r in children(relations) if r.get("modelType") == "RelationshipElement" and r.get("second")]
    skill = realized[0]["second"]["keys"][-1]["value"] if realized else None
    cap = Capability(name=container["idShort"], meaning=_meaning(element) or container["idShort"], skill=skill, component=component)
    decl = spec.skills.get(skill) or spec.composites.get(skill)
    meanings: dict[str, str] = {}
    properties: dict[str, tuple[dict, dict]] = {}
    for prop in children(at(container, "PropertySet")):
        value = at(prop, "Value")
        if value is None:
            continue
        name, meant = prop["idShort"], _meaning(value) or prop["idShort"]
        meanings[name] = meant
        properties[name] = (prop, value)
        if value.get("modelType") == "Range":
            given = decl.parameters.get(name) if decl is not None else None
            cap.parameters[name] = Parameter(_number(value.get("min")), _number(value.get("max")),
                                             given.default if given is not None else None)
        elif value.get("value") is not None:
            cap.values[name] = _value(value)
    return cap, relations, meanings, properties


def _capability(container: dict, component: str | None, spec, voc: Vocabulary, completions: dict | None = None) -> Capability | None:
    """The capability of a container whose conditions and effects are the constraints of its ConstraintSet (the old form)."""
    head = _head(container, component, spec)
    if head is None:
        return None
    cap, relations, meanings, properties = head
    skill = cap.skill
    variables: dict[str, Var] = {}
    for name, (prop, value) in properties.items():
        meant = meanings[name]
        same = at(prop, "SameProperty")
        if same is not None and same.get("second"):
            variables[name] = Var(same["second"]["keys"][-1]["value"], meant)
        elif voc.product(meant):
            variables[name] = Var(PRODUCT, meant)
        else:
            variables[name] = Var(component or "module", meant)
    for record in children(at(relations, "ConstraintSet")):
        kind = at(record, "PropertyConditionalType")["value"]
        expression = parse(at(record, "BasicConstraint")["value"], kind)
        (has,) = [r for r in children(at(record, "ConstraintPropertyRelations")) if r.get("second")]
        prop = has["second"]["keys"][-2]["value"]
        constraint = Constraint(prop, variables[prop], expression)
        {"Pre": cap.pre, "Post": cap.post, "Invariant": cap.invariant}[kind].append(constraint)
    cap.variables = {**variables, **cap.variables}
    # A property that is the rate of what a Post changes: the change goes at that rate (0.10).
    for name, meant in meanings.items():
        of = voc.rate_of(meant)
        for c in cap.post:
            if of is not None and c.var.meaning == of and c.expression.op in ("=", "+=", "given"):
                cap.rates[c.var] = name
    if skill is not None and (completions or {}).get(skill) is not None:
        cap.after = parse(f"= {completions[skill]}")
    if component is None:
        for result in voc.results(cap.meaning):
            cap.results[result] = [Constraint(s.prop, Var(PRODUCT if voc.product(s.prop) else "module", s.prop), s.expression)
                                   for s in voc.needs(result)]
    return cap


def _number(text) -> float | None:
    return None if text is None else float(text)


# The execution model ---------------------------------------------------------------------------

class Unsupported(ValueError):
    """The execution model says something a module's planning domain cannot hold (docs/execution-model.md, section 12: what modplan
    supports); the capability is refused by name, with the feature."""


CONDITIONS = {"equal": "=", "notEqual": "!=", "less": "<", "lessOrEqual": "<=", "greater": ">", "greaterOrEqual": ">="}


def read_execution(module: dict, components: list[dict], vocabulary: Vocabulary | None = None) -> Domain:
    """The planning domain of the module ``module`` (its AAS, as JSON) with its ``components``, from what the execution model says of
    each capability (the reading of ``modplan.execution``) where ``read`` takes the constraints of its ConstraintSet.

    A state variable (``Var``) is a property (its meaning) of a subject: the name of the AAS of a fixed subject, or ``PRODUCT`` for
    the products' unit. A capability's ``variables`` name the states its formulas read (the alias of a Use) and its constraints are
    about (the meaning). Unsupported: ValueError naming the feature."""
    voc = vocabulary or Vocabulary()
    spec = described(module, components)
    shell = module["assetAdministrationShells"][0]
    merged = {key: [item for env in (module, *components) for item in env.get(key, [])]
              for key in ("assetAdministrationShells", "submodels")}
    reading = model.read(merged)
    names = {a["id"]: a["idShort"] for a in merged["assetAdministrationShells"]}
    caps = {}
    for env in (module, *components):
        aas = env["assetAdministrationShells"][0]["id"]
        description = submodel(env, "CapabilityDescription")
        for capability_set in children(description):
            component = None if capability_set["idShort"] == OFFERED_SET else capability_set["idShort"]
            for container in children(capability_set):
                path = f"{description['idShort']}/{capability_set['idShort']}/{container['idShort']}"
                cap = _capability_of_execution(container, component, spec, voc, _found(reading, path, aas), names)
                if cap is not None:
                    caps[cap.name if cap.name not in caps else f"{component}{cap.name}"] = cap
    domain = Domain(shell["idShort"], caps)
    reset = spec.procedures.get(RESET, [])
    domain.rest = domain.after([(step.skill, {k: v for k, v in step.bind.items() if not isinstance(v, str)}) for step in reset])
    return domain


def _found(reading: model.Reading, path: str, aas: str):
    return next((c for c in reading.capabilities if c.path == path and c.aas == aas), None)


def _capability_of_execution(container: dict, component: str | None, spec, voc: Vocabulary, found, names: dict[str, str]) -> Capability | None:
    head = _head(container, component, spec)
    if head is None:
        return None
    cap, _, meanings, _ = head
    if found is not None:
        _Terms(cap, found, names, meanings, voc).read()
    if component is None:
        for result in voc.results(cap.meaning):
            cap.results[result] = [Constraint(s.prop, Var(PRODUCT if voc.product(s.prop) else "module", s.prop), s.expression)
                                   for s in voc.needs(result)]
    return cap


class _Terms:
    """The conditions, effects and duration of one capability, read into the constraints of its domain."""

    def __init__(self, cap: Capability, found, names: dict[str, str], meanings: dict[str, str], voc: Vocabulary):
        self.cap, self.found, self.meanings, self.voc, self.names = cap, found, meanings, voc, names
        self.properties = f"{found.path}/PropertySet/"
        self.subjects = [self.subject(s) for s in found.subjects]
        self.rated: list[tuple[Constraint, str]] = []              # what an End effect's Rate says: the Post and the rate's name

    def refuse(self, feature: str, where: str = "") -> Unsupported:
        return Unsupported(f"{self.cap.name}: modplan does not plan with {feature}{' (' + where + ')' if where else ''}")

    def subject(self, subject) -> str | None:
        """The subject of a variable: the AAS of a fixed subject (by idShort), PRODUCT for the unit; None for what modplan has no
        variables of."""
        if isinstance(subject, model.FixedSubject):
            return self.names.get(subject.target, subject.target)
        if isinstance(subject, model.RuntimeSubject) and subject.stands_for == PRODUCT_ASSET_TYPE:
            return PRODUCT
        return None

    def var(self, reference, meant: str, where: str) -> Var:
        if not isinstance(reference, model.SubjectRef):
            raise self.refuse("a quantifier's variable", where)
        subject = self.subjects[reference.index] if reference.index < len(self.subjects) else None
        if subject is None:
            raise self.refuse(f"the subject {self.found.subjects[reference.index].name!r}, which is not a unit of the product or an AAS", where)
        return Var(subject, local(meant))

    def read(self) -> None:
        found, cap = self.found, self.cap
        for phase, kind in (("pre", "Pre"), ("invariant", "Invariant")):
            for term in self.atoms(getattr(found, phase)):
                self.condition(term, kind)
        for term in self.atoms(found.during):                    # a definition that holds while it runs: an Invariant "="
            self.effect(term, "Invariant", during=True)
        for term in self.atoms(found.end):
            self.effect(term, "Post")
        for phase, feature in ((found.post, "a Post condition (checked, not caused)"), (found.start, "a Start effect")):
            if phase:
                raise self.refuse(feature)
        if found.duration is not None:
            cap.after = self.expression("=", found.duration, "Duration")
        # A property that is the rate of what a Post changes: the change goes at that rate (0.10), as the End effect's Rate says.
        for c, rate in self.rated:
            cap.rates[c.var] = rate

    def atoms(self, terms):
        """The atoms of a phase: a conjunction is its terms."""
        for term in terms:
            if isinstance(term, model.And):
                yield from self.atoms(term.terms)
            elif isinstance(term, (model.Condition, model.Effect)):
                yield term
            else:
                raise self.refuse({model.Or: "a disjunction", model.Not: "a negation", model.Exists: "a quantifier",
                                   model.ForAll: "a quantifier", model.When: "a conditional effect",
                                   model.OneOf: "a non-deterministic outcome"}[type(term)], term.path)

    def state(self, alias: str, var: Var) -> str:
        """The name of a state in this capability's formulas: the alias, unless that is taken by another."""
        taken = lambda n: n in self.cap.variables and self.cap.variables[n] != var or n in self.meanings    # noqa: E731
        name, n = alias, 2
        while taken(name):
            name, n = f"{alias}_{n}", n + 1
        self.cap.variables[name] = var
        return name

    def element(self, operand, where: str) -> str:
        """The name of a property of the capability or of a parameter of the realizing skill that an operand refers to."""
        if isinstance(operand, model.Parameter):
            return operand.variable
        if isinstance(operand, model.Element):
            parts = operand.path.split("/")
            if operand.path.startswith(self.properties) and len(parts) == len(self.properties.split("/")) + 1 and parts[-1] == "Value":
                return parts[-2]
        raise self.refuse("an operand that is not a property of the capability or a parameter of its skill", where)

    def expression(self, op: str, operand, where: str) -> Expression:
        """The expression of an operator and an operand."""
        if isinstance(operand, model.Literal):
            if operand.type not in ("number", "boolean"):
                raise self.refuse(f"a {operand.type} operand", where)
            return Expression(op, float(operand.value) if operand.type == "number" else operand.value)
        if isinstance(operand, model.FormulaOperand):
            return Expression(op, self.formula(operand, where))
        return Expression(op, self.element(operand, where))

    def formula(self, operand: model.FormulaOperand, where: str):
        """A formula over the names its Uses give: a property, a parameter, the state of a subject; a formula of one name (or a
        number) is that name (or number), as an expression of the old form says it."""
        if operand.ast is None:
            raise self.refuse("a formula that does not parse", where)
        renamed = {}
        for alias, use in operand.uses:
            if isinstance(use, model.State):
                renamed[alias] = self.state(alias, self.var(use.subject, use.property, f"{where}/Uses/{alias}"))
            elif use is None:
                raise self.refuse("a use that does not resolve", f"{where}/Uses/{alias}")
            else:
                renamed[alias] = self.element(use, f"{where}/Uses/{alias}")
        parsed = formula(operand.text)
        if any(alias != name for alias, name in renamed.items()):
            parsed = Formula(_rename(parsed.tree, renamed), re.sub(r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*",
                                                                   lambda m: renamed.get(m.group(0), m.group(0)), parsed.text))
        if isinstance(parsed.tree, (str, float)):
            return parsed.tree
        return parsed

    def condition(self, term: model.Condition, kind: str) -> None:
        var = self.var(term.subject, term.property, term.path)
        if term.op == "unchanged":
            e = Expression("unchanged")
        elif term.op in CONDITIONS:
            e = self.expression(CONDITIONS[term.op], term.operand, term.path)
        else:
            raise self.refuse(f"the operator {term.op}", term.path)
        self.add(kind, var, e)

    def effect(self, term: model.Effect, kind: str, during: bool = False) -> None:
        var = self.var(term.subject, term.property, term.path)
        if term.op == "assign":
            e = self.expression("=", term.operand, term.path)
            given = isinstance(term.operand, model.Parameter) and term.operand.operation == f"Skills/{self.cap.skill}/Start/Start"
            if given and not during:                    # the property ends at the value the capability was given
                e = Expression("given")
            self.add(kind, var, e, prop=term.operand.variable if given and not during else None)
        elif term.op == "increase" and not during:
            self.add(kind, var, self.expression("+=", term.operand, term.path))
        elif term.op == "measured" and not during:
            self.add(kind, var, Expression("measured"))
        else:
            raise self.refuse(f"the effect {term.op}", term.path)
        if term.rate is not None and kind == "Post":
            self.rated.append((self.cap.post[-1], self.element(term.rate, f"{term.path}/Rate")))

    def add(self, kind: str, var: Var, e: Expression, prop: str | None = None) -> None:
        """A constraint about ``var``, named for what it is about: the parameter it is given, else the state's meaning."""
        name = prop or self.state(var.meaning, var)
        self.cap.variables.setdefault(name, var)
        {"Pre": self.cap.pre, "Post": self.cap.post, "Invariant": self.cap.invariant}[kind].append(Constraint(name, var, e))


def _rename(tree, names: dict[str, str]):
    if isinstance(tree, str):
        return names.get(tree, tree)
    if isinstance(tree, float):
        return tree
    op, *parts = tree
    return (op, *[_rename(p, names) for p in parts])
