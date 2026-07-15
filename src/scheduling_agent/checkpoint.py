"""Checkpoint serialization configuration.

The graph stores Pydantic domain models in its checkpointed state. LangGraph's
msgpack serializer reconstructs them but warns for types not on its allowlist
(and will block them in a future version). We allowlist every model/enum
defined in ``scheduling_agent.models`` — derived dynamically so a new model is
covered automatically (a missing one would silently deserialize to a dict).
"""

from __future__ import annotations

from enum import Enum

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from scheduling_agent import models

_MODELS_MODULE = "scheduling_agent.models"


def _model_types() -> list[tuple[str, str]]:
    """(module, qualname) for every BaseModel/Enum defined in models.py."""
    pairs: list[tuple[str, str]] = []
    for name, obj in vars(models).items():
        if (
            isinstance(obj, type)
            and obj.__module__ == _MODELS_MODULE
            and issubclass(obj, BaseModel | Enum)
        ):
            pairs.append((_MODELS_MODULE, name))
    return pairs


def default_serde() -> JsonPlusSerializer:
    """A serializer that allowlists our domain models for checkpointing."""
    return JsonPlusSerializer(allowed_msgpack_modules=_model_types())
