"""[`translation.realized-by`] of the execution model (section 9 of ``docs/execution-model.md``): a capability with a
Translation is realized by exactly one Operation. Whether its Call and Reply fit that Operation ([`translation.coverage`]) is
not modreg's to check: the Translations are the behaviour tree's, and the line planner's compiler checks them where it copies
them into the tree, as the hosts that run them check the shared test vectors.
"""
from __future__ import annotations

from typing import Optional

from modplan.execution import Capability, Finding

from .environment import Environment, children


def check(capability: Capability, environment: Environment) -> list[Finding]:
    """[`translation.realized-by`] for a capability with a Translation."""
    translation = capability.translation
    if translation is None or _operation(capability, environment) is not None:
        return []
    return [Finding("translation.realized-by", translation.path, "error")]


def _operation(capability: Capability, environment: Environment) -> Optional[dict]:
    """The one Operation of the one skill that realizes the capability."""
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
    return operations[0] if len(operations) == 1 else None
