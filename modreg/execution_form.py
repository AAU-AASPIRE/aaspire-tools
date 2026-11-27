"""A capability's conditions and effects in the execution model (docs/execution-model.md in aas-model), written from the
constraints the module spec and the vocabulary state for it (Phase 2 of the execution plan).

The old form is a ConstraintSet of BasicConstraint strings (`modreg.capabilities.Container`): each a Pre, Post or Invariant
about a property of the capability, a state of the module or of the product being a property too. The new form says the same in
the capability's CapabilityRelations:

- a Pre or an Invariant comparison: a Condition of ConditionSet/Pre or /Invariant with the same operator; an Invariant
  `unchanged`: a Condition `unchanged`;
- a Post `= v`: an Effect `assign` in EffectSet/End; `+= x`: `increase`; `measured`: `measured`; `given`: `assign` of the value of
  the realizing Operation's parameter;
- an Invariant `= formula` that no Post sets (a defined quantity: OutletImmersion): an Effect `assign` with the Formula in
  EffectSet/During;
- a property that is the rate of what an End effect changes (lab:rateOf): the Rate of that effect;
- a primitive's Completion After: the Duration, a Value or a Formula over the skill's parameters.

Who a term is about is a **subject** of the capability (its Subjects): the component's own AAS for its state, another
component's AAS for the state of that one, the products' asset type for a property of the unit being processed. A name of the
old form that is a state (`LinearAxisPosition`, `ContentVolume`) is a (subject, meaning) of the new; a formula keeps its
text, such a name becoming the alias of a Use that reads the subject's property. Every state of a fixed subject that a term
reads or changes is declared in that AAS's Variables/Knowledge (`Declarations`) as an asserted variable: whether the old
SameProperty link to a data point becomes an AIMC sink into the variable (observed) is for later. What stays in the
ConstraintSet is what is for matching: a constraint about a property that is no state (rule `constraint.state`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from aas_model.submodel_templates.execution import (
    ConditionOperator, ConditionSet, DuringTerms, EffectOperator, EffectSet, EndTerms, ExecutionDuration, ExecutionSubject,
    FormulaUse, FormulaUses, InvariantTerms, PreTerms, Rate, StateCondition, StateEffect, Subjects, TermFormula,
    TermSubject, TermValue, TermValueReference,
)
from aas_model.submodel_templates.knowledge import Knowledge, StateVariable, StateVariableCardinality, StateVariableValueType
from aas_model.constants import PRODUCT_ASSET_TYPE
from aas_pydantic import ExternalReference, Key, ModelReference

from modgen.spec import Skill
from modplan.execution import Vocabulary as StateVocabulary
from modplan.expression import Formula, parse

from .profile import SKILLS, SMC, component_identity, meaning, text

SELF, UNIT = "self", "unit"        # the subjects that are no component: the capability's own resource, the unit of the product
CONDITIONS = {"=": "equal", "!=": "notEqual", "<": "less", "<=": "lessOrEqual", ">": "greater", ">=": "greaterOrEqual"}


def words(name: str) -> str:
    """`LinearAxis` as `linear axis`."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()


def local(iri: str) -> str:
    return iri.rstrip("/").rsplit("/", 1)[-1]


def state_name(about: str) -> str:
    """The property a possibly dotted name is about (`LinearAxis.Position`: Position)."""
    return about.rpartition(".")[2]


def property_meaning(container, name: str) -> str:
    """The meaning (local name) of a capability property: its value's supplemental semantic id, else its name."""
    element = next(iter((container.PropertyProperty or container.PropertyRange or container.PropertySubmodelList).values()), None)
    return local(element.supplemental_semantic_ids[0]) if element is not None and element.supplemental_semantic_ids else name


