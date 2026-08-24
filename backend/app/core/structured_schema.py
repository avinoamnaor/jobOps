"""Deriving a strict Structured-Outputs JSON schema from a Pydantic model.

The point of this module is that there is exactly ONE definition of what a
classification looks like — `schemas.classification.EmailClassification` — and
the schema sent to the provider is *derived* from it mechanically. A hand-copied
second definition would drift the first time a field changed, and the drift
would show up as a confusing validation failure rather than as an obvious
mistake.

Strict structured outputs impose two requirements a normal Pydantic schema does
not satisfy, so the derived schema is transformed rather than used as-is:

  * every property must appear in `required` — optionality is expressed by
    allowing null, not by omitting the key
  * every object must set `additionalProperties: false`

and a number of JSON Schema validation keywords are not supported in strict
mode, so they are stripped. Nothing is lost by that: those constraints are
still enforced, on the way back in, by the Pydantic model itself. Which is the
right place for them anyway — a bound the provider promises to respect is a
courtesy, a bound we check on arrival is a guarantee.

Pure functions, no I/O, no SDK import.
"""

from typing import Any

from pydantic import BaseModel

# JSON Schema keywords strict structured outputs do not accept.
#
# `format` is included deliberately even though some formats are supported:
# `event_datetime` is far better handled by letting the model emit an ordinary
# ISO-8601 string and having Pydantic parse it, because Pydantic also enforces
# the rule that actually matters to us — that the value is timezone-aware —
# which no JSON Schema `format` can express.
#
# `default` is stripped because strict mode requires every property to be
# required, which makes a default meaningless in the wire schema.
UNSUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "uniqueItems",
    }
)

# Documentation keywords, stripped for a different reason than the above: they
# are supported, they are just the wrong text to send.
#
# Pydantic turns every docstring into a `description`, and ours are written for
# developers — they discuss why `ClassificationConfidence` is kept separate from
# `SuggestionConfidence`, what a later phase will do, which fields were left out
# on purpose. That is internal rationale about JobOps, not guidance about the
# task: it costs tokens on every request and, worse, invites the model to reason
# about product policy it has no business touching.
#
# All the model-facing guidance lives in `classification_prompt.INSTRUCTIONS`,
# written for that audience. Keeping the schema to pure shape means there is one
# place to edit what the model is told, rather than two that can disagree.
DOCUMENTATION_KEYWORDS: frozenset[str] = frozenset({"description", "title"})


def harden_for_strict_mode(node: Any) -> Any:
    """Recursively transform a JSON Schema fragment for strict structured outputs.

    Walks the whole document — including `$defs`, `properties`, `items` and
    `anyOf` branches — because a nested object that misses these rules fails
    the request just as surely as the root one would.
    """
    if isinstance(node, list):
        return [harden_for_strict_mode(item) for item in node]
    if not isinstance(node, dict):
        return node

    hardened = {
        key: harden_for_strict_mode(value)
        for key, value in node.items()
        if key not in UNSUPPORTED_KEYWORDS and key not in DOCUMENTATION_KEYWORDS
    }

    # Only objects need the two structural rules. A string/enum/array subschema
    # is left alone, which is why `anyOf: [{string}, {null}]` survives intact.
    if hardened.get("type") == "object" or "properties" in hardened:
        properties = hardened.get("properties", {})
        # Sorted for a stable, diffable schema: an unstable key order would make
        # every regeneration look like a change.
        hardened["required"] = sorted(properties)
        hardened["additionalProperties"] = False

    return hardened


def strict_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """The strict Structured-Outputs schema for a Pydantic model.

    The returned schema is what the provider is asked to satisfy. It is
    deliberately *looser* than the model in places (no length bounds, no
    datetime format), because the model re-validates everything on arrival and
    is the authority on what is acceptable.
    """
    return harden_for_strict_mode(model.model_json_schema())
