"""The validator of the execution model (section 11 of ``docs/execution-model.md``): modreg's check of what the model says.

    findings = validate(environment)            # environment: the dict of an AAS JSON file, one AAS or a whole line

``modplan.execution.read`` reads the environment and reports the structure, reference and formula codes as it reads; the
validator adds every other code of section 11 over that reading, and gives all findings sorted as section 15 says (by path, then
code). A rule that needs what is not in the environment (a component's AAS, the vocabulary) leaves it unchecked: a profile of one
module checks what it holds. ``level.derived`` is checked from Phase 5, with modplan's compose.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Iterator, Optional

from modplan.execution import (
    And, Condition, Effect, Exists, Finding, FixedSubject, ForAll, FormulaOperand, Literal, Not, OneOf, Or,
    Reading, State, SubjectRef, When, Vocabulary, degree, read,
)

from . import execution_levels, execution_translation
from .environment import Environment, children, semantic_id
from .ontology import Finding as Line

SEVERITIES = {
    "operand.type": "error", "operator.cardinality": "error", "operator.unchanged": "error", "operator.during": "error",
    "operator.equal-measured": "warning", "formula.linear": "error", "effect.conflict": "error", "effect.target": "error",
    "declaration.vocabulary": "error", "declaration.kind": "error", "declaration.missing": "error",
    "constraint.state": "error", "translation.realized-by": "error",
    "level.widening": "error", "level.incomplete": "warning",
}
RULES = {
    "structure.kind": "a term's semanticId is a known kind and its children are the kind's",
    "structure.meaning": "an atom has exactly one supplemental semanticId, a vocabulary property",
    "structure.operand": "exactly one operand, or none for measured, unchanged and clear",
    "structure.rate": "a Rate only on an End effect",
    "structure.not-arity": "Not has exactly one term",
    "structure.placement": "condition kinds in conditions, effect kinds in effects, OneOf at End and not nested",
    "structure.subject-name": "every Subjects item has an English displayName",
    "reference.unresolved": "the reference does not resolve",
    "reference.subject": "an atom's Subject is an item of Subjects or an enclosing quantifier's Variable",
    "reference.scope": "a quantifier's Variable is used only inside it",
    "formula.syntax": "the formula does not parse",
    "formula.alias": "an alias is not declared in Uses",
    "formula.unused": "a Use is not used",
    "operand.type": "a literal's valueType does not match the property's value type",
    "operator.cardinality": "the operator does not suit the property's cardinality and value type",
    "operator.unchanged": "unchanged only in an Invariant",
    "operator.during": "During allows only assign",
    "operator.equal-measured": "equal on a property that an effect makes measured",
    "formula.linear": "the formula is not linear in the states that change",
    "effect.conflict": "two assigns of one variable in one phase",
    "effect.target": "the effect targets a variable that is not declared",
    "declaration.vocabulary": "value type and cardinality are not the vocabulary's",
    "declaration.kind": "more than one mapping sinks into the variable, or one into a derived variable",
    "declaration.missing": "the subject's property is not declared",
    "constraint.state": "a BasicConstraint names a state property",
    "translation.realized-by": "a capability with a Translation is not realized by exactly one Operation",
    "level.widening": "a specialization widens or contradicts the capability it specializes",
    "level.incomplete": "a specialization lacks a term of the capability it specializes",
}

PROPERTY_CONSTRAINT_CONTAINER = "https://admin-shell.io/idta/CapabilityDescription/PropertyConstraintContainer/1/0"
BASIC_CONSTRAINT = "https://admin-shell.io/idta/CapabilityDescription/PropertyConstraintType/BasicConstraint/1/0"
CONSTRAINT_HAS_PROPERTY = "https://admin-shell.io/idta/CapabilityDescription/ConstraintHasProperty/1/0"
CONDITION_PHASES = ("pre", "invariant", "post")
EFFECT_PHASES = ("start", "during", "end")
SINGLE_OPERATORS = {"equal", "notEqual", "assign", "unchanged", "measured", "clear"}
NUMERIC_OPERATORS = {"less", "lessOrEqual", "greater", "greaterOrEqual", "increase", "decrease"}
SET_OPERATORS = {"contains", "notContains", "add", "remove"}
CHANGING = {"assign", "increase", "decrease", "measured", "clear", "add", "remove"}


def validate(environment: dict, vocabulary: Optional[Vocabulary] = None) -> list[Finding]:
    """Every finding of section 11 in the environment: the reader's and the validator's, sorted by path, then code.
    ``vocabulary`` is the lab's (read from the ontologies beside this repository if not given); without one, the rules that
    compare with it are not checked."""
    vocabulary = Vocabulary.default() if vocabulary is None else vocabulary
    reading = read(environment, vocabulary)
    model = _Model(Environment(environment), reading, vocabulary)
    found = [*reading.findings]
    for rule in (operand_types, operators, equal_on_measured, linear_formulas, effect_conflicts, declarations_missing,
                 declaration_vocabulary, declaration_kinds, constraints_on_state, translations, specialization):
        found += rule(model)
    return sorted(set(found), key=lambda f: (f.path, f.code))


def check(environment: dict, vocabulary: Optional[Vocabulary] = None, partial: bool = False) -> list[Line]:
    """The findings of ``validate`` in the form of modreg's ontology check (``error: <path>: <message>``; the warnings of
    section 11 are ``warning``). A ``partial`` environment (one AAS of a module, whose components are registered apart) does not
    report what it cannot resolve: its references that lead out of it."""
    return [Line(f.severity, f.path, f"{f.code}: {RULES[f.code]}") for f in validate(environment, vocabulary)
            if not (partial and f.code == "reference.unresolved")]


# ── What the rules read ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Atom:
    """A condition or effect, the phase it is in (``when`` for the condition of a When, ``goal`` in a Goal) and the subjects of
    the capability or goal it belongs to."""
    term: Condition | Effect
    phase: str
    subjects: tuple


@dataclass
class _Model:
    environment: Environment
    reading: Reading
    vocabulary: Vocabulary

    @cached_property
    def atoms(self) -> list[Atom]:
        found: list[Atom] = []
        for capability in self.reading.capabilities:
            for phase in CONDITION_PHASES + EFFECT_PHASES:
                found += _atoms(getattr(capability, phase), phase, capability.subjects)
        for goal in self.reading.goals:
            found += _atoms(goal.condition, "goal", goal.subjects)
        return found

    @cached_property
    def changed(self) -> set[str]:
        """The properties some effect of the environment changes."""
        return {a.term.property for a in self.atoms if isinstance(a.term, Effect) and a.term.op in CHANGING}

    @cached_property
    def measured(self) -> set[str]:
        return {a.term.property for a in self.atoms if isinstance(a.term, Effect) and a.term.op == "measured"}

    @cached_property
    def declared(self) -> set[tuple[Optional[str], Optional[str]]]:
        return {(k.subject, k.property) for k in self.reading.knowledge}

    def formulas(self) -> Iterator[tuple[FormulaOperand, str, tuple]]:
        """Every formula, the path of its element and the subjects it is read against."""
        for atom in self.atoms:
            for operand in (atom.term.operand, getattr(atom.term, "rate", None)):
                if isinstance(operand, FormulaOperand):
                    yield operand, f"{atom.term.path}/Formula", atom.subjects
        for capability in self.reading.capabilities:
            if isinstance(capability.duration, FormulaOperand):
                yield capability.duration, f"{capability.path}/CapabilityRelations/Duration/Formula", capability.subjects

    def property(self, iri: Optional[str]):
        """The vocabulary's entry for a property with a value type and a cardinality (that of a state variable), or None."""
        found = self.vocabulary.properties.get(iri)
        return found if found is not None and found.value_type and found.cardinality else None

    def fixed(self, subjects: tuple, reference) -> Optional[str]:
        """The identity of a fixed subject that is in the environment, where ``reference`` names one."""
        if not isinstance(reference, SubjectRef) or reference.index >= len(subjects):
            return None
        subject = subjects[reference.index]
        return subject.target if isinstance(subject, FixedSubject) and self.environment.holds(subject.target) else None


