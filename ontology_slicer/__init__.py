"""公开接口。

    from ontology_slicer import Ontology, slice_terms

    ont = Ontology.load("fixtures/ontology.json")
    out = slice_terms(ont, ["成交额"], dim_terms=["城市"], subject="role:hq")
"""

from __future__ import annotations

from .errors import SliceError
from .ontology import ONTOLOGY_VERSION, MetricDef, Ontology, PropertyDef
from .view import slice_terms, slice_view, view_hash

__all__ = [
    "ONTOLOGY_VERSION",
    "MetricDef",
    "Ontology",
    "PropertyDef",
    "SliceError",
    "slice_terms",
    "slice_view",
    "view_hash",
]

__version__ = "1.0.0"
