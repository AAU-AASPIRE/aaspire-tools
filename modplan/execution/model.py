"""What the reader makes of an AAS environment: frozen dataclasses for the capabilities, goals and knowledge of the execution
model (``docs/execution-model.md``), and the findings where the model is not as it says.

Every class that section 15 (the canonical reading) states has a ``to_canonical()`` that gives exactly that JSON: IRIs
written with the prefixes ``lab:``, ``exec:`` and ``fixture:``, terms in idShort order. What the validator needs and the
canonical reading leaves out (the ``path`` of every element, RealizedBy and GeneralizedBy, the Translation, the property
set, the number of mappings that sink into a variable) is in the fields beside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

from aas_model.constants import BASE_URL, LAB

FIXTURE_NAMESPACE = "urn:fixture:"
_EXEC = f"{BASE_URL}/execution/"
_VERSION = re.compile(r"/\d+/\d+$")

# The severities of the codes the reader reports (section 11); the validator adds the others.
SEVERITIES = {
    "structure.kind": "error", "structure.meaning": "error", "structure.operand": "error", "structure.rate": "error",
    "structure.not-arity": "error", "structure.placement": "error", "structure.subject-name": "error",
    "reference.unresolved": "error", "reference.subject": "error", "reference.scope": "error",
    "formula.syntax": "error", "formula.alias": "error", "formula.unused": "warning",
}


def compact(iri: str) -> str:
    """``iri`` with the prefix section 15 gives it: ``lab:``, ``fixture:``, or ``exec:`` (without the version suffix)."""
    if iri.startswith(LAB):
        return "lab:" + iri[len(LAB):]
    if iri.startswith(FIXTURE_NAMESPACE):
        return "fixture:" + iri[len(FIXTURE_NAMESPACE):]
    if iri.startswith(_EXEC):
        return "exec:" + _VERSION.sub("", iri[len(_EXEC):])
    return iri


def _compact(iri: Optional[str]) -> Optional[str]:
    return None if iri is None else compact(iri)


# ── Findings ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Finding:
    """A rule of section 11 that the model breaks, at the idShort path (from the submodel) of the smallest element the
    rule is about."""
    code: str
    path: str
    severity: str = ""

    def __post_init__(self):
        if not self.severity:
            object.__setattr__(self, "severity", SEVERITIES[self.code])

    def to_canonical(self) -> dict:
        return {"code": self.code, "severity": self.severity, "path": self.path}


# ── Subjects ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FixedSubject:
    """A particular asset or place: the identity of the AAS, or of the element (``<submodel id>#<idShortPath>``)."""
    name: str
    target: str

    def to_canonical(self) -> dict:
        return {"name": self.name, "fixed": self.target}


@dataclass(frozen=True)
class RuntimeSubject:
    """What the planner chooses and the behaviour tree binds: it stands for an asset type, a place kind."""
    name: str
    stands_for: str

    def to_canonical(self) -> dict:
        return {"name": self.name, "standsFor": compact(self.stands_for)}


@dataclass(frozen=True)
class RequirementSubject:
    """What the unit's product requires: the element of its product type with this meaning."""
    name: str
    meaning: str

    def to_canonical(self) -> dict:
        return {"name": self.name, "requirement": compact(self.meaning)}


Subject = Union[FixedSubject, RuntimeSubject, RequirementSubject]


# ── Operands ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Literal:
    """A value: ``type`` is number, boolean, string or iri; ``xsd`` the valueType the element declares."""
    type: str
    value: Union[float, bool, str]
    xsd: str = ""

    def to_canonical(self) -> dict:
        return {self.type: compact(self.value) if self.type == "iri" else self.value}


@dataclass(frozen=True)
class SubjectRef:
    """An item of the capability's (or goal's) Subjects, by index."""
    index: int

    def to_canonical(self) -> dict:
        return {"subject": self.index}


@dataclass(frozen=True)
class VariableRef:
    """The variable of an enclosing quantifier, by the number the reader gave it (``v0``, ``v1``)."""
    id: str

    def to_canonical(self) -> dict:
        return {"variable": self.id}


@dataclass(frozen=True)
class Parameter:
    """An Operation variable: ``operation`` is the idShort path of the Operation, ``variable`` the variable's idShort."""
    operation: str
    variable: str

    def to_canonical(self) -> dict:
        return {"parameter": {"operation": self.operation, "variable": self.variable}}


@dataclass(frozen=True)
class Element:
    """A constant read with the model: an idShort path from the submodel's own, or ``<submodel id>#<idShortPath>`` where the
    element is in another AAS."""
    path: str

    def to_canonical(self) -> dict:
        return {"element": self.path}


@dataclass(frozen=True)
class State:
    """The property (a meaning) of a subject, read in a formula's Uses."""
    subject: Union[SubjectRef, VariableRef]
    property: str

    def to_canonical(self) -> dict:
        reference = self.subject.to_canonical()
        key, value = next(iter(reference.items()))
        return {"state": {key: value, "property": compact(self.property)}}


@dataclass(frozen=True)
class FormulaOperand:
    """A formula (``text``, and ``ast`` where it parses) and the operand each of its aliases stands for."""
    text: str
    ast: Optional[object]                                   # a formula.Node
    uses: tuple[tuple[str, Optional["Operand"]], ...]

    def to_canonical(self) -> dict:
        return {"formula": None if self.ast is None else self.ast.to_canonical(),
                "uses": {alias: None if use is None else use.to_canonical() for alias, use in self.uses}}


Operand = Union[Literal, SubjectRef, VariableRef, Parameter, Element, State, FormulaOperand]
Reference = Union[SubjectRef, VariableRef]


# ── Terms ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Condition:
    """One property of one subject compared with an operand (none for ``unchanged``)."""
    path: str
    property: Optional[str]
    subject: Optional[Reference]
    op: Optional[str]
    operand: Optional[Operand] = None

    def to_canonical(self) -> dict:
        return {"condition": _atom(self)}


@dataclass(frozen=True)
class Effect:
    """One property of one subject changed; an End effect may run at a ``rate``."""
    path: str
    property: Optional[str]
    subject: Optional[Reference]
    op: Optional[str]
    operand: Optional[Operand] = None
    rate: Optional[Operand] = None

    def to_canonical(self) -> dict:
        atom = _atom(self)
        if self.rate is not None:
            atom["rate"] = self.rate.to_canonical()
        return {"effect": atom}


def _atom(term) -> dict:
    atom = {"subject": None if term.subject is None else term.subject.to_canonical(),
            "property": _compact(term.property), "op": term.op}
    if term.operand is not None:
        atom["operand"] = term.operand.to_canonical()
    return atom


@dataclass(frozen=True)
class And:
    terms: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"and": [t.to_canonical() for t in self.terms]}


