"""The Translations of the execution model (section 9 of ``docs/execution-model.md``): is a capability that the behaviour tree
runs realized by one Operation, and do its Call and Reply scripts fit that Operation and the capability.

The scripts are Lua 5.2 and run in the sandbox the DMP gives them (D38: the base functions, ``math``, ``string`` and ``table``,
no ``load``, ``io`` or ``os``), on vectors made from the model: the subjects of the capability (what a requirement asks of the
unit is the range 1 to 3), the properties it states, and what the Operation answers (every number at 0, 2 and 4 in turn, ``ErrorID`` at
each error the Skills submodel declares). The scripts are not read: what ``Reply`` makes of an error code is in the script (a table
from codes to names), so only running it shows the names it can answer with. A script that raises on a vector is not judged by it:
the vectors are the model's guess at what the tree sends.
"""
from __future__ import annotations

import itertools
import json
from functools import cache
from typing import Optional

from modplan.execution import Capability, Finding, FixedSubject, OneOf, RuntimeSubject, Vocabulary
from modplan.execution.model import And, ForAll

from .environment import Environment, children

CODE = "translation.coverage"
NUMBER_TYPES = ("xs:double", "xs:float", "xs:decimal")
INTEGER_TYPES = ("xs:integer", "xs:int", "xs:long", "xs:short", "xs:byte")
UNIT = "urn:uuid:00000000-0000-4000-8000-000000000000"
PRODUCT = "https://smartproductionlab.aau.dk/Product"
SWEEP = (0.0, 2.0, 4.0)
REQUIRED = {"min": 1.0, "max": 3.0}

SANDBOX = """
local json_decode, json_encode = ...
local function limited(fn, ...)
  debug.sethook(function() error("the script runs too long", 2) end, "", 2000000)
  local ok, result = pcall(fn, ...)
  debug.sethook()
  if not ok then error(result, 0) end
  return result
end
local function load_script(code, name)
  local env = { assert = assert, error = error, ipairs = ipairs, next = next, pairs = pairs, pcall = pcall,
                select = select, tonumber = tonumber, tostring = tostring, type = type,
                math = math, string = string, table = table,
                json = { decode = json_decode, encode = json_encode } }
  local chunk, err = load(code, name, "t", env)
  if not chunk then error(err, 0) end
  chunk()
  return env
end
return load_script, limited
"""


class Unloadable(Exception):
    """A script that does not load, or lacks the function the tree calls."""


class _Lua:
    """One Lua 5.2 runtime and the sandbox that loads a script into it."""

    def __init__(self):
        import lupa.lua52 as lupa
        self.lupa = lupa
        self.runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.load, self.limited = self.runtime.execute(
            SANDBOX, lambda text: self.table(json.loads(text)), lambda value: json.dumps(self.python(value)))

    def table(self, value):
        if isinstance(value, dict):
            return self.runtime.table_from({k: self.table(v) for k, v in value.items()})
        if isinstance(value, list):
            return self.runtime.table_from([self.table(v) for v in value])
        return value

    def python(self, value):
        if self.lupa.lua_type(value) == "table":
            items = {k: self.python(v) for k, v in value.items()}
            return list(items.values()) if items and list(items) == list(range(1, len(items) + 1)) else items
        return value

    def run(self, source: str, function: str, *arguments):
        """What ``function`` of the script makes of the arguments, as Python values; Unloadable where the script does not load
        or has no such function, a LuaError where the function raises."""
        try:
            env = self.load(source, function)
        except self.lupa.LuaError as error:
            raise Unloadable(str(error)) from error
        if self.lupa.lua_type(env[function]) != "function":
            raise Unloadable(f"no function {function}")
        return self.python(self.limited(env[function], *(self.table(a) for a in arguments)))


@cache
def lua() -> _Lua:
    return _Lua()


def check(capability: Capability, environment: Environment, vocabulary: Vocabulary) -> list[Finding]:
    """The findings of a capability's Translation: [`translation.realized-by`] one Operation realizes it,
    [`translation.coverage`] its Call sends what that Operation takes and its Reply names only outcomes and errors declared."""
    translation = capability.translation
    if translation is None:
        return []
    operation = _operation(capability, environment)
    if operation is None:
        return [Finding("translation.realized-by", translation.path, "error")]
    skills, node = operation
    errors = _errors(skills)
    return [Finding(CODE, f"{translation.path}/{name}", "error")
            for name, source, rule in (("Call", translation.call, _call), ("Reply", translation.reply, _reply))
            if not rule(source, capability, node, errors, vocabulary)]


def _operation(capability: Capability, environment: Environment) -> Optional[tuple[dict, dict]]:
    """The Skills submodel and the one Operation of the one skill that realizes the capability."""
    if len(capability.realized_by) != 1:
        return None
    nodes = environment.resolve(capability.realized_by[0].keys)
    if nodes is None:
        return None
    operations, pending = [], [nodes[-1]]
    while pending:
        element = pending.pop()
        if element.get("modelType") == "Operation":
            operations.append(element)
        else:
            pending.extend(children(element))
    return (nodes[0], operations[0]) if len(operations) == 1 else None


