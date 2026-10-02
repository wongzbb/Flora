# SPDX-License-Identifier: Apache-2.0
"""Advertise the same input bounds that the tool implementations enforce."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace


def bounded_specs(
    specs,
    *,
    describe_results=False,
    collaboration=False,
    structured_results=False,
    work_refinement=False,
):
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
        "read_work_history": {
            "offset": {"minimum": 0, "default": 0},
            "limit": {"minimum": 1, "maximum": 20, "default": 20},
        },
        "refine_work": {
            "superseded_ids": {
                "minItems": 1,
                "maxItems": 64,
                "description": "Distinct current step IDs; duplicates are rejected by the work ledger.",
            },
            "reason": {"minLength": 1, "maxLength": 4000},
            "evidence": {"maxItems": 64},
            "expected_revision": {"minimum": 0},
        },
    }
    if structured_results:
        result_notes["read_agent"] = (
            " Successful VALUE is a collection window, not the worker answer. When offset=0 "
            "and next_offset=null, result is the actual structured child view "
            "{status,value,reason,budget,failure,claims_verified:false}; result.value is its "
            "unverified answer. Partial windows omit result: concatenate text in order, "
            "parse_json the complete text, then inspect status and value. Never return the "
            "collection window in place of the requested answer. Full collection and "
            "review_agent do not prove factual correctness."
        )
        result_notes["table_query"] += (
            " cell_types maps source column names to observed JSON cell types before filters. "
            "CSV cells are strings; XLSX cells can differ. eq/ne/contains compare exact string "
            "representations (boolean true becomes 'True', not 'true'); numeric comparison "
            "operators and metrics parse bounded decimals. Inspect unknown encodings before "
            "filtering. A zero match count is not proof that an assumed encoding is correct. "
            "Keep exact decimal strings unless the requested output explicitly needs a number."
        )
    for spec in specs:
        if work_refinement and spec.name == "read_work":
            spec = replace(
                spec,
                description=spec.description
                + (
                    " Returns current steps and history_count; use read_work_history for superseded snapshots."
                ),
            )
        if work_refinement and spec.name == "update_work":
            spec = replace(
                spec,
                description=spec.description
                + (
                    " Required goals cannot be silently rewritten; use refine_work to explicitly revise model-authored plans. Superseded IDs cannot be reused."
                ),
            )
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
        if work_refinement and spec.name in {"update_work", "refine_work"}:
            properties["steps" if spec.name == "update_work" else "replacements"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 64,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "goal", "status", "required", "evidence", "note"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 64,
                            "description": "Start with an ASCII letter; remaining characters must be ASCII letters, digits, underscore, dot, or hyphen. Enforced by the work ledger.",
                        },
                        "goal": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "status": {
                            "type": "string",
                            "enum": (
                                ["pending", "running"]
                                if spec.name == "refine_work"
                                else ["pending", "running", "completed", "blocked"]
                            ),
                        },
                        "required": {"type": "boolean"},
                        "evidence": {"type": "array", "items": {"type": "object"}, "maxItems": 64},
                        "note": {"type": "string", "maxLength": 4000},
                    },
                },
            }
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
    elif name == "spawn_agents":
        properties["tasks"].update(
            minItems=1,
            maxItems=32,
            description=(
                "Model-authored independent worker specifications. Each task is handed off "
                "with the same evidence, dependency and review rules as spawn_agent."
            ),
        )
        properties["tasks"]["items"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {
                "task": {"type": "string", "minLength": 1, "maxLength": 16000},
                "name": {"type": "string", "minLength": 1, "maxLength": 64},
                "context": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
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
                },
                "depends_on": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "required": {"type": "boolean"},
            },
        }
    elif name == "read_agent":
        properties["offset"].update(minimum=0)
        properties["limit"].update(minimum=1, maximum=24000)
    elif name == "wait_agents":
        properties["agent_ids"].update(
            minItems=1,
            maxItems=32,
            items={
                "type": ["string", "object"],
                "properties": {"agent_id": {"type": "string"}},
                "required": ["agent_id"],
            },
            description="Child IDs or untrusted spawn result envelopes containing agent_id; only the ID is used.",
        )
        properties["timeout"].update(minimum=0, maximum=60)
    elif name == "review_agent":
        properties["disposition"].update(enum=["accepted", "blocked", "rejected"])
        properties["note"].update(minLength=1, maxLength=4000)
        properties["evidence"].update(maxItems=64)
