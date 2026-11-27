"""The language of a constraint: what a BasicConstraint holds (IDTA 02020; ARSO 0.11).

    <op> <operand>   op one of = != < <= > >= +=; the operand a number, true or false, the idShort
                     of another property of the capability (a parameter, a named value or a state),
                     or a linear formula of them
    given            (Post) the property ends at the value the capability was given
    measured         (Post) the property ends at what the capability measured
    unchanged        (Invariant) nothing else changes the property while the capability runs

A **formula** (since 6 Nov) is numbers and idShorts with + - * / and brackets, and ``pi``:
``= LinearAxisPosition - NestPosition + 1000 * ContentVolume / (pi * InnerDiameter * InnerDiameter / 4)``.
It has to be linear in the states the capabilities change (a product of two of them, or a division
by one, is refused when the domain is read, ``Formula.degree``): with every rate constant between two
events, a linear condition holds over a stretch of time if it holds at both its ends, which is what
lets the checker look at the events only. An Invariant ``=`` a formula about a property nothing else
sets defines it while the capability runs (a derived quantity: how deep the needle is in the liquid).

A planner achieves ``=`` and ``+=`` (it finds a capability whose Post makes them hold) and only
checks the other comparisons.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from .execution import formula as grammar

OPERATORS = ("+=", "!=", "<=", ">=", "=", "<", ">")      # longest first, so that "<=" is not "<"
COMPARISONS = ("=", "!=", "<", "<=", ">", ">=")
WORDS = ("given", "measured", "unchanged")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
CONSTANTS = {"pi": math.pi}
# What each kind of constraint (PropertyConditionalType) may say.
ALLOWED = {"Pre": COMPARISONS, "Post": ("=", "+=", "given", "measured"), "Invariant": (*COMPARISONS, "unchanged")}


@dataclass(frozen=True)
class Formula:
    """A formula of numbers and names: a tree of (op, left, right), a number, or a name."""
    tree: tuple | float | str
    text: str

    def __str__(self) -> str:
        return self.text

    @property
    def names(self) -> tuple[str, ...]:
        """The idShorts it names, in order, each once (``pi`` is not one)."""
        found: list[str] = []

        def walk(node):
            if isinstance(node, str):
                if node not in CONSTANTS and node not in found:
                    found.append(node)
            elif isinstance(node, tuple):
                for part in node[1:]:
                    walk(part)
        walk(self.tree)
        return tuple(found)

    def value(self, lookup) -> float | None:
        """Its value, ``lookup`` giving each name's (None: not known, and then neither is it)."""
        def ev(node):
            if isinstance(node, float):
                return node
            if isinstance(node, str):
                found = CONSTANTS[node] if node in CONSTANTS else lookup(node)
                return float(found) if isinstance(found, (int, float)) and not isinstance(found, bool) else None
            op, *parts = node
            values = [ev(p) for p in parts]
            if any(v is None for v in values):
                return None
            if op == "neg":
                return -values[0]
            a, b = values
            if op == "/":
                return a / b if b != 0 else None
            return {"+": a + b, "-": a - b, "*": a * b}[op]
        return ev(self.tree)

    def degree(self, varying) -> int:
        """Its degree in the names ``varying`` (0: a constant, 1: linear; a division by one: 2)."""
        def deg(node):
            if isinstance(node, float):
                return 0
            if isinstance(node, str):
                return 1 if node in varying else 0
            op, *parts = node
            ds = [deg(p) for p in parts]
            if op == "neg":
                return ds[0]
            if op in "+-":
                return max(ds)
            if op == "*":
                return ds[0] + ds[1]
            return ds[0] if ds[1] == 0 else 2                 # a division by what varies is not linear
        return deg(self.tree)


@dataclass(frozen=True)
class Expression:
    op: str                                      # an operator, or one of WORDS
    operand: float | bool | str | Formula | None = None    # a value, the name of a property, or a formula

    @property
    def named(self) -> str | None:
        """The property the operand names, if it names one."""
        return self.operand if isinstance(self.operand, str) else None

    @property
    def names(self) -> tuple[str, ...]:
        """Every property the operand names: its one name, or the names in its formula."""
        if isinstance(self.operand, Formula):
            return self.operand.names
        return (self.operand,) if isinstance(self.operand, str) else ()

    def __str__(self) -> str:
        if self.op in WORDS:
            return self.op
        value = str(self.operand).lower() if isinstance(self.operand, bool) else self.operand
        return f"{self.op} {value}"


def parse(text: str, kind: str | None = None) -> Expression:
    """The expression a constraint's text states; ``kind`` (Pre, Post, Invariant): it has to be one
    that kind of constraint may say. ValueError for anything else."""
    found = _parse(text.strip())
    if kind is not None and found.op not in ALLOWED[kind]:
        raise ValueError(f"a {kind} constraint cannot say {text!r}")
    return found


def _parse(text: str) -> Expression:
    if text in WORDS:
        return Expression(text)
    op = next((op for op in OPERATORS if text.startswith(op)), None)
    if op is None:
        raise ValueError(f"not a constraint: {text!r}")
    rest = text[len(op):].strip()
    if rest in ("true", "false"):
        return Expression(op, rest == "true")
    if NAME.match(rest) and rest not in CONSTANTS:
        return Expression(op, rest)
    try:
        return Expression(op, float(rest))
    except ValueError:
        pass
    try:
        return Expression(op, formula(rest))
    except ValueError as e:
        raise ValueError(f"not a constraint: {text!r} ({e})") from None


def formula(text: str) -> Formula:
    """The formula ``text`` states: numbers, names, + - * /, brackets, in the grammar of the execution model
    (``execution.formula``, the one parser of both), as a tree of (op, left, right), numbers and names."""
    try:
        node = grammar.parse(text)
    except grammar.FormulaSyntaxError as e:
        raise ValueError(e.message) from None
    return Formula(_tree(node), " ".join(text.split()))


def _tree(node) -> tuple | float | str:
    if isinstance(node, grammar.Number):
        return float(node.value)
    if isinstance(node, grammar.Pi):
        return "pi"
    if isinstance(node, grammar.Alias):
        return node.name
    if isinstance(node, grammar.Neg):
        return ("neg", _tree(node.operand))
    return (node.op, _tree(node.left), _tree(node.right))
