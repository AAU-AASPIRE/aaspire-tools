"""The pieces modreg authors a resource's AAS from that aas-model has no function for: an element put under
its key, a delegated Operation's qualifiers, the AIMC's sources, sinks and mappings, and the time series of
the Variables, each built from aas-model's classes."""
from __future__ import annotations

from aas_model.submodel_templates import (
    AimcMappingConfiguration, DmpEvaluation, DmpEvaluationMode, DmpResponseSink, DmpResponseSource,
)
from aas_model.submodel_templates.aimc import DmpSink, DmpSource
from aas_model.submodel_templates.control_component_instance import InvocationDelegation
from aas_model.submodel_templates.time_series import (
    DmpLinkedSegment, InfluxDb2Target, RecordEntry, RecordField, RecordReference, TargetWriteMode,
    TimeSeriesCollection, TimeSeriesMetadata, TimeSeriesRecord, TimeSeriesSegments, WriteModeValue,
)
from aas_pydantic import ModelReference, Qualifier, SubmodelElement
from aas_pydantic.submodel_templates.time_series import Name
from pydantic import BaseModel


def put(container, key: str, element: SubmodelElement) -> SubmodelElement:
    """Assign ``element`` into ``container`` under ``key``, its id_short the key: a field of a model, or an
    entry of a dictionary."""
    element.id_short = key
    if isinstance(container, BaseModel):
        setattr(container, key, element)
    else:
        container[key] = element
    return element


def delegated(synchronous: bool = True) -> list[Qualifier]:
    """The qualifiers of an Operation the AAS server delegates to the DMP (its URL is filled in when the AAS
    is built), and whether the call waits for the skill's answer."""
    return [InvocationDelegation(value_type="xs:string"),
            Qualifier(type_="Synchronous" if synchronous else "Asynchronous", value=str(synchronous).lower(),
                      value_type="xs:string", kind="ConceptQualifier")]


def source(name: str, ref: ModelReference, evaluation: str | None = None) -> DmpSource:
    """A source of a mapping, under the id of its name; ``evaluation`` is its Mode (OnUpdate, OnChange, Cached,
    ReadAtEvaluation), without one it is OnUpdate."""
    return DmpSource(id_short=name, Source=ref, SourceId=name,
                     Evaluation=DmpEvaluation(Mode=DmpEvaluationMode(value=evaluation)) if evaluation else None)


def sink(name: str, ref: ModelReference) -> DmpSink:
    """A sink of a mapping, under the id of its name."""
    return DmpSink(id_short=name, Sink=ref, SinkId=name)


def response_source(source_id: str, ref: ModelReference) -> DmpResponseSource:
    """An element of the action's reply, as the response transformation reads it."""
    return DmpResponseSource(id_short=source_id, Source=ref, SourceId=source_id)


def response_sink(sink_id: str, ref: ModelReference) -> DmpResponseSink:
    """Where a result of the response transformation is written."""
    return DmpResponseSink(id_short=sink_id, Sink=ref, SinkId=sink_id)


def mapping_configuration(*, id_short: str, sources: list[DmpSource], sinks: list[DmpSink], transformation: str,
                          response_sources: list[DmpResponseSource] | None = None,
                          response_sinks: list[DmpResponseSink] | None = None,
                          response_transformation: str | None = None) -> AimcMappingConfiguration:
    """One mapping: the sources of the interface, the sinks in the AAS and the Lua between them. The response
    arguments apply to the mapping of an Operation."""
    extra = {}
    if response_sources is not None:
        extra["ResponseSources"] = response_sources
    if response_sinks is not None:
        extra["ResponseSinks"] = response_sinks
    if response_transformation is not None:
        extra["ResponseTransformation"] = response_transformation
    return AimcMappingConfiguration(id_short=id_short, DefaultPollingInterval="0.0", Transformation=transformation,
                                    Sources=sources, Sinks=sinks, **extra)


def record_field(value_type: str, *, concept: str = "", description: str = "") -> RecordField:
    """A field of a series' row; what it shows is its supplemental semantic id."""
    return RecordField(value_type=value_type, description=description,
                       supplemental_semantic_ids=[concept] if concept else [])


def time_series(*, name: str, measurement: str, fields: dict[str, RecordEntry], description: str = "",
                segment: str = "Live", write_mode: WriteModeValue | None = None) -> TimeSeriesCollection:
    """A series with one segment, tagged with the AAS that owns it."""
    target = InfluxDb2Target(Measurement=measurement, WriteMode=TargetWriteMode(value=write_mode) if write_mode else None)
    return TimeSeriesCollection(
        description=description,
        Metadata=TimeSeriesMetadata(Name=Name(value={"en": name}), Record=TimeSeriesRecord(field=fields)),
        Segments=TimeSeriesSegments(LinkedSegment={segment: DmpLinkedSegment(InfluxDb2Target=target)}))


def operation_log(operation: ModelReference, *, name: str, measurement: str) -> TimeSeriesCollection:
    """The log series of an Operation: how each call ended and how long it took, and the Operation's own
    variables, referenced, so they follow its definition."""
    return time_series(name=name, measurement=measurement, fields={
        "Outcome": record_field("xs:string", description="How the invocation ended"),
        "DurationMs": record_field("xs:long", description="Time from accepting the call to its answer"),
        "Operation": RecordReference(value=operation, description="The invoked Operation's variables"),
    })
