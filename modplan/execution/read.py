"""The reader of the execution model: an AAS environment (the JSON a server or a file gives, several AASs and submodels) into
the capabilities, goals and knowledge of ``docs/execution-model.md``, as the typed reading of ``model``.

What an element is, is its semanticId (section 2): a capability is a container whose CapabilityRelations hold Subjects,
a ConditionSet or an EffectSet, a goal is the submodel with the Goal's semanticId, a declaration an SMC with the
Knowledge's; nothing is found by idShort but the aliases of a formula's Uses. References are resolved inside the
environment: a subject by its index, a quantifier's variable by containment, an Operation's variable by passing through
the Operation, a constant by its idShort path, an element's identity as ``<submodel id>#<idShortPath>``.

The reader is lenient: it never raises on a broken model. Where it cannot read something it records a finding with the code
of section 11 (the structure, reference and formula codes are the reader's, the others the validator's) and goes on,
leaving out what it could not read.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from typing import Optional

from aas_model import constants as c
from modplan.execution import formula as formulas
from modplan.execution.model import (
    And, Capability, CapabilityProperty, Condition, Effect, Element, Exists, FixedSubject, Finding, ForAll,
    FormulaOperand, Goal, Knowledge, Literal, ModelReference, Not, OneOf, Operand, Or, Outcome, Parameter, Reading,
    RequirementSubject, RuntimeSubject, State, SubjectRef, Translation, Variable, VariableRef, When,
)
from modplan.execution.vocabulary import Vocabulary

_IDTA = "https://admin-shell.io/idta"
CAPABILITY = f"{_IDTA}/CapabilityDescription/Capability/1/0"
CAPABILITY_RELATIONS = f"{_IDTA}/CapabilityDescription/CapabilityRelations/1/0"
PROPERTY_SET = f"{_IDTA}/CapabilityDescription/PropertySet/1/0"
PROPERTY_CONTAINER = f"{_IDTA}/CapabilityDescription/PropertyContainer/1/0"
REALIZED_BY = f"{_IDTA}/CapabilityDescription/CapabilityRealizedBy/1/0"
GENERALIZED_BY_SET = f"{_IDTA}/CapabilityDescription/GeneralizedBySet/1/0"
GENERALIZED_BY = f"{_IDTA}/CapabilityDescription/CapabilityGeneralizedBy/1/0"
# The elements of an AIMC mapping that sink into the model: an action's Sinks, and a response's.
SINKS = {f"{_IDTA}/AssetInterfacesMappingConfiguration/2/0/MappingConfiguration/Sink/Sink",
         c.AIMC_RESPONSE_SINK_REFERENCE}

TERM_KINDS = {
    c.EXECUTION_CONDITION: "condition", c.EXECUTION_EFFECT: "effect", c.EXECUTION_AND: "and", c.EXECUTION_OR: "or",
    c.EXECUTION_NOT: "not", c.EXECUTION_EXISTS: "exists", c.EXECUTION_FOR_ALL: "forall", c.EXECUTION_WHEN: "when",
    c.EXECUTION_ONE_OF: "oneof",
}
CONDITION_ONLY = {"condition", "or", "not", "exists"}
EFFECT_ONLY = {"effect", "when", "oneof"}
CONDITION_OPERATORS = {"equal", "notEqual", "less", "lessOrEqual", "greater", "greaterOrEqual", "unchanged", "contains",
                       "notContains"}
EFFECT_OPERATORS = {"assign", "increase", "decrease", "measured", "clear", "add", "remove"}
NO_OPERAND = {"unchanged", "measured", "clear"}
NUMERIC_OPERATORS = {"less", "lessOrEqual", "greater", "greaterOrEqual", "increase", "decrease"}
SET_OPERATORS = {"contains", "notContains", "add", "remove"}
NUMBER_TYPES = {f"xs:{name}" for name in (
    "double", "float", "decimal", "integer", "int", "long", "short", "byte", "nonNegativeInteger", "positiveInteger",
    "negativeInteger", "nonPositiveInteger", "unsignedLong", "unsignedInt", "unsignedShort", "unsignedByte")}
CONDITION_PHASES = ((c.EXECUTION_PRE, "pre"), (c.EXECUTION_INVARIANT, "invariant"), (c.EXECUTION_POST, "post"))
EFFECT_PHASES = ((c.EXECUTION_START, "start"), (c.EXECUTION_DURING, "during"), (c.EXECUTION_END, "end"))


def read(environment: dict, vocabulary: Optional[Vocabulary] = None) -> Reading:
    """The capabilities, goals and knowledge of an AAS environment (the JSON of ``assetAdministrationShells`` and
    ``submodels``), with the findings of the reader's rules. ``vocabulary`` is the lab's (read from the ontologies beside
    this repository if not given)."""
    return _Reader(environment, Vocabulary.default() if vocabulary is None else vocabulary).read()


# ── Elements of the AAS JSON ─────────────────────────────────────────────────

def _sid(element: dict) -> Optional[str]:
    keys = (element.get("semanticId") or {}).get("keys")
    return keys[0]["value"] if keys else None


def _supplemental(element: dict) -> list[str]:
    return [r["keys"][0]["value"] for r in element.get("supplementalSemanticIds") or () if r.get("keys")]


def _children(element: dict) -> list[dict]:
    value = element.get("value")
    return value if element.get("modelType") in ("SubmodelElementCollection", "SubmodelElementList") \
        and isinstance(value, list) else []


def _named(element: dict, index: int) -> str:
    return element.get("idShort") or str(index)


def _find(element: dict, semantic_id: str) -> Optional[dict]:
    return next((child for child in _children(element) if _sid(child) == semantic_id), None)


def _top(submodel: dict) -> dict:
    """A submodel as the collection of its elements, for the lookups elements have."""
    return {"modelType": "SubmodelElementCollection", "value": submodel.get("submodelElements") or []}


def _english(element: dict) -> Optional[str]:
    for text in element.get("displayName") or ():
        if str(text.get("language", "")).lower().split("-")[0] == "en" and str(text.get("text", "")).strip():
            return text["text"]
    return None


def _reference_value(element: dict) -> Optional[dict]:
    value = element.get("value")
    return value if isinstance(value, dict) and value.get("keys") else None


# ── Resolving references ─────────────────────────────────────────────────────

@dataclass
class _Target:
    """What a ModelReference resolves to: an AAS, a submodel, or an element (``path`` is its idShorts from the submodel's,
    a list item's index where it has no idShort; ``nodes`` the elements down to it)."""
    kind: str
    aas: Optional[str] = None
    submodel: Optional[dict] = None
    path: tuple[str, ...] = ()
    nodes: tuple[dict, ...] = ()
    operation_at: Optional[int] = None                      # where in ``path`` the Operation passed through is

    @property
    def text(self) -> str:
        return "/".join(self.path)


@dataclass
class _Context:
    """Where terms are read: the submodel, the AAS, the subjects, the quantifier variables of the capability or goal."""
    submodel: str
    aas: str
    subjects: str = ""
    subject_count: int = 0
    variables: frozenset = frozenset()                      # the paths of every quantifier variable here
    scope: dict = field(default_factory=dict)               # the paths of those that enclose, with their numbers
    declared: int = 0


class _Reader:
    def __init__(self, environment: dict, vocabulary: Vocabulary):
        self.vocabulary = vocabulary
        self.aases = {a["id"]: a for a in environment.get("assetAdministrationShells") or ()}
        self.submodels = {s["id"]: s for s in environment.get("submodels") or ()}
        self.owners: dict[str, list[str]] = {}
        for aas in self.aases.values():
            for reference in aas.get("submodels") or ():
                if reference.get("keys"):
                    self.owners.setdefault(reference["keys"][-1]["value"], []).append(aas["id"])
        self.findings: list[Finding] = []
        self.mappings: dict[tuple[str, str, str], int] = {}

    def read(self) -> Reading:
        capabilities, goals, knowledge = [], [], []
        for submodel in self.submodels.values():
            self._sinks(submodel)
        for submodel in self.submodels.values():
            path = submodel.get("idShort", "")
            for i, element in enumerate(submodel.get("submodelElements") or ()):
                self._scan(submodel, element, f"{path}/{_named(element, i)}", capabilities, knowledge)
            if _sid(submodel) == c.SM_GOAL:
                goals.append(self._goal(submodel))
        findings = sorted(set(self.findings), key=lambda f: (f.path, f.code))
        return Reading(tuple(capabilities), tuple(goals), tuple(knowledge), tuple(findings))

    def report(self, code: str, path: str) -> None:
        self.findings.append(Finding(code, path))

    def owner(self, submodel: str) -> str:
        return (self.owners.get(submodel) or [""])[0]

    # ── references ───────────────────────────────────────────────────────────

    def resolve(self, reference: Optional[dict]) -> Optional[_Target]:
        keys = (reference or {}).get("keys") or []
        if not keys or reference.get("type") != "ModelReference":
            return None
        aas, at = None, 0
        if keys[0]["type"] == "AssetAdministrationShell":
            if keys[0]["value"] not in self.aases:
                return None
            aas, at = keys[0]["value"], 1
            if len(keys) == 1:
                return _Target("aas", aas)
        submodel = self.submodels.get(keys[at]["value"]) if keys[at]["type"] == "Submodel" else None
        if submodel is None:
            return None
        node, nodes, path, operation = submodel, [submodel], [submodel.get("idShort", "")], None
        for key in keys[at + 1:]:
            child, name = self._step(node, key["value"])
            if child is None:
                return None
            if child.get("modelType") == "Operation":
                operation = len(path)
            node = child
            nodes.append(child)
            path.append(name)
        return _Target("element" if len(keys) > at + 1 else "submodel", aas or self.owner(submodel["id"]), submodel,
                       tuple(path), tuple(nodes), operation)

    @staticmethod
    def _step(node: dict, value: str) -> tuple[Optional[dict], str]:
        if "submodelElements" in node:
            children = node["submodelElements"]
        elif node.get("modelType") == "Operation":
            children = [v["value"] for kind in ("inputVariables", "outputVariables", "inoutVariables")
                        for v in node.get(kind) or () if v.get("value")]
        else:
            children = _children(node)
        if node.get("modelType") == "SubmodelElementList":
            if value.isdigit() and int(value) < len(children):
                return children[int(value)], value
            return None, value
        return next((child for child in children if child.get("idShort") == value), None), value

    def identity(self, target: _Target) -> str:
        """What the state store calls it (section 13): the AAS's id, or ``<submodel id>#<idShortPath>``."""
        if target.kind == "aas":
            return target.aas
        if target.kind == "submodel":
            return target.submodel["id"]
        return f"{target.submodel['id']}#{'/'.join(target.path[1:])}"

    @staticmethod
    def identity_of_keys(keys: list[dict]) -> str:
        """The identity a reference names, resolved or not."""
        if keys[0]["type"] == "AssetAdministrationShell":
            return keys[0]["value"]
        rest = "/".join(k["value"] for k in keys[1:])
        return f"{keys[0]['value']}#{rest}" if rest else keys[0]["value"]

    def render(self, target: _Target, count: int, home: str) -> str:
        """The first ``count`` parts of the target's path: from the submodel's idShort within the AAS ``home``, else as an
        identity."""
        parts = target.path[:count]
        if home in self.owners.get(target.submodel["id"], ()):
            return "/".join(parts)
        return f"{target.submodel['id']}#{'/'.join(parts[1:])}"

    # ── the elements that are read ───────────────────────────────────────────

    def _sinks(self, submodel: dict) -> None:
        """Count, per AAS, the AIMC sinks that reference each element."""
        def walk(element: dict, path: str) -> None:
            if _sid(element) in SINKS and element.get("modelType") == "ReferenceElement":
                target = self.resolve(_reference_value(element))
                if target is None:
                    self.report("reference.unresolved", path)
                elif target.kind == "element":
                    for aas in self.owners.get(submodel["id"], ()):
                        key = (aas, target.submodel["id"], target.text)
                        self.mappings[key] = self.mappings.get(key, 0) + 1
            for i, child in enumerate(_children(element)):
                walk(child, f"{path}/{_named(child, i)}")

        for i, element in enumerate(submodel.get("submodelElements") or ()):
            walk(element, f"{submodel.get('idShort', '')}/{_named(element, i)}")

    def _scan(self, submodel: dict, element: dict, path: str, capabilities: list, knowledge: list) -> None:
        if element.get("modelType") not in ("SubmodelElementCollection", "SubmodelElementList"):
            return
        relations = _find(element, CAPABILITY_RELATIONS)
        if relations is not None and any(child.get("modelType") == "Capability" for child in _children(element)):
            if any(_find(relations, sid) for sid in (c.EXECUTION_SUBJECTS, c.EXECUTION_CONDITION_SET, c.EXECUTION_EFFECT_SET)):
                capabilities.append(self._capability(submodel, element, relations, path))
            return
        if _sid(element) == c.VARIABLES_KNOWLEDGE:
            for i, variable in enumerate(_children(element)):
                if _sid(variable) == c.STATE_VARIABLE:
                    knowledge.append(self._knowledge(submodel, variable, f"{path}/{_named(variable, i)}"))
            return
        for i, child in enumerate(_children(element)):
            self._scan(submodel, child, f"{path}/{_named(child, i)}", capabilities, knowledge)

    # ── capabilities and goals ───────────────────────────────────────────────

    def _context(self, submodel: dict, scope: dict, scope_path: str) -> _Context:
        """The context of the capability (or goal) whose elements are ``scope`` at ``scope_path``."""
        subjects = _find(scope, c.EXECUTION_SUBJECTS)
        variables: set[str] = set()

        def walk(element: dict, path: str) -> None:
            for i, child in enumerate(_children(element)):
                child_path = f"{path}/{_named(child, i)}"
                if _sid(child) == c.EXECUTION_QUANTIFIED_VARIABLE:
                    variables.add(child_path)
                walk(child, child_path)

        walk(scope, scope_path)
        return _Context(submodel["id"], self.owner(submodel["id"]),
                        f"{scope_path}/{subjects.get('idShort')}" if subjects else "",
                        len(_children(subjects)) if subjects else 0, frozenset(variables))

    def _subjects(self, scope: dict, scope_path: str) -> tuple:
        element = _find(scope, c.EXECUTION_SUBJECTS)
        if element is None:
            return ()
        found = []
        for i, item in enumerate(_children(element)):
            path = f"{scope_path}/{element.get('idShort')}/{i}"
            name = _english(item)
            if name is None:
                self.report("structure.subject-name", path)
            name = name or ""
            reference = item.get("value") if isinstance(item.get("value"), dict) else None
            keys = (reference or {}).get("keys") or []
            if not keys:
                self.report("structure.kind", path)
                found.append(RuntimeSubject(name, ""))
            elif reference.get("type") == "ModelReference":
                target = self.resolve(reference)
                if target is None:
                    self.report("reference.unresolved", path)
                found.append(FixedSubject(name, self.identity(target) if target else self.identity_of_keys(keys)))
            elif c.EXECUTION_SUBJECT_REQUIREMENT in _supplemental(item):
                found.append(RequirementSubject(name, keys[0]["value"]))
            else:
                found.append(RuntimeSubject(name, keys[0]["value"]))
        return tuple(found)

    def _capability(self, submodel: dict, container: dict, relations: dict, path: str) -> Capability:
        relations_path = f"{path}/{relations.get('idShort')}"
        context = self._context(submodel, relations, relations_path)
        subjects = self._subjects(relations, relations_path)
        phases: dict[str, tuple] = {name: () for _, name in CONDITION_PHASES + EFFECT_PHASES}
        for parent_id, table, mode in ((c.EXECUTION_CONDITION_SET, CONDITION_PHASES, "condition"),
                                       (c.EXECUTION_EFFECT_SET, EFFECT_PHASES, "effect")):
            parent = _find(relations, parent_id)
            for phase_id, name in table:
                phase = _find(parent, phase_id) if parent else None
                if phase is not None:
                    phases[name] = self._terms(
                        phase, f"{relations_path}/{parent.get('idShort')}/{phase.get('idShort')}", context, mode, name)
        duration = _find(relations, c.EXECUTION_DURATION)
        duration_operand = None
        if duration is not None:
            duration_operand = self._operand(duration, f"{relations_path}/{duration.get('idShort')}", context, True)
        meaning = next((_supplemental(child)[:1] or [None])[0] for child in _children(container)
                       if child.get("modelType") == "Capability")
        return Capability(
            aas=self.owner(submodel["id"]), path=path, meaning=meaning, subjects=subjects, **phases,
            duration=duration_operand, features=_features(phases, duration_operand),
            **self._relations(relations, relations_path),
            properties=self._properties(container, path))

    def _relations(self, relations: dict, path: str) -> dict:
        found = {"realized_by": [], "generalized_by": [], "translation": None}
        for i, child in enumerate(_children(relations)):
            child_path = f"{path}/{_named(child, i)}"
            if _sid(child) == REALIZED_BY:
                found["realized_by"].append(self._relation(child, child_path))
            elif _sid(child) == GENERALIZED_BY_SET:
                found["generalized_by"] += [self._relation(g, f"{child_path}/{_named(g, j)}")
                                            for j, g in enumerate(_children(child)) if _sid(g) == GENERALIZED_BY]
            elif _sid(child) == c.EXECUTION_TRANSLATION:
                found["translation"] = Translation(*(self._blob(_find(child, sid))
                                                      for sid in (c.EXECUTION_TRANSLATION_CALL,
                                                                  c.EXECUTION_TRANSLATION_REPLY)), path=child_path)
        return {"realized_by": tuple(found["realized_by"]), "generalized_by": tuple(found["generalized_by"]),
                "translation": found["translation"]}

    def _relation(self, element: dict, path: str) -> ModelReference:
        """What a RealizedBy or GeneralizedBy points at (its ``second``)."""
        reference = element.get("second") or {}
        target = self.resolve(reference)
        if target is None:
            self.report("reference.unresolved", path)
        return ModelReference(tuple((k["type"], k["value"]) for k in reference.get("keys") or ()),
                              None if target is None else self.identity(target))

    @staticmethod
    def _blob(element: Optional[dict]) -> Optional[str]:
        try:
            return base64.b64decode(element["value"], validate=True).decode("utf-8")
        except (TypeError, KeyError, ValueError, binascii.Error):
            return None

    def _properties(self, container: dict, path: str) -> tuple:
        found = []
        property_set = _find(container, PROPERTY_SET)
        for i, entry in enumerate(_children(property_set) if property_set else ()):
            if _sid(entry) != PROPERTY_CONTAINER:
                continue
            entry_path = f"{path}/{property_set.get('idShort')}/{_named(entry, i)}"
            value = next((v for v in _children(entry) if _supplemental(v)), None)
            found.append(CapabilityProperty(
                entry_path, _named(entry, i), _supplemental(value)[0] if value else None,
                self._literal(value, f"{entry_path}/{value.get('idShort')}")
                if value and value.get("modelType") == "Property" else None))
        return tuple(found)

    def _goal(self, submodel: dict) -> Goal:
        path = submodel.get("idShort", "")
        top = _top(submodel)
        context = self._context(submodel, top, path)
        condition = _find(top, c.GOAL_CONDITION)
        terms = self._terms(condition, f"{path}/{condition.get('idShort')}", context, "condition", "goal") if condition else ()
        return Goal(self.owner(submodel["id"]), path, self._subjects(top, path), terms)

    # ── terms ────────────────────────────────────────────────────────────────

    def _terms(self, parent: dict, path: str, context: _Context, mode: str, phase: str, nested: bool = False) -> tuple:
        children = sorted(enumerate(_children(parent)), key=lambda pair: _named(pair[1], pair[0]))
        found = (self._term(child, f"{path}/{_named(child, i)}", context, mode, phase, nested) for i, child in children)
        return tuple(term for term in found if term is not None)

    def _term(self, element: dict, path: str, context: _Context, mode: str, phase: str, nested: bool):
        kind = TERM_KINDS.get(_sid(element))
        if kind is None:
            self.report("structure.kind", path)
            return None
        if (mode == "condition" and kind in EFFECT_ONLY) or (mode == "effect" and kind in CONDITION_ONLY):
            self.report("structure.placement", path)
        elif kind == "oneof" and (phase != "end" or nested):
            self.report("structure.placement", path)
        if kind in ("condition", "effect"):
            return self._atom(element, path, context, kind, phase)
        if kind in ("and", "or"):
            terms = self._terms(element, path, context, mode, phase, nested)
            if kind == "or" and len(terms) < 2:
                self.report("structure.kind", path)
            return (And if kind == "and" else Or)(terms, path)
        if kind == "not":
            terms = self._terms(element, path, context, mode, phase, nested)
            if len(terms) != 1:
                self.report("structure.not-arity", path)
            return Not(terms[0] if len(terms) == 1 else And(terms, path), path)
        if kind in ("exists", "forall"):
            return self._quantifier(element, path, context, mode, phase, kind)
        if kind == "when":
            condition, then = _find(element, c.EXECUTION_WHEN_CONDITION), _find(element, c.EXECUTION_WHEN_THEN)
            if condition is None or then is None:
                self.report("structure.kind", path)
            return When(
                self._terms(condition, f"{path}/{condition.get('idShort')}", context, "condition", phase, True)
                if condition else (),
                self._terms(then, f"{path}/{then.get('idShort')}", context, "effect", phase, True) if then else (), path)
        outcomes = sorted(((i, o) for i, o in enumerate(_children(element)) if _sid(o) == c.EXECUTION_OUTCOME),
                          key=lambda pair: _named(pair[1], pair[0]))
        if len(outcomes) < 2 or len(outcomes) != len(_children(element)):
            self.report("structure.kind", path)
        return OneOf((_supplemental(element) or [None])[0], tuple(Outcome(
            _named(o, i), (_supplemental(o) or [None])[0],
            self._terms(o, f"{path}/{_named(o, i)}", context, "effect", phase, True), f"{path}/{_named(o, i)}")
            for i, o in outcomes), path)

    def _quantifier(self, element: dict, path: str, context: _Context, mode: str, phase: str, kind: str):
        declared, body = _find(element, c.EXECUTION_QUANTIFIED_VARIABLE), _find(element, c.EXECUTION_BODY)
        variable = None
        if declared is None or body is None:
            self.report("structure.kind", path)
        if declared is not None:
            keys = (_reference_value(declared) or {}).get("keys") or []
            variable = Variable(f"v{context.declared}", _english(declared) or "", keys[0]["value"] if keys else None,
                                f"{path}/{declared.get('idShort')}")
            context.declared += 1
        outer = context.scope
        if variable is not None:
            context.scope = {**outer, variable.path: variable.id}
        terms = self._terms(body, f"{path}/{body.get('idShort')}", context, mode, phase, True) if body else ()
        context.scope = outer
        return (Exists if kind == "exists" else ForAll)(variable, terms, path)

    # ── atoms ────────────────────────────────────────────────────────────────

    def _atom(self, element: dict, path: str, context: _Context, kind: str, phase: str):
        supplemental = _supplemental(element)
        meaning = supplemental[0] if supplemental else None
        if len(supplemental) != 1 or not self.vocabulary.knows(meaning):
            self.report("structure.meaning", path)
        subject = None
        subject_element = _find(element, c.EXECUTION_TERM_SUBJECT)
        if subject_element is None:
            self.report("reference.subject", path)
        else:
            subject = self._subject(subject_element, f"{path}/{subject_element.get('idShort')}", context)
        operator_element = _find(element, c.EXECUTION_CONDITION_OPERATOR) or _find(element, c.EXECUTION_EFFECT_OPERATOR)
        operator = None
        if operator_element is None:
            self.report("structure.kind", path)
        else:
            operator = operator_element.get("value")
            if operator not in (CONDITION_OPERATORS if kind == "condition" else EFFECT_OPERATORS):
                self.report("structure.kind", f"{path}/{operator_element.get('idShort')}")
        operand = self._operand(element, path, context, operator not in NO_OPERAND)
        if kind == "condition":
            rate_element = _find(element, c.EXECUTION_RATE)
            if rate_element is not None:
                self.report("structure.rate", f"{path}/{rate_element.get('idShort')}")
            return Condition(path, meaning, subject, operator, operand)
        rate_element, rate = _find(element, c.EXECUTION_RATE), None
        if rate_element is not None:
            rate_path = f"{path}/{rate_element.get('idShort')}"
            if phase != "end":
                self.report("structure.rate", rate_path)
            rate = self._reference_operand(rate_element, rate_path, context)
        return Effect(path, meaning, subject, operator, operand, rate)

    def _classify(self, target: _Target, context: _Context) -> tuple[str, Optional[object]]:
        """Whether ``target`` is a subject or a variable in scope of this context (``ok``, with the reference), a variable
        out of scope (``scope``), or neither (``other``)."""
        if target.kind == "element" and target.submodel["id"] == context.submodel:
            if target.text in context.scope:
                return "ok", VariableRef(context.scope[target.text])
            if target.text in context.variables:
                return "scope", None
            parent, _, last = target.text.rpartition("/")
            if parent == context.subjects and last.isdigit() and int(last) < context.subject_count:
                return "ok", SubjectRef(int(last))
        return "other", None

    def _subject(self, element: dict, path: str, context: _Context):
        target = self.resolve(_reference_value(element))
        if target is None:
            self.report("reference.unresolved", path)
            return None
        kind, reference = self._classify(target, context)
        if kind != "ok":
            self.report("reference.scope" if kind == "scope" else "reference.subject", path)
        return reference

    def _reference_operand(self, element: dict, path: str, context: _Context) -> Optional[Operand]:
        """A ValueReference (or Rate, or Use): a subject, a variable, an Operation variable, or a constant."""
        target = self.resolve(_reference_value(element))
        if target is None:
            self.report("reference.unresolved", path)
            return None
        kind, reference = self._classify(target, context)
        if kind == "ok":
            return reference
        if kind == "scope" or (len(target.nodes) > 1 and _sid(target.nodes[-2]) == c.EXECUTION_SUBJECTS):
            self.report("reference.scope" if kind == "scope" else "reference.subject", path)
            return None
        if target.operation_at is not None and len(target.path) > target.operation_at + 1:
            return Parameter(self.render(target, target.operation_at + 1, context.aas), target.path[target.operation_at + 1])
        return Element(self.identity(target) if target.kind == "aas" else self.render(target, len(target.path), context.aas))

    def _operand(self, element: dict, path: str, context: _Context, expected: bool) -> Optional[Operand]:
        """The operand of an atom or a Duration: one of Value, ValueReference, Formula (with Uses), and none where
        ``expected`` is false (``measured``, ``unchanged``, ``clear``)."""
        value = _find(element, c.EXECUTION_TERM_VALUE)
        reference = _find(element, c.EXECUTION_TERM_VALUE_REFERENCE)
        formula = _find(element, c.EXECUTION_TERM_FORMULA)
        stated = sum(found is not None for found in (value, reference, formula))
        if stated != (1 if expected else 0):
            self.report("structure.operand", path)
        if value is not None:
            return self._literal(value, f"{path}/{value.get('idShort')}")
        if reference is not None:
            return self._reference_operand(reference, f"{path}/{reference.get('idShort')}", context)
        if formula is not None:
            return self._formula(element, formula, path, context)
        return None

    def _literal(self, element: dict, path: str) -> Optional[Literal]:
        declared = element.get("valueType") or "xs:string"
        raw = element.get("value")
        kind = "number" if declared in NUMBER_TYPES else {"xs:boolean": "boolean", "xs:anyURI": "iri"}.get(declared, "string")
        try:
            if raw is None:
                raise ValueError
            if kind == "number":
                return Literal(kind, float(raw), declared)
            if kind == "boolean":
                return Literal(kind, {"true": True, "1": True, "false": False, "0": False}[str(raw).lower()], declared)
            return Literal(kind, str(raw), declared)
        except (ValueError, KeyError):
            self.report("structure.operand", path)
            return None

    def _formula(self, element: dict, formula: dict, path: str, context: _Context) -> FormulaOperand:
        formula_path = f"{path}/{formula.get('idShort')}"
        text = str(formula.get("value") or "")
        try:
            ast = formulas.parse(text)
        except formulas.FormulaSyntaxError:
            self.report("formula.syntax", formula_path)
            ast = None
        uses_element = _find(element, c.EXECUTION_FORMULA_USES)
        uses = []
        for i, use in enumerate(_children(uses_element) if uses_element else ()):
            if use.get("modelType") == "ReferenceElement":
                name = _named(use, i)
                operand = self._reference_operand(use, f"{path}/{uses_element.get('idShort')}/{name}", context)
                supplemental = _supplemental(use)
                if supplemental and isinstance(operand, (SubjectRef, VariableRef)):
                    operand = State(operand, supplemental[0])
                uses.append((name, operand))
        if ast is not None:
            names = {name for name, _ in uses}
            if any(alias not in names for alias in formulas.aliases(ast)):
                self.report("formula.alias", formula_path)
            used = set(formulas.aliases(ast))
            for name, _ in uses:
                if name not in used:
                    self.report("formula.unused", f"{path}/{uses_element.get('idShort')}/{name}")
        return FormulaOperand(text, ast, tuple(uses))

    # ── knowledge ────────────────────────────────────────────────────────────

    def _knowledge(self, submodel: dict, variable: dict, path: str) -> Knowledge:
        aas = self.owner(submodel["id"])
        supplemental = _supplemental(variable)
        meaning = supplemental[0] if supplemental else None
        if meaning is None:
            self.report("structure.meaning", path)
        subject, subject_element = None, _find(variable, c.STATE_VARIABLE_SUBJECT)
        if subject_element is not None and _reference_value(subject_element):
            reference = _reference_value(subject_element)
            target = self.resolve(reference)
            if target is None:
                self.report("reference.unresolved", f"{path}/{subject_element.get('idShort')}")
            subject = self.identity(target) if target else self.identity_of_keys(reference["keys"])

        def text(semantic_id: str) -> Optional[str]:
            found = _find(variable, semantic_id)
            return None if found is None or found.get("value") is None else str(found["value"])

        initial = _find(variable, c.STATE_VARIABLE_INITIAL)
        mappings = self.mappings.get((aas, submodel["id"], path), 0)
        kind = "observed" if mappings == 1 else "derived" if meaning and self.vocabulary.is_derived(meaning) else "asserted"
        return Knowledge(aas, path, subject, meaning, text(c.STATE_VARIABLE_VALUE_TYPE),
                         text(c.STATE_VARIABLE_CARDINALITY),
                         self._literal(initial, f"{path}/{initial.get('idShort')}") if initial is not None else None,
                         kind, mappings)


# ── Features (section 12, as section 15 refines them) ────────────────────────

def _features(phases: dict, duration: Optional[Operand]) -> tuple[str, ...]:
    found: set[str] = set()

    def operand(value) -> None:
        if isinstance(value, FormulaOperand) and any(isinstance(use, State) for _, use in value.uses):
            found.add("numeric")

    def walk(term, mode: str) -> None:
        if isinstance(term, (Condition, Effect)):
            if term.op in NUMERIC_OPERATORS:
                found.add("numeric")
            if term.op in SET_OPERATORS:
                found.add("set-valued")
            if term.op == "unchanged":
                found.add("locks")
            if term.op == "measured":
                found.add("non-deterministic")
            operand(term.operand)
            if isinstance(term, Effect) and term.rate is not None:
                found.add("continuous")
                operand(term.rate)
        elif isinstance(term, (And, Or, Not)):
            if isinstance(term, Or):
                found.add("disjunctive-conditions")
            if isinstance(term, Not):
                found.add("negative-conditions")
            for part in (term.terms if not isinstance(term, Not) else (term.term,)):
                walk(part, mode)
        elif isinstance(term, (Exists, ForAll)):
            found.add("existential-conditions" if isinstance(term, Exists) else
                      "universal-conditions" if mode == "condition" else "universal-effects")
            for part in term.body:
                walk(part, mode)
        elif isinstance(term, When):
            found.add("conditional-effects")
            for part in term.condition:
                walk(part, "condition")
            for part in term.then:
                walk(part, "effect")
        else:
            found.add("non-deterministic")
            for outcome in term.outcomes:
                for part in outcome.effects:
                    walk(part, "effect")

    for name, terms in phases.items():
        for term in terms:
            walk(term, "condition" if name in ("pre", "invariant", "post") else "effect")
    operand(duration)
    if phases["invariant"] or phases["during"] or phases["start"] or duration is not None:
        found.add("durative")
    return tuple(sorted(found))
