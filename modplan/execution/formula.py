"""The formulas of the execution model (section 5.5 of ``docs/execution-model.md``).

    Formula ::= Term (("+" | "-") Term)*
    Term    ::= Factor (("*" | "/") Factor)*
    Factor  ::= Number | "pi" | Alias | "(" Formula ")" | "-" Factor
    Number  ::= Digit+ ("." Digit+)? ([eE] [+-]? Digit+)?
    Alias   ::= [A-Za-z_] [A-Za-z0-9_]*          an idShort in the term's Uses; "pi" is not an alias

Whitespace between tokens is ignored. ``parse`` gives the tree section 15 writes as JSON (``to_canonical``), operators
left-associative as parsed; a formula that does not parse raises a ``FormulaSyntaxError`` that says where.
``aliases`` are the names a formula uses and ``degree`` its degree in some of them, which is what the linearity rule of
the validator (``formula.linear``) asks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Union

_TOKEN = re.compile(r"\s*(?:(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<alias>[A-Za-z_][A-Za-z0-9_]*)|(?P<other>\S))")


class FormulaSyntaxError(ValueError):
    """A formula that is not one; ``position`` is the offset (from 0) of the character where it went wrong."""

    def __init__(self, message: str, position: int):
        super().__init__(f"{message} (at {position})")
        self.message, self.position = message, position


@dataclass(frozen=True)
class Number:
    value: float

    def to_canonical(self) -> dict:
        return {"number": self.value}


@dataclass(frozen=True)
class Pi:
    def to_canonical(self) -> dict:
        return {"pi": True}


@dataclass(frozen=True)
class Alias:
    name: str

    def to_canonical(self) -> dict:
        return {"alias": self.name}


@dataclass(frozen=True)
class Neg:
    operand: "Node"

    def to_canonical(self) -> dict:
        return {"neg": self.operand.to_canonical()}


@dataclass(frozen=True)
class Binary:
    op: str                     # + - * /
    left: "Node"
    right: "Node"

    def to_canonical(self) -> dict:
        return {self.op: [self.left.to_canonical(), self.right.to_canonical()]}


Node = Union[Number, Pi, Alias, Neg, Binary]


def _tokens(text: str) -> list[tuple[str, str, int]]:
    found, at = [], 0
    while at < len(text):
        match = _TOKEN.match(text, at)
        if match is None:                                   # only trailing whitespace is left
            break
        kind = match.lastgroup
        if kind == "other" and match.group(kind) not in "+-*/()":
            raise FormulaSyntaxError(f"{match.group(kind)!r} is not part of a formula", match.start(kind))
        found.append((kind, match.group(kind), match.start(kind)))
        at = match.end()
    return found


class _Parser:
    def __init__(self, text: str):
        self.tokens, self.at, self.end = _tokens(text), 0, len(text)

    def peek(self) -> tuple[str | None, str | None, int]:
        return self.tokens[self.at] if self.at < len(self.tokens) else (None, None, self.end)

    def take(self) -> tuple[str, str, int]:
        self.at += 1
        return self.tokens[self.at - 1]

    def is_symbol(self, *symbols: str) -> bool:
        kind, value, _ = self.peek()
        return kind == "other" and value in symbols

    def formula(self) -> Node:
        node = self.term()
        while self.is_symbol("+", "-"):
            node = Binary(self.take()[1], node, self.term())
        return node

    def term(self) -> Node:
        node = self.factor()
        while self.is_symbol("*", "/"):
            node = Binary(self.take()[1], node, self.factor())
        return node

    def factor(self) -> Node:
        kind, value, position = self.peek()
        if kind is None:
            raise FormulaSyntaxError("the formula ends where a value belongs", position)
        self.take()
        if kind == "number":
            return Number(float(value))
        if kind == "alias":
            return Pi() if value == "pi" else Alias(value)
        if value == "-":
            return Neg(self.factor())
        if value == "(":
            node = self.formula()
            if not self.is_symbol(")"):
                raise FormulaSyntaxError("a bracket is not closed", self.peek()[2])
            self.take()
            return node
        raise FormulaSyntaxError(f"{value!r} where a value belongs", position)


def parse(text: str) -> Node:
    """The tree of ``text``; a FormulaSyntaxError where it is not a formula."""
    parser = _Parser(text)
    node = parser.formula()
    kind, value, position = parser.peek()
    if kind is not None:
        raise FormulaSyntaxError(f"{value!r} after the end of the formula", position)
    return node


def aliases(node: Node) -> tuple[str, ...]:
    """The aliases the formula names, in order of appearance, each once (``pi`` is none)."""
    found: list[str] = []

    def walk(n: Node) -> None:
        if isinstance(n, Alias) and n.name not in found:
            found.append(n.name)
        elif isinstance(n, Neg):
            walk(n.operand)
        elif isinstance(n, Binary):
            walk(n.left)
            walk(n.right)

    walk(node)
    return tuple(found)


def degree(node: Node, varying: Iterable[str]) -> int:
    """The degree of the formula in the aliases ``varying``: 0 for a constant, 1 for a linear formula; a product of two
    of them has degree 2, and so has a division by one."""
    varying = frozenset(varying)

    def deg(n: Node) -> int:
        if isinstance(n, Alias):
            return int(n.name in varying)
        if isinstance(n, Neg):
            return deg(n.operand)
        if isinstance(n, Binary):
            left, right = deg(n.left), deg(n.right)
            if n.op in "+-":
                return max(left, right)
            if n.op == "*":
                return left + right
            return left if right == 0 else 2
        return 0

    return deg(node)