# ── Vectors ──────────────────────────────────────────────────────────────────

def _sample(value_type: Optional[str]):
    return {"number": 1.0, "boolean": True, "reference": UNIT}.get(value_type, "x")


def _subjects(capability: Capability, vocabulary: Vocabulary) -> list[dict]:
    """The subjects as the tree hands them to a script: an identity, the state it knows of and, for a requirement, its value."""
    state = {iri: _sample(found.value_type) for iri, found in vocabulary.properties.items()}
    subjects = []
    for subject in capability.subjects:
        if isinstance(subject, FixedSubject):
            subjects.append({"id": subject.target, "state": state})
        elif isinstance(subject, RuntimeSubject):
            subjects.append({"id": UNIT if subject.stands_for == PRODUCT else subject.stands_for, "state": state})
        else:
            subjects.append({"id": subject.meaning, "state": state, "value": REQUIRED})
    return subjects


def _properties(capability: Capability) -> dict:
    return {p.id_short: p.value.value for p in capability.properties if p.value is not None}


def _variables(operation: dict, *kinds: str) -> dict[str, dict]:
    return {v["value"]["idShort"]: v["value"] for kind in kinds for v in operation.get(kind) or () if v.get("value")}


def _optional(variable: dict) -> bool:
    return any(q.get("type") == "Optional" and str(q.get("value")).lower() == "true" for q in variable.get("qualifiers") or ())


def _outcomes(capability: Capability) -> set[str]:
    found: set[str] = set()

    def walk(terms) -> None:
        for term in terms:
            if isinstance(term, OneOf):
                found.update(outcome.id for outcome in term.outcomes)
            elif isinstance(term, And):
                walk(term.terms)
            elif isinstance(term, ForAll):
                walk(term.body)

    walk(capability.end)
    return found


def _errors(skills: dict) -> dict[str, int]:
    """The errors the Skills submodel declares, by name, with the ``ErrorID`` each stands for."""
    errors = next((e for e in skills.get("submodelElements") or () if e.get("idShort") == "Errors"), None)
    return {e["idShort"]: int(e["value"]) for e in children(errors or {}) if str(e.get("value", "")).lstrip("-").isdigit()}


# ── The Call ─────────────────────────────────────────────────────────────────

def _call(source: Optional[str], capability: Capability, operation: dict, errors: dict, vocabulary: Vocabulary) -> bool:
    """Whether the Call sends only what the Operation takes, and all it needs."""
    if source is None:
        return False
    try:
        sent = lua().run(source, "call", _subjects(capability, vocabulary), _properties(capability))
    except Unloadable:
        return False
    except lua().lupa.LuaError as error:
        return _undecided(error)
    if not isinstance(sent, dict):
        return False
    takes = _variables(operation, "inputVariables", "inoutVariables")
    required = {name for name, variable in takes.items() if not _optional(variable)}
    return set(sent) <= set(takes) and required <= set(sent)


# ── The Reply ────────────────────────────────────────────────────────────────

def _reply(source: Optional[str], capability: Capability, operation: dict, errors: dict, vocabulary: Vocabulary) -> bool:
    """Whether the Reply, on the answers the Operation can give, names only declared outcomes and errors."""
    if source is None:
        return False
    outcomes = _outcomes(capability)
    for outputs in _answers(_variables(operation, "outputVariables", "inoutVariables"), errors):
        try:
            result = lua().run(source, "reply", outputs, _subjects(capability, vocabulary), _properties(capability))
        except Unloadable:
            return False
        except lua().lupa.LuaError as error:
            if _undecided(error):
                continue
            return False
        if not _names_declared(result, outcomes, errors):
            return False
    return True


def _answers(answers: dict[str, dict], errors: dict[str, int]):
    """The outputs the Operation can answer with: success with every number swept through
    ``SWEEP``, and a failure for each declared error code (any, where it declares none)."""
    numbers = [n for n, v in answers.items() if n != "ErrorID" and v.get("valueType") in NUMBER_TYPES + INTEGER_TYPES]
    base = {n: _zero(v) for n, v in answers.items()}
    for values in itertools.product(SWEEP, repeat=min(len(numbers), 3)):
        yield {**base, **dict(zip(numbers, values))}
    if "ErrorID" in answers:
        for code in errors.values() or (1,):
            yield {**base, "ErrorID": code, **{n: SWEEP[1] for n in numbers}}


def _zero(variable: dict):
    kind = variable.get("valueType")
    return 0.0 if kind in NUMBER_TYPES else 0 if kind in INTEGER_TYPES else False if kind == "xs:boolean" else ""


def _names_declared(result, outcomes: set[str], errors: dict[str, int]) -> bool:
    """Whether a Reply's result is {ok, outcome} or {ok, error}, naming only what is declared."""
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        return False
    if result.get("error") is not None and result["error"] not in errors:
        return False
    if not result["ok"]:
        return True
    return result.get("outcome") in outcomes if outcomes else result.get("outcome") is None


def _undecided(error: Exception) -> bool:
    """A script that raises on a vector is not judged by it, unless it never ends."""
    return "runs too long" not in str(error)
