"""The native building proposal: schema, validator and mapper (#1417).

A proposal is one JSON document that an agent (or a person) writes to
describe a building. It is not a model: the proposal is compiled into the
canonical ``BuildingModel`` and then discarded, and the proposal itself
survives only as provenance.

    >>> from proposal import validate_proposal, map_proposal
    >>> validation = validate_proposal(doc)          # doctest: +SKIP
    >>> validation.ok                                 # doctest: +SKIP
    True
    >>> model, report = map_proposal(doc)             # doctest: +SKIP

The three pieces:

``validate_proposal``
    a pure function from a parsed document to diagnostics (errors and
    warnings with stable machine codes),
``map_proposal``
    the deterministic compiler from a valid document to the canonical
    model, plus the report of what it did with every element, and
``proposal_json_schema``
    the JSON Schema (draft 2020-12) of the same contract, generated from
    the field tables the validator reads.
"""

from .mapper import LevelCounts, MappingReport, ReportEntry, map_proposal
from .schema import (
    JSON_SCHEMA_DIALECT,
    JSON_SCHEMA_ID,
    PROPOSAL_VERSION,
    SCHEMA_ID,
    UNITS,
    proposal_json_schema,
)
from .validate import (
    ERROR_CODES,
    WARNING_CODES,
    Diagnostic,
    ProposalValidation,
    validate_proposal,
)

__all__ = [
    "Diagnostic",
    "ERROR_CODES",
    "JSON_SCHEMA_DIALECT",
    "JSON_SCHEMA_ID",
    "LevelCounts",
    "MappingReport",
    "PROPOSAL_VERSION",
    "ProposalValidation",
    "ReportEntry",
    "SCHEMA_ID",
    "UNITS",
    "WARNING_CODES",
    "map_proposal",
    "proposal_json_schema",
    "validate_proposal",
]
