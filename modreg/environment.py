"""An AAS environment (the JSON of ``assetAdministrationShells`` and ``submodels``) as the checks of the execution model need
it: its submodels by id, which AAS holds each, and a ModelReference followed down to the element it names."""
from __future__ import annotations

from typing import Optional


def children(element: dict) -> list[dict]:
    """The elements a collection or list holds, an Operation's variables, or a submodel's elements."""
    if "submodelElements" in element:
        return element["submodelElements"] or []
    if element.get("modelType") == "Operation":
        return [v["value"] for kind in ("inputVariables", "outputVariables", "inoutVariables")
                for v in element.get(kind) or () if v.get("value")]
    value = element.get("value")
    return value if element.get("modelType") in ("SubmodelElementCollection", "SubmodelElementList") \
        and isinstance(value, list) else []


def semantic_id(element: dict) -> Optional[str]:
    keys = (element.get("semanticId") or {}).get("keys")
    return keys[0]["value"] if keys else None


class Environment:
    """The submodels of a document by id, the AAS that hold them, and the AAS ids."""

    def __init__(self, document: dict):
        self.document = document
        self.submodels = {s["id"]: s for s in document.get("submodels") or ()}
        self.aases = {a["id"] for a in document.get("assetAdministrationShells") or ()}
        self.owners: dict[str, list[str]] = {}
        for aas in document.get("assetAdministrationShells") or ():
            for reference in aas.get("submodels") or ():
                if reference.get("keys"):
                    self.owners.setdefault(reference["keys"][-1]["value"], []).append(aas["id"])

    def holds(self, identity: str) -> bool:
        """Whether the AAS or submodel an identity names (an AAS id, or ``<submodel id>#<idShortPath>``) is in the environment."""
        head = identity.split("#", 1)[0]
        return head in self.aases or head in self.submodels

    def resolve(self, keys) -> Optional[list[dict]]:
        """The elements from the submodel down to the one the keys (type, value) name, or None where one is not there."""
        keys = list(keys)
        while keys and keys[0][0] == "AssetAdministrationShell":
            keys.pop(0)
        if not keys or keys[0][1] not in self.submodels:
            return None
        nodes = [self.submodels[keys[0][1]]]
        for _, value in keys[1:]:
            parent = nodes[-1]
            below = children(parent)
            if parent.get("modelType") == "SubmodelElementList":
                found = below[int(value)] if value.isdigit() and int(value) < len(below) else None
            else:
                found = next((child for child in below if child.get("idShort") == value), None)
            if found is None:
                return None
            nodes.append(found)
        return nodes
