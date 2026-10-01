# SPDX-License-Identifier: Apache-2.0
"""Advertise the same input bounds that the tool implementations enforce."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace


def bounded_specs(specs, *, describe_results=False, collaboration=False):
    result = []
    result_notes = {
        "read_file": " Successful VALUE is an object: {path,content:string,size_bytes,offset,next_offset,read_bytes,truncated,has_more,sha256,partial_sha256}. Extract content before parse_json. Check has_more/truncated before treating it as a whole document. Errors are raised, not a content string. A missing file raises FileNotFoundError (the error.type class name), not a not_found code.",
        "table_query": " Successful VALUE contains rows:[{metric_alias:value}], matched_rows, total_result_rows, next_offset and source_id. Sum metrics are exact decimal strings. Extract rows then the requested field; return the actual number/string rather than the whole receipt when the user asks only for the total.",
        "write_file": " Successful VALUE is an actual publication receipt with path and sha256; read those fields only after success. An earlier read receipt does not prove that a write happened.",
        "list_files": " Successful VALUE is an object with entries:[{path,type,size_bytes?}], returned_entries, truncated, traversal_truncated and skipped counters; not an array of strings. Use discovery only when the path is not already supplied.",
    }
    bounds = {
        "append_lines": {"lines": {"minItems": 1, "maxItems": 10000}},
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
        if describe_results and spec.name in result_notes:
            spec = replace(spec, description=spec.description + result_notes[spec.name])
        schema = deepcopy(spec.input_schema)
        if schema is None:
            result.append(spec)
            continue
        properties = schema.get("properties", {})
        for name, details in bounds.get(spec.name, {}).items():
            properties[name].update(details)
        if collaboration:
            _collaboration_bounds(spec.name, properties)
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


def _collaboration_bounds(name, properties):
    """Versioned metadata for existing host checks, not extra capabilities."""
    if name == "spawn_agent":
        properties["task"].update(minLength=1, maxLength=16000)
        properties["name"].update(minLength=1, maxLength=64)
        properties["depends_on"].update(maxItems=8)
        properties["context"].update(
            additionalProperties=False,
            properties={
                "guidance": {"type": "string", "maxLength": 16000},
                "source_ids": {"type": "array", "maxItems": 64, "items": {"type": "string"}},
                "files": {
                    "type": "array",
                    "maxItems": 64,
                    "items": {
                        "type": "object",
                        "required": ["path", "sha256"],
                        "additionalProperties": False,
                        "properties": {
                            "path": {"type": "string"},
                            "sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                        },
                    },
                },
            },
            description="Only guidance, source_ids and files are accepted. Put free-form observed context in guidance; completed dependency outputs are handed over automatically.",
        )
    elif name == "read_agent":
        properties["offset"].update(minimum=0)
        properties["limit"].update(minimum=1, maximum=24000)
    elif name == "wait_agents":
        properties["agent_ids"].update(minItems=1, maxItems=32)
        properties["timeout"].update(minimum=0, maximum=60)
    elif name == "review_agent":
        properties["disposition"].update(enum=["accepted", "blocked", "rejected"])
        properties["note"].update(minLength=1, maxLength=4000)
        properties["evidence"].update(maxItems=64)
