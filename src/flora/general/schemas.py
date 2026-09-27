# SPDX-License-Identifier: Apache-2.0
"""Advertise the same input bounds that the tool implementations enforce."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace


def bounded_specs(specs):
    result = []
    bounds = {
        "web_search": {
            "query": {"minLength": 1, "maxLength": 1000},
            "limit": {"minimum": 1, "maximum": 10, "default": 5},
        },
        "read_source": {
            "offset": {"minimum": 0, "default": 0},
            "limit": {"minimum": 1, "maximum": 24000, "default": 6000},
        },
        "list_sources": {
            "offset": {"minimum": 0, "default": 0},
            "limit": {"minimum": 1, "maximum": 100, "default": 50},
        },
        "table_query": {
            "offset": {"minimum": 0, "default": 0},
            "limit": {"minimum": 1, "maximum": 500, "default": 100},
            "sheet": {
                "default": "",
                "description": "Sheet name; empty string selects the first sheet",
            },
        },
        "write_report": {
            "content": {"minLength": 1, "maxLength": 1048576},
            "source_ids": {"maxItems": 1000},
        },
        "browser_fill": {"value": {"maxLength": 65536}},
        "http_request": {
            "method": {"enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"}
        },
    }
    for spec in specs:
        schema = deepcopy(spec.input_schema)
        if schema is None:
            result.append(spec)
            continue
        properties = schema.get("properties", {})
        for name, details in bounds.get(spec.name, {}).items():
            properties[name].update(details)
        if spec.name == "table_query":
            properties["filters"] = {
                "type": ["array", "null"],
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "op": {
                            "type": "string",
                            "enum": ["eq", "ne", "gt", "ge", "lt", "le", "contains"],
                        },
                        "value": {},
                    },
                    "required": ["column", "op", "value"],
                    "additionalProperties": False,
                },
            }
            properties["group_by"] = {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "maxItems": 256,
            }
            properties["metrics"] = {
                "type": ["array", "null"],
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "op": {"type": "string", "enum": ["count", "sum", "mean", "min", "max"]},
                        "as": {"type": "string", "minLength": 1},
                    },
                    "required": ["op"],
                    "additionalProperties": False,
                },
            }
        result.append(replace(spec, input_schema=schema))
    return result