def _atoms(terms, phase: str, subjects: tuple) -> Iterator[Atom]:
    for term in terms:
        if isinstance(term, (Condition, Effect)):
            yield Atom(term, phase, subjects)
        elif isinstance(term, (And, Or)):
            yield from _atoms(term.terms, phase, subjects)
        elif isinstance(term, Not):
            yield from _atoms((term.term,), phase, subjects)
        elif isinstance(term, (Exists, ForAll)):
            yield from _atoms(term.body, phase, subjects)
        elif isinstance(term, When):
            yield from _atoms(term.condition, "when", subjects)
            yield from _atoms(term.then, phase, subjects)
        elif isinstance(term, OneOf):
            for outcome in term.outcomes:
                yield from _atoms(outcome.effects, phase, subjects)


def _finding(code: str, path: str) -> Finding:
    return Finding(code, path, SEVERITIES[code])


# ── Atoms: operands and operators ────────────────────────────────────────────

def operand_types(model: _Model) -> list[Finding]:
    """[`operand.type`] A literal's valueType is the property's value type (a reference value is an ``xs:anyURI``), a formula
    gives a number."""
    found = []
    for atom in model.atoms:
        term, known = atom.term, model.vocabulary.properties.get(atom.term.property)
        if known is None or known.value_type is None:
            continue
        if isinstance(term.operand, Literal) and term.operand.type != {"reference": "iri"}.get(known.value_type, known.value_type):
            found.append(_finding("operand.type", f"{term.path}/Value"))
        elif isinstance(term.operand, FormulaOperand) and known.value_type != "number":
            found.append(_finding("operand.type", f"{term.path}/Formula"))
    return found