def posted(spec, classes) -> frozenset[str]:
    """The properties (by meaning) that a Post of some capability of the module sets: what the vocabulary says of the capability
    `classes` and the spec states of its skills. An Invariant `= formula` about any other defines it."""
    from .capabilities import vocabulary
    found = {s.prop for cls in classes for s in vocabulary().constraints(cls) if s.kind == "Post"}
    for decl in spec.skills.values():
        found |= {state_name(about) for kind, about, _ in (decl.capability.conditions() if decl.capability else ()) if kind == "Post"}
    return frozenset(found)


@dataclass
class Declarations:
    """The state variables the terms of a module's capabilities read or change, by the component whose AAS declares them (None:
    the module's): the meanings (local names) in the order the terms found them."""
    found: dict[str | None, dict[str, None]] = field(default_factory=dict)

    def add(self, item: str | None, prop: str) -> None:
        self.found.setdefault(item, {})[prop] = None

    def knowledge(self, item: str | None) -> Knowledge | None:
        """The Knowledge that declares `item`'s variables (an asserted variable each), with the value type and cardinality the
        vocabulary gives its meaning (what it does not give is left out); None if there are none."""
        if not self.found.get(item):
            return None
        vocabulary = StateVocabulary.default().properties
        variables = {}
        for prop in self.found[item]:
            known = vocabulary.get(meaning(prop))
            variable = StateVariable.own(meaning(prop))
            if known is not None and known.value_type and variable.ValueType is None:
                variable.ValueType = StateVariableValueType(value=known.value_type)
            if known is not None and known.cardinality and variable.Cardinality is None:
                variable.Cardinality = StateVariableCardinality(value=known.cardinality)
            variables[prop] = variable
        return Knowledge(variable=variables)


@dataclass
class Execution:
    """What writing the new form needs beside the module spec: the properties some Post sets, and the declarations the terms
    make (filled as they are written)."""
    changed: frozenset[str] = frozenset()
    module: str = ""                                          # the id of the module's AAS
    declared: Declarations = field(default_factory=Declarations)


def skill_start(skill: str) -> tuple[Key, ...]:
    """The keys down to the Start Operation of a skill, in the AAS that holds it."""
    return (Key(type_="Submodel", value=SKILLS), Key(type_=SMC, value=skill), Key(type_=SMC, value="Start"),
            Key(type_="Operation", value="Start"))