@dataclass(frozen=True)
class Or:
    terms: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"or": [t.to_canonical() for t in self.terms]}


@dataclass(frozen=True)
class Not:
    """The negation of ``term``; where the model gives it more or fewer than one (a finding), the implicit And of them."""
    term: "Term"
    path: str = ""

    def to_canonical(self) -> dict:
        return {"not": self.term.to_canonical()}


@dataclass(frozen=True)
class Variable:
    """What a quantifier ranges over: its number in the capability, its name, and what it stands for."""
    id: str
    name: str
    stands_for: Optional[str]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"id": self.id, "name": self.name, "standsFor": _compact(self.stands_for)}


@dataclass(frozen=True)
class Exists:
    variable: Optional[Variable]
    body: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"exists": _quantified(self)}


@dataclass(frozen=True)
class ForAll:
    variable: Optional[Variable]
    body: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"forall": _quantified(self)}


def _quantified(term) -> dict:
    return {"variable": None if term.variable is None else term.variable.to_canonical(),
            "body": [t.to_canonical() for t in term.body]}


@dataclass(frozen=True)
class When:
    """The effects of ``then`` apply where the conditions of ``condition`` hold before the phase."""
    condition: tuple["Term", ...]
    then: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"when": {"if": [t.to_canonical() for t in self.condition], "then": [t.to_canonical() for t in self.then]}}


@dataclass(frozen=True)
class Outcome:
    id: str                                                 # the idShort the Translation's Reply names
    meaning: Optional[str]
    effects: tuple["Term", ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"id": self.id, "meaning": _compact(self.meaning), "effects": [t.to_canonical() for t in self.effects]}


@dataclass(frozen=True)
class OneOf:
    meaning: Optional[str]                                  # what is decided
    outcomes: tuple[Outcome, ...]
    path: str = ""

    def to_canonical(self) -> dict:
        return {"oneOf": {"meaning": _compact(self.meaning), "outcomes": [o.to_canonical() for o in self.outcomes]}}


Term = Union[Condition, Effect, And, Or, Not, Exists, ForAll, When, OneOf]


# ── Capabilities, goals, knowledge ───────────────────────────────────────────

@dataclass(frozen=True)
class ModelReference:
    """A reference of the model as it is (``keys`` are (type, value)), and the identity of what it resolves to, if it does."""
    keys: tuple[tuple[str, str], ...]
    target: Optional[str] = None