def operators(model: _Model) -> list[Finding]:
    """[`operator.unchanged`] ``unchanged`` only in an Invariant, [`operator.during`] During only ``assign``,
    [`operator.cardinality`] the operator suits the property's cardinality and value type (5.4)."""
    found = []
    for atom in model.atoms:
        term, known = atom.term, model.property(atom.term.property)
        path = f"{term.path}/Operator"
        if term.op == "unchanged" and atom.phase != "invariant":
            found.append(_finding("operator.unchanged", path))
        if atom.phase == "during" and term.op != "assign":
            found.append(_finding("operator.during", path))
        if known is not None and not _suits(term.op, known.cardinality, known.value_type):
            found.append(_finding("operator.cardinality", path))
    return found


def _suits(op: Optional[str], cardinality: str, value_type: str) -> bool:
    """Whether the operator is one of the table of 5.4 for a property of this cardinality and value type (an unknown one is the
    reader's finding)."""
    if op not in SINGLE_OPERATORS | NUMERIC_OPERATORS | SET_OPERATORS:
        return True
    if cardinality == "set":
        return op in SET_OPERATORS
    return op in SINGLE_OPERATORS or op in NUMERIC_OPERATORS and value_type == "number"


def equal_on_measured(model: _Model) -> list[Finding]:
    """[`operator.equal-measured`] A condition does not use ``equal`` on a property that some effect makes ``measured``."""
    return [Finding("operator.equal-measured", f"{a.term.path}/Operator", "warning") for a in model.atoms
            if isinstance(a.term, Condition) and a.term.op == "equal" and a.term.property in model.measured]


# ── Formulas ─────────────────────────────────────────────────────────────────

def linear_formulas(model: _Model) -> list[Finding]:
    """[`formula.linear`] A formula is linear in every alias that reads a state some effect of the environment changes."""
    found = []
    for formula, path, _ in model.formulas():
        varying = {alias for alias, use in formula.uses if isinstance(use, State) and use.property in model.changed}
        if formula.ast is not None and degree(formula.ast, varying) > 1:
            found.append(_finding("formula.linear", path))
    return found


# ── Effects ──────────────────────────────────────────────────────────────────

def effect_conflicts(model: _Model) -> list[Finding]:
    """[`effect.conflict`] Effects that are applied together (those of a phase outside a When, and those of one Outcome) do not
    assign one variable different operands; the later term is reported."""
    found = []
    for capability in model.reading.capabilities:
        for phase in EFFECT_PHASES:
            terms = getattr(capability, phase)
            for group in _applied_together(terms):
                for i, later in enumerate(group):
                    if any(_conflict(earlier, later) for earlier in group[:i]):
                        found.append(_finding("effect.conflict", later.path))
    return found


def _applied_together(terms) -> list[list[Effect]]:
    """The groups of effects that happen together: those that always do, and those with each Outcome of a OneOf."""
    always, outcomes = _always(terms)
    return [always, *[always + group for group in outcomes]]


def _always(terms) -> tuple[list[Effect], list[list[Effect]]]:
    """The effects of ``terms`` that happen whatever else (through And and ForAll, not When), and the effects of each Outcome."""
    always: list[Effect] = []
    outcomes: list[list[Effect]] = []
    for term in terms:
        if isinstance(term, Effect):
            always.append(term)
        elif isinstance(term, (And, ForAll)):
            inner = _always(term.terms if isinstance(term, And) else term.body)
            always += inner[0]
            outcomes += inner[1]
        elif isinstance(term, OneOf):
            outcomes += [[a.term for a in _atoms(o.effects, "end", ()) if isinstance(a.term, Effect)] for o in term.outcomes]
    return always, outcomes


def _conflict(earlier: Effect, later: Effect) -> bool:
    return (earlier.op == later.op == "assign" and earlier.subject is not None and earlier.subject == later.subject
            and earlier.property == later.property and earlier.operand != later.operand)


# ── Declarations ─────────────────────────────────────────────────────────────

