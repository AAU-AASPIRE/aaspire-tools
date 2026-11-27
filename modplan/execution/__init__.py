"""The shared execution package (section 2 of ``docs/execution-model.md``): the reader of the execution model, and the formula
parser it and the validator share. It reads AAS JSON (the form a server or a file gives), not the pydantic templates, and
depends on aas-model only for its constants; the lab vocabulary is read from the ontologies beside it.

    reading = read(environment)            # environment: the dict of an AAS JSON file
    reading.capabilities, reading.goal, reading.knowledge, reading.findings
    reading.to_canonical()                 # section 15, as JSON
"""

from modplan.execution.formula import FormulaSyntaxError, aliases, degree, parse
from modplan.execution.model import (
    And, Capability, CapabilityProperty, Condition, Effect, Element, Exists, Finding, FixedSubject, ForAll,
    FormulaOperand, Goal, Knowledge, Literal, ModelReference, Not, OneOf, Or, Outcome, Parameter, Reading,
    RequirementSubject, RuntimeSubject, State, SubjectRef, Translation, Variable, VariableRef, When, compact,
)
from modplan.execution.read import read
from modplan.execution.vocabulary import Vocabulary, VocabularyProperty

__all__ = [
    "And", "Capability", "CapabilityProperty", "Condition", "Effect", "Element", "Exists", "Finding", "FixedSubject",
    "ForAll", "FormulaOperand", "FormulaSyntaxError", "Goal", "Knowledge", "Literal", "ModelReference", "Not", "OneOf",
    "Or", "Outcome", "Parameter", "Reading", "RequirementSubject", "RuntimeSubject", "State", "SubjectRef", "Translation",
    "Variable", "VariableRef", "Vocabulary", "VocabularyProperty", "When", "aliases", "compact", "degree", "parse", "read",
]