@dataclass(frozen=True)
class CapabilityProperty:
    """A property of the capability's PropertySet: its idShort, its meaning and, where it is a Property, its value."""
    path: str
    id_short: str
    meaning: Optional[str]
    value: Optional[Literal] = None


@dataclass(frozen=True)
class Translation:
    """The Lua of the behaviour tree's Translation, as text."""
    call: Optional[str]
    reply: Optional[str]
    path: str = ""


@dataclass(frozen=True)
class Capability:
    """A capability: who takes part, what must hold, what changes. ``path`` is its idShort path from the submodel; the
    fields after ``features`` are not in the canonical reading."""
    aas: str
    path: str
    meaning: Optional[str]
    subjects: tuple[Subject, ...]
    pre: tuple[Term, ...] = ()
    invariant: tuple[Term, ...] = ()
    post: tuple[Term, ...] = ()
    start: tuple[Term, ...] = ()
    during: tuple[Term, ...] = ()
    end: tuple[Term, ...] = ()
    duration: Optional[Operand] = None
    features: tuple[str, ...] = ()
    realized_by: tuple[ModelReference, ...] = ()
    generalized_by: tuple[ModelReference, ...] = ()
    translation: Optional[Translation] = None
    properties: tuple[CapabilityProperty, ...] = ()

    def to_canonical(self) -> dict:
        return {
            "aas": self.aas, "meaning": _compact(self.meaning), "subjects": [s.to_canonical() for s in self.subjects],
            **{phase: [t.to_canonical() for t in getattr(self, phase)]
               for phase in ("pre", "invariant", "post", "start", "during", "end")},
            "duration": None if self.duration is None else self.duration.to_canonical(),
            "features": list(self.features),
        }


@dataclass(frozen=True)
class Goal:
    """What a batch (or any AAS that states a task) wants to hold at the end, for each of its subjects."""
    aas: str
    path: str
    subjects: tuple[Subject, ...]
    condition: tuple[Term, ...]

    def to_canonical(self) -> dict:
        return {"aas": self.aas, "subjects": [s.to_canonical() for s in self.subjects],
                "condition": [t.to_canonical() for t in self.condition]}


@dataclass(frozen=True)
class Knowledge:
    """The declaration of a state variable (section 7). ``mappings`` is the number of AIMC mapping sinks of the AAS that
    reference it; it is not in the canonical reading."""
    aas: str
    path: str
    subject: Optional[str]
    property: Optional[str]
    value_type: Optional[str]
    cardinality: Optional[str]
    initial: Optional[Literal]
    kind: str                                               # observed, asserted or derived
    mappings: int = 0

    def to_canonical(self) -> dict:
        return {"aas": self.aas, "subject": self.subject, "property": _compact(self.property),
                "valueType": self.value_type, "cardinality": self.cardinality,
                "initial": None if self.initial is None else self.initial.to_canonical(), "kind": self.kind}


@dataclass(frozen=True)
class Reading:
    """Everything the reader found in an environment."""
    capabilities: tuple[Capability, ...]
    goals: tuple[Goal, ...]
    knowledge: tuple[Knowledge, ...]
    findings: tuple[Finding, ...]

    def capability(self, path: str, aas: Optional[str] = None) -> Capability:
        """The capability at ``path`` (of ``aas``, where several AASs have one there); KeyError if there is none."""
        for capability in self.capabilities:
            if capability.path == path and aas in (None, capability.aas):
                return capability
        raise KeyError(path)

    @property
    def goal(self) -> Optional[Goal]:
        """The environment's goal, if it has exactly one."""
        return self.goals[0] if len(self.goals) == 1 else None

    def to_canonical(self) -> dict:
        """Section 15: capabilities keyed by path (by ``<aas id>#<path>`` where two AASs have one at the same), the goal
        (a list of them where the environment has several), knowledge sorted by AAS, subject and property, findings sorted
        by path and code."""
        paths = [c.path for c in self.capabilities]
        unique = len(set(paths)) == len(paths)
        goals = [g.to_canonical() for g in self.goals]
        return {
            "capabilities": {(c.path if unique else f"{c.aas}#{c.path}"): c.to_canonical() for c in self.capabilities},
            "goal": goals[0] if len(goals) == 1 else (goals or None),
            "knowledge": [k.to_canonical() for k in sorted(
                self.knowledge, key=lambda k: (k.aas, k.subject or "", _compact(k.property) or ""))],
            "findings": [f.to_canonical() for f in sorted(self.findings, key=lambda f: (f.path, f.code))],
        }
