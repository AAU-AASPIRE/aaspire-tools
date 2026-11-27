"""Levels and specialization of the execution model (section 10 of ``docs/execution-model.md``): a capability that generalizes
another (GeneralizedBy) only narrows and adds.

Its subjects correspond to the general one's by identity, not position: the same AAS, the same meaning it stands for or requires, the
n-th of a kind to the n-th (a general Self, the component's own AAS, to the specific subject that references that AAS). Every term of
a general phase then has, under the corresponding subjects, an equal or tighter term in the same phase: a term that widens or
contradicts is [`level.widening`]; one with no counterpart at all is [`level.incomplete`], a warning until the generator of Phase 13
writes the supersets.
"""
from __future__ import annotations

import math
from typing import Optional

from modplan.execution import (
    And, Capability, Condition, Effect, Finding, FixedSubject, Literal, Reading, RuntimeSubject,
)

from .environment import Environment

PHASES = ("pre", "invariant", "post", "start", "during", "end")
COMPARISONS = {"equal", "less", "lessOrEqual", "greater", "greaterOrEqual"}


def check(reading: Reading, environment: Environment) -> list[Finding]:
    """The findings of every capability against the capabilities it generalizes to, where those are in the environment."""
    found: list[Finding] = []
    for specific in reading.capabilities:
        for relation in specific.generalized_by:
            general = _general(reading, environment, relation.target)
            if general is not None:
                found += _narrows(general, specific)
    return found


def _general(reading: Reading, environment: Environment, identity: Optional[str]) -> Optional[Capability]:
    """The capability a GeneralizedBy names (the identity of its ``Capability`` element, ``<submodel id>#<path>``)."""
    submodel, _, path = (identity or "").partition("#")
    if submodel not in environment.submodels:
        return None
    container = path.rsplit("/", 1)[0] if path.rsplit("/", 1)[-1] == "Capability" else path
    wanted = f"{environment.submodels[submodel].get('idShort', '')}/{container}"
    return next((c for c in reading.capabilities if c.path == wanted and c.aas in environment.owners.get(submodel, ())), None)


def _narrows(general: Capability, specific: Capability) -> list[Finding]:
    corresponding = _correspondence(general, specific)
    found = []
    for phase in PHASES:
        offered = _conjuncts(getattr(specific, phase))
        for term in _conjuncts(getattr(general, phase)):
            verdict = _counterpart(term, offered, corresponding)
            if verdict is None:
                found.append(Finding("level.incomplete", _phase_path(specific, phase), "warning"))
            elif verdict is not True:
                found.append(Finding("level.widening", verdict.path, "error"))
    return found


def _phase_path(capability: Capability, phase: str) -> str:
    relations = "ConditionSet" if phase in ("pre", "invariant", "post") else "EffectSet"
    return f"{capability.path}/CapabilityRelations/{relations}/{phase.capitalize()}"


# ── Subjects ─────────────────────────────────────────────────────────────────

def _correspondence(general: Capability, specific: Capability) -> dict[int, int]:
    """For each general subject, the index of the specific subject it corresponds to (the first not taken, where the specific
    has several of one kind); a general subject without one is left out."""
    taken: set[int] = set()
    found: dict[int, int] = {}
    for i, subject in enumerate(general.subjects):
        for j, candidate in enumerate(specific.subjects):
            if j not in taken and _identity(subject) == _identity(candidate):
                found[i] = j
                taken.add(j)
                break
    return found


def _identity(subject) -> tuple:
    if isinstance(subject, FixedSubject):
        return "fixed", subject.target
    if isinstance(subject, RuntimeSubject):
        return "run-time", subject.stands_for
    return "requirement", subject.meaning


def _renumbered(canonical, corresponding: dict[int, int]):
    """A term or operand in the canonical form, with its subject indexes the specific capability's."""
    if isinstance(canonical, dict):
        return {key: corresponding.get(value, -1) if key == "subject" and isinstance(value, int) else _renumbered(value, corresponding)
                for key, value in canonical.items()}
    if isinstance(canonical, list):
        return [_renumbered(item, corresponding) for item in canonical]
    return canonical


# ── Terms ────────────────────────────────────────────────────────────────────

def _conjuncts(terms) -> list:
    """The terms of a phase with every And opened: they all hold."""
    return [part for term in terms for part in (_conjuncts(term.terms) if isinstance(term, And) else [term])]


def _counterpart(term, offered: list, corresponding: dict[int, int]):
    """True where the specific terms ``offered`` hold an equal or tighter term for the general ``term``; the first one that
    widens or contradicts it where there is one; None where there is none to compare."""
    if not isinstance(term, (Condition, Effect)):
        mine = _renumbered(term.to_canonical(), corresponding)
        return True if any(mine == other.to_canonical() for other in offered) else None
    comparable = None
    for other in offered:
        if type(other) is not type(term) or not _same_target(term, other, corresponding):
            continue
        if _covers(term, other, corresponding):
            return True
        if _comparable(term, other):
            comparable = comparable or other
    return comparable


def _same_target(general, specific, corresponding: dict[int, int]) -> bool:
    if general.property != specific.property or general.subject is None or specific.subject is None:
        return False
    return _renumbered(general.subject.to_canonical(), corresponding) == specific.subject.to_canonical()


def _comparable(general, specific) -> bool:
    """Whether two terms about one subject and property say something of the same kind, so that one can contradict the other."""
    if not isinstance(general.operand, Literal) or not isinstance(specific.operand, Literal):
        return False
    if isinstance(general, Effect):
        return general.op == specific.op
    return {general.op, specific.op} <= COMPARISONS | {"notEqual"}


def _covers(general, specific, corresponding: dict[int, int]) -> bool:
    """Whether ``specific`` is equal to ``general`` or tighter."""
    if general.op == specific.op and _same_operand(general, specific, corresponding):
        return True
    if isinstance(general, Condition) and _comparable(general, specific):
        return _within(general, specific)
    return False


def _same_operand(general, specific, corresponding: dict[int, int]) -> bool:
    mine, yours = (None if t.operand is None else t.operand.to_canonical() for t in (general, specific))
    rates = [None if getattr(t, "rate", None) is None else t.rate.to_canonical() for t in (general, specific)]
    return _renumbered(mine, corresponding) == yours and _renumbered(rates[0], corresponding) == rates[1]


def _within(general: Condition, specific: Condition) -> bool:
    """Whether every value the specific condition admits, the general one does: comparisons of numbers as intervals, a
    ``notEqual`` as the one value left out, anything else a literal that must be the same."""
    if general.op == "notEqual":
        return specific.op == "equal" and specific.operand.value != general.operand.value \
            or specific.op == "notEqual" and specific.operand.value == general.operand.value
    if not (general.op in COMPARISONS and specific.op in COMPARISONS):
        return False
    if general.operand.type == specific.operand.type == "number":
        return _inside(_interval(specific), _interval(general))
    return general.op == specific.op == "equal" and general.operand.value == specific.operand.value


def _interval(condition: Condition) -> tuple[float, bool, float, bool]:
    """(low, low open, high, high open) of the numbers a comparison admits."""
    x, op = condition.operand.value, condition.op
    low, high = (x, x) if op == "equal" else (x, math.inf) if op in ("greater", "greaterOrEqual") else (-math.inf, x)
    return low, op == "greater", high, op == "less"


def _inside(inner: tuple, outer: tuple) -> bool:
    low = inner[0] > outer[0] or inner[0] == outer[0] and (inner[1] or not outer[1])
    high = inner[2] < outer[2] or inner[2] == outer[2] and (inner[3] or not outer[3])
    return low and high
