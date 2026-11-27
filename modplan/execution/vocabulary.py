"""What the lab vocabulary says of the properties a term can be about: their value type and cardinality (plan D14), and
whether they are derived (``lab:derived``, D36). It is read from the vocabulary's Turtle (AAU-AASPIRE/ontologies,
``Vocabulary/capabilities.ttl``, beside this repository, or ``$LAB_VOCABULARY``), with rdflib, which only this reading needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Mapping, Optional

from aas_model.constants import LAB

# The ontologies beside this repository, or $AASPIRE_ONTOLOGIES, as modplan.vocabulary finds them.
ONTOLOGY = Path(os.environ.get("AASPIRE_ONTOLOGIES") or Path(__file__).resolve().parents[3] / "ontologies")
DEFAULT_FILE = ONTOLOGY / "Vocabulary" / "capabilities.ttl"


@dataclass(frozen=True)
class VocabularyProperty:
    value_type: Optional[str]
    cardinality: Optional[str]
    derived: bool = False


@dataclass(frozen=True)
class Vocabulary:
    """The properties of the vocabulary by IRI. A vocabulary that lists none (``properties`` empty: the lab's was not found)
    knows no property, so it refuses none."""
    properties: Mapping[str, VocabularyProperty] = field(default_factory=dict)

    def knows(self, iri: str) -> bool:
        """Whether ``iri`` is a property of the vocabulary (any, where it has none to check against)."""
        return not self.properties or iri in self.properties

    def is_derived(self, iri: str) -> bool:
        found = self.properties.get(iri)
        return found is not None and found.derived

    @classmethod
    def from_turtle(cls, *files: Path | str) -> "Vocabulary":
        """The properties (the things with a ``lab:valueType``) of the Turtle ``files``."""
        import rdflib

        graph = rdflib.Graph()
        for file in files:
            graph.parse(file)
        lab = rdflib.Namespace(LAB)

        def text(subject, predicate) -> Optional[str]:
            value = graph.value(subject, predicate)
            return None if value is None else str(value)

        def derived(subject) -> bool:
            value = graph.value(subject, lab.derived)
            return value is not None and bool(value.toPython())

        return cls({str(s): VocabularyProperty(str(value_type), text(s, lab.cardinality), derived(s))
                    for s, value_type in graph.subject_objects(lab.valueType)})

    @classmethod
    @cache
    def default(cls) -> "Vocabulary":
        """The lab vocabulary beside this repository (or ``$LAB_VOCABULARY``); an empty one without it or without rdflib."""
        file = Path(os.environ.get("LAB_VOCABULARY") or DEFAULT_FILE)
        if not file.is_file():
            return cls()
        try:
            return cls.from_turtle(file)
        except ImportError:
            return cls()
