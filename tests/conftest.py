from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ontology_slicer import Ontology  # noqa: E402

FIXTURE = ROOT / "fixtures" / "ontology.json"


@pytest.fixture(scope="session")
def ont() -> Ontology:
    return Ontology.load(FIXTURE)


def base_ontology() -> dict:
    """一份最小可用的本体，测试里按需改坏它。"""
    return {
        "version": "1.1",
        "capabilities": {
            "supports_compare": ["yoy"],
            "min_granularity": "day",
            "max_scan_days": 365,
            "dialects_verified": ["sqlite"],
        },
        "tables": [
            {"name": "f1", "kind": "fact"},
            {"name": "d1", "kind": "dimension"},
        ],
        "relations": [
            {"from": "f1", "to": "d1", "on": "k", "cardinality": "many_to_one"}
        ],
        "metrics": [
            {
                "name": "m1",
                "display": "M1",
                "domain": "x",
                "aliases": ["一号"],
                "visibility_scope": ["*"],
                "dimension_policy": {"mode": "all"},
                "supports_compare": True,
                "bindings": [{"table": "f1", "expr": "SUM({f}.v)"}],
            }
        ],
        "properties": [
            {
                "name": "p1",
                "display": "P1",
                "kind": "enum",
                "domain": "x",
                "aliases": [],
                "bindings": [{"table": "d1", "column": "c"}],
            }
        ],
        "glossary": [],
    }


@pytest.fixture()
def raw() -> dict:
    return copy.deepcopy(base_ontology())