class Form:
    """The execution model of one capability, built as its constraints are stated (`constrain`) and written out (`finish`).
    `own`: the item of the component whose capability it is (None: the module's); `skill` and `decl`: what realizes it."""

    def __init__(self, box, spec, own: str | None, skill: str | None, decl: Skill | None, execution: Execution):
        self.box, self.spec, self.own, self.skill, self.decl, self.execution = box, spec, own, skill, decl, execution
        self.states: dict[str, tuple[str, str]] = {}          # a name of the old form that is a state -> (subject, meaning)
        self.subjects: list[ExecutionSubject] = []
        self.index: dict[str, int] = {}
        self.pre: list = []
        self.invariant: list = []
        self.during: list = []
        self.end: list = []
        self.subject(SELF)                                    # the capability's own resource is always the first

    # -- subjects ---------------------------------------------------------------------------

    def subject(self, key: str) -> int:
        """The index of the subject `key` (SELF, UNIT, or the item of a component), adding it if it is new."""
        if key == self.own:
            key = SELF
        if key not in self.index:
            # Always the base class and its value stated (not SelfSubject's default `{aas_id}`, nor UnitSubject's): a profile keeps
            # only what differs from the class, and could not tell the subjects' classes apart.
            if key == SELF:
                aas = component_identity(self.spec, self.own)[1] if self.own else self.execution.module
                made = ExecutionSubject(display_name={"en": f"This {words(self.own) if self.own else 'module'}"},
                                        value=ModelReference(key=(Key(type_="AssetAdministrationShell", value=aas),)),
                                        description="The resource owning the capability.")
            elif key == UNIT:
                made = ExecutionSubject(display_name={"en": "Unit of the product"}, description="A unit of a product.",
                                        value=ExternalReference(key=(Key(type_="GlobalReference", value=PRODUCT_ASSET_TYPE),)))
            else:
                if key not in [i for i in self.spec.equipment if any(d.equipment == i for d in self.spec.skills.values())]:
                    raise ValueError(f"{self.box.name}: {key} is no component of the module (no skill moves it), so it has no AAS to be a subject")
                made = ExecutionSubject(display_name={"en": words(key).capitalize()}, value=ModelReference(key=(
                    Key(type_="AssetAdministrationShell", value=component_identity(self.spec, key)[1]),)))
            self.index[key] = len(self.subjects)
            self.subjects.append(made)
        return self.index[key]

    def subject_ref(self, key: str) -> ModelReference:
        return self.box.at((SMC, "CapabilityRelations"), ("SubmodelElementList", "Subjects"),
                           ("ReferenceElement", str(self.subject(key))))

    def state(self, name: str, subject: str, meant: str | None) -> None:
        """The name `name` of the old form is the property `meant` of `subject`."""
        self.states[name] = (SELF if subject == self.own else subject, meant or name)

    def declare(self, key: str, prop: str) -> None:
        """The state `prop` of a fixed subject is declared in its AAS (a unit's are its product type's)."""
        if key != UNIT:
            self.execution.declared.add(self.own if key in (SELF, self.own) else key, prop)

    # -- references -------------------------------------------------------------------------

    def operation_variable(self, name: str) -> ModelReference | None:
        """The input of the realizing skill's Operation that is the parameter `name`, if it has one."""
        if self.skill is None or self.decl is None or name not in self.decl.parameters:
            return None
        return ModelReference(key=(*skill_start(self.skill), Key(type_="Property", value=name)))

    def reference(self, name: str) -> ModelReference | None:
        """What a capability property stands for: the realizing skill's parameter, else the property's value."""
        if name not in self.box.properties:
            return None
        return self.operation_variable(name) or self.box.at((SMC, "PropertySet"), (SMC, name), self.box.value_of(name))

    def use(self, name: str) -> FormulaUse:
        """The Use for a name of a formula: a property of the capability, or a property of a subject."""
        if name in self.box.properties:
            return FormulaUse(value=self.reference(name))
        if name in self.states:
            subject, meant = self.states[name]
            self.declare(subject, meant)
            return FormulaUse(value=self.subject_ref(subject), supplemental_semantic_ids=[meaning(meant)])
        raise ValueError(f"{self.box.name}: {name} is neither a property of the capability nor a state it is about")

    def operand(self, e, duration: bool = False) -> dict:
        """The operand of an atom for the expression `e`: a literal, a reference, or a formula with its Uses (a Duration has no
        reference: a name is a formula of its own)."""
        o = e.operand
        if isinstance(o, bool):
            return {"Value": TermValue(value=text(o), value_type="xs:boolean")}
        if isinstance(o, (int, float)):
            return {"Value": TermValue(value=text(float(o)), value_type="xs:double")}
        if isinstance(o, str) and o in self.box.properties and not duration:
            return {"ValueReference": TermValueReference(value=self.reference(o))}
        if isinstance(o, str):                                # a name that is a state, or a Duration's: a formula of its own
            return {"Formula": TermFormula(value=o), "Uses": FormulaUses(use={o: self.use(o)})}
        assert isinstance(o, Formula)
        return {"Formula": TermFormula(value=o.text), "Uses": FormulaUses(use={name: self.use(name) for name in o.names})}

    # -- constraints ------------------------------------------------------------------------

    def constrain(self, kind: str, prop: str, expression: str) -> None:
        """The constraint `expression` of `kind` about `prop`, a state, as a term."""
        e = parse(expression, kind)
        if prop not in self.states:
            raise ValueError(f"{self.box.name}: {kind} {prop} {expression}: {prop} is no state of the capability")
        if kind != "Post" and prop in self.box.properties:
            raise ValueError(f"{self.box.name}: {kind} {prop} {expression}: {prop} is a parameter and a state; name the state "
                             f"through its item (<Item>.{prop}) to say which is meant")
        subject, meant = self.states[prop]
        self.declare(subject, meant)
        said = str(e) if len(str(e)) <= 60 else f"{e.op} a formula"              # a display name is at most 128 characters
        atom = dict(display_name={"en": f"{meant} {said}"}, supplemental_semantic_ids=[meaning(meant)],
                    Subject=TermSubject(value=self.subject_ref(subject)))
        if kind == "Post":
            self.end.append(self.effect(atom, prop, e))
        elif e.op == "unchanged":
            self.invariant.append(StateCondition(**atom, Operator=ConditionOperator(value="unchanged")))
        elif kind == "Invariant" and e.op == "=" and isinstance(e.operand, Formula) and meant not in self.execution.changed:
            self.during.append(StateEffect(**atom, Operator=EffectOperator(value="assign"), **self.operand(e)))
        else:
            condition = StateCondition(**atom, Operator=ConditionOperator(value=CONDITIONS[e.op]), **self.operand(e))
            (self.pre if kind == "Pre" else self.invariant).append(condition)

    def effect(self, atom: dict, prop: str, e) -> StateEffect:
        if e.op == "given":
            reference = self.operation_variable(prop)
            if reference is None:
                raise ValueError(f"{self.box.name}: Post {prop} given: {self.skill} has no parameter {prop}")
            return StateEffect(**atom, Operator=EffectOperator(value="assign"), ValueReference=TermValueReference(value=reference))
        if e.op == "measured":
            return StateEffect(**atom, Operator=EffectOperator(value="measured"))
        return StateEffect(**atom, Operator=EffectOperator(value="assign" if e.op == "=" else "increase"), **self.operand(e))

    # -- what the capability adds up to -----------------------------------------------------

    def rates(self) -> None:
        """A property that is the rate of what an End effect changes (a Speed of Position, a FlowRate of ContentVolume, vocabulary
        0.10) is the Rate of that effect."""
        from .capabilities import vocabulary
        for name, container in self.box.properties.items():
            of = vocabulary().rate_of(property_meaning(container, name))
            for effect in self.end:
                if of is not None and effect.supplemental_semantic_ids[0] == meaning(of) and effect.Operator.value in ("assign", "increase"):
                    effect.Rate = Rate(value=self.reference(name))

    def duration(self) -> ExecutionDuration | None:
        """A primitive's Completion After: a number of seconds, or a formula over its parameters."""
        after = self.decl.after if self.decl is not None and self.decl.postcondition is None else None
        if after is None or self.own is None:
            return None
        e = parse(f"= {text(after)}")
        if isinstance(e.operand, float):
            return ExecutionDuration(Value=TermValue(value=text(e.operand), value_type="xs:double"))
        return ExecutionDuration(**self.operand(e, duration=True))

    def finish(self) -> dict:
        """The Subjects, ConditionSet, EffectSet and Duration of the capability, as fields of its CapabilityRelations (none for
        what it does not state)."""
        duration = self.duration()
        if not (self.pre or self.invariant or self.during or self.end or duration is not None):
            return {}
        self.rates()

        def terms(held, kind):
            return kind(term={f"Term_{i:02d}": term for i, term in enumerate(held)}) if held else None
        found = {"Subjects": Subjects(value=self.subjects)}
        if self.pre or self.invariant:
            found["ConditionSet"] = ConditionSet(Pre=terms(self.pre, PreTerms), Invariant=terms(self.invariant, InvariantTerms))
        if self.during or self.end:
            found["EffectSet"] = EffectSet(During=terms(self.during, DuringTerms), End=terms(self.end, EndTerms))
        if duration is not None:
            found["Duration"] = duration
        return found