def declarations_missing(model: _Model) -> list[Finding]:
    """[`effect.target`] An effect, and [`declaration.missing`] a condition or a Use, on a fixed subject reads or changes a
    state variable its AAS declares. (A unit's variables are those of its product type, a run-time subject's those of whatever
    it is bound to: checked when the batch is known and when the planner grounds them.)"""
    found = []
    for atom in model.atoms:
        term = atom.term
        identity = model.fixed(atom.subjects, term.subject)
        if identity is not None and (identity, term.property) not in model.declared:
            found.append(_finding("effect.target" if isinstance(term, Effect) else "declaration.missing", term.path))
        for operand in (term.operand, getattr(term, "rate", None)):
            if isinstance(operand, FormulaOperand):
                found += _reads(model, operand, term.path, atom.subjects)
    for capability in model.reading.capabilities:
        if isinstance(capability.duration, FormulaOperand):
            found += _reads(model, capability.duration, f"{capability.path}/CapabilityRelations/Duration", capability.subjects)
    return found


def _reads(model: _Model, formula: FormulaOperand, path: str, subjects: tuple) -> list[Finding]:
    return [_finding("declaration.missing", f"{path}/Uses/{alias}") for alias, use in formula.uses
            if isinstance(use, State) and (model.fixed(subjects, use.subject), use.property) not in model.declared
            and model.fixed(subjects, use.subject) is not None]


def declaration_vocabulary(model: _Model) -> list[Finding]:
    """[`declaration.vocabulary`] A state variable's value type and cardinality are the vocabulary's; a property a term is about
    has both there."""
    if not model.vocabulary.properties:
        return []
    found = []
    for variable in model.reading.knowledge:
        known = model.vocabulary.properties.get(variable.property)
        if variable.property and (known is None or (variable.value_type, variable.cardinality) != (known.value_type, known.cardinality)):
            found.append(_finding("declaration.vocabulary", variable.path))
    for atom in model.atoms:
        known = model.vocabulary.properties.get(atom.term.property)
        if known is not None and not (known.value_type and known.cardinality):
            found.append(_finding("declaration.vocabulary", atom.term.path))
    return found


def declaration_kinds(model: _Model) -> list[Finding]:
    """[`declaration.kind`] At most one AIMC mapping sinks into a variable, and none into a derived one."""
    return [_finding("declaration.kind", k.path) for k in model.reading.knowledge
            if k.mappings > 1 or k.mappings and model.vocabulary.is_derived(k.property)]


def constraints_on_state(model: _Model) -> list[Finding]:
    """[`constraint.state`] A BasicConstraint of a ConstraintSet is about properties of the capability, none of them a state
    property (one with a cardinality in the vocabulary, D5)."""
    found = []

    def walk(element: dict, path: str) -> None:
        if semantic_id(element) == PROPERTY_CONSTRAINT_CONTAINER:
            constraint = next((c for c in children(element) if semantic_id(c) == BASIC_CONSTRAINT), None)
            if constraint is not None and any(model.property(m) for m in _constrained(element, model)):
                found.append(_finding("constraint.state", f"{path}/{constraint.get('idShort')}"))
        for i, child in enumerate(children(element)):
            walk(child, f"{path}/{child.get('idShort', i)}")

    for submodel in model.environment.submodels.values():
        for i, element in enumerate(submodel.get("submodelElements") or ()):
            walk(element, f"{submodel.get('idShort', '')}/{element.get('idShort', i)}")
    return found


def _constrained(container: dict, model: _Model) -> list[Optional[str]]:
    """The meanings of the capability properties that the ConstraintHasProperty relations of a constraint container name."""
    properties = [p for c in model.reading.capabilities for p in c.properties]
    relations = [item for group in children(container) for item in children(group) if semantic_id(item) == CONSTRAINT_HAS_PROPERTY]
    found = []
    for relation in relations:
        keys = [k["value"] for k in (relation.get("second") or {}).get("keys") or ()]
        if keys and keys[0] in model.environment.submodels:
            path = "/".join([model.environment.submodels[keys[0]].get("idShort", ""), *keys[1:]])
            found += [p.meaning for p in properties if path == p.path or path.startswith(p.path + "/")]
    return found


# ── Translations and levels ──────────────────────────────────────────────────

def translations(model: _Model) -> list[Finding]:
    """[`translation.realized-by`] (section 9): see ``execution_translation``."""
    return [f for c in model.reading.capabilities for f in execution_translation.check(c, model.environment)]


def specialization(model: _Model) -> list[Finding]:
    """[`level.widening`] and [`level.incomplete`] (section 10): see ``execution_levels``."""
    return execution_levels.check(model.reading, model.environment)
