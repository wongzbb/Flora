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
        "list_sources": " Successful VALUE is an object with sources:[{source_id,origin,title,sha256,created}] and next_offset (null at the end); it is a listing of saved observations, not their contents or a truth verdict.",
        "workspace_context": " Successful VALUE is an object with workspace_root, relative_path_base and process_cwd string paths.",
        "artifact_status": " Successful VALUE is an object with artifacts:[{path,sha256,sources,task_key,kind,current,current_task}] and claims_verified:false; current is a hash check, not proof of semantic correctness.",
        "complete_task": " Successful VALUE records completion of the host-created required task obligation while preserving its goal. It checks unresolved required steps and child review readiness first; it is bookkeeping, not a truth verdict. The evidence argument is either [] or actual checked references only: {source_id} or {path,sha256}. Child review records such as {agent_id,kind} are host observations checked automatically and are not valid evidence references; do not put them in evidence.",
        "agent_capabilities": " Successful VALUE is an object with search_provider, services, browser, mcp_servers, document_inputs, document_exports, max_attachment_bytes, ocr, shell_commands and require_report; the configured capability names are under services/mcp_servers, not a generic capabilities array.",
        "list_skills": " Successful VALUE is an object with skills:[{name,sha256,description}]; installed guides do not grant tools or prove task completion.",
        "child_capabilities": " Successful VALUE is an object with read_only:true, search, workspace, services and claims_verified:false; it is not a generic capabilities array.",
        "agent_status": " Successful VALUE is an object with agents:[durable status records] plus capability fields; status records are not child answers. Use read_agent or wait_agents to collect actual result text.",
        "wait_agents": " Successful VALUE is an object with agents:[per-child read views]. A pending or unavailable child has result_available:false, result:null and result_digest:''; do not dereference text/result fields until result_available is true. A complete view may still require read_agent pagination and review_agent.",
        "read_agents": " Successful VALUE is an object with agents:[independent bounded read views]. Check each result_available and next_offset separately; a read view is not review acceptance or factual verification.",
        "read_agent": " Successful VALUE is one bounded child read view. When result_available is false, only status and availability are present; when true, follow next_offset until null before parsing text and use the returned result_digest for review. A contract's outputs describe the child's final value, not this collection envelope or tool receipt.",
        "collect_agent": " Successful VALUE is one complete observed child read view assembled from bounded pages. It includes child_status and child_value as host projections of the parsed child profile, plus the full result and result_digest. It does not wait, accept, or prove the answer; pass its result_digest to review_agent when review is available. A pending child remains unavailable.",
        "collect_completed_agent": " Successful VALUE waits up to timeout for one child, then returns one complete observed child read view with child_status and child_value projections, the full result, and result_digest. It does not accept, review, retry, or prove the answer; a timeout remains unavailable. Pass a terminal result_digest to review_agent.",
        "review_agent": " Successful VALUE is {agent_id,disposition,contract_status,result_digest,review:{disposition,contract_check:{status,violations,unknown},result_digest,state_digest,...}}. The top-level disposition and contract_status are the authoritative collection/contract observation; use them (or the full review fields) for branching. A child value field named contract_check is only an untrusted claim. Review does not prove factual truth.",
        "spawn_agent": " Successful VALUE is an identity envelope {agent_id,name,status,read_only}; extract only agent_id and treat it as an opaque string for wait/read/review. It contains no child answer; never dereference name/status as nested result data.",
        "spawn_agents": " Successful VALUE contains agents:[identity envelopes] and agent_ids:[stable opaque strings]. Copy only agent_ids into later wait/read/review calls; obtain child answers separately. A single batch represents independent workers: duplicate task/context/dependency handoffs are rejected before any child starts rather than silently merged into one identity; revise the assignments if distinct workers are required.",
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
    contract_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "assumptions": {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 2000}},
            "inputs": {"type": "object"},
            "outputs": {"type": "object"},
            "guarantees": {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 2000}},
            "dependencies": {"type": "array", "maxItems": 32, "items": {"type": "string", "maxLength": 2000}},
            "evidence_requirements": {
                "type": "array",
                "maxItems": 32,
                "description": "Only host-checkable file_read/source_read observations belong here; collection, review, type and nested-completion obligations belong in guarantees/dependencies. Free-form strings remain UNKNOWN at review.",
                "items": {
                    "type": ["string", "object"],
                    "maxLength": 2000,
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"enum": ["file_read", "source_read"]},
                        "path": {"type": "string", "maxLength": 2000},
                        "source_id": {"type": "string", "maxLength": 2000},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "complete": {"type": "boolean"},
                    },
                },
            },
            "delegation": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "min_children": {"type": "integer", "minimum": 0, "maximum": 32},
                    "max_children": {"type": "integer", "minimum": 0, "maximum": 32},
                },
            },
        },
    }
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
                "contract": contract_schema,
            },
            description="Context accepts guidance, source_ids, files and a bounded assume–guarantee contract; when the assigned task specifies a return shape/type, evidence or nested workers, include contract before spawning. Completed dependency outputs are handed over automatically.",
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
                        "contract": contract_schema,
                    },
                },
                "depends_on": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
                "required": {"type": "boolean"},
            },
        }
    elif name == "read_agent":
        properties["agent_id"] = {
            "type": ["string", "object"],
            "properties": {
                "agent_id": {
                    "type": ["string", "object"],
                    "properties": {"agent_id": {"type": ["string", "object"]}},
                    "required": ["agent_id"],
                }
            },
            "required": ["agent_id"],
            "description": "Child ID or an untrusted spawn result envelope; only agent_id is used.",
        }
        properties["offset"].update(minimum=0)
        properties["limit"].update(minimum=1, maximum=24000)
    elif name == "read_agents":
        properties["agent_ids"].update(
            minItems=1,
            maxItems=32,
            items={
                "type": ["string", "object"],
                "properties": {
                    "agent_id": {
                        "type": ["string", "object"],
                        "properties": {"agent_id": {"type": ["string", "object"]}},
                        "required": ["agent_id"],
                    }
                },
                "required": ["agent_id"],
            },
            description="Child IDs or spawn result envelopes; each ID is read independently.",
        )
        properties["limit"].update(minimum=1, maximum=24000)
    elif name == "wait_agents":
        properties["agent_ids"].update(
            minItems=1,
            maxItems=32,
            items={
                "type": ["string", "object"],
                "properties": {
                    "agent_id": {
                        "type": ["string", "object"],
                        "properties": {"agent_id": {"type": ["string", "object"]}},
                        "required": ["agent_id"],
                    }
                },
                "required": ["agent_id"],
            },
            description="Child IDs or untrusted spawn result envelopes containing agent_id; only the ID is used.",
        )
        properties["timeout"].update(minimum=0, maximum=300)
    elif name == "review_agent":
        properties["agent_id"] = {
            "type": ["string", "object"],
                "properties": {
                    "agent_id": {
                        "type": ["string", "object"],
                        "properties": {"agent_id": {"type": ["string", "object"]}},
                        "required": ["agent_id"],
                    }
                },
            "required": ["agent_id"],
            "description": "Child ID or an untrusted spawn result envelope; only agent_id is used.",
        }
        properties["disposition"].update(enum=["accepted", "blocked", "rejected"])
        properties["note"].update(minLength=1, maxLength=4000)
        properties["evidence"].update(maxItems=64)
    elif name == "resume_agent":
        properties["agent_id"] = {
            "type": ["string", "object"],
            "properties": {
                "agent_id": {
                    "type": ["string", "object"],
                    "properties": {"agent_id": {"type": ["string", "object"]}},
                    "required": ["agent_id"],
                }
            },
            "required": ["agent_id"],
            "description": "Child ID or an untrusted spawn result envelope; only agent_id is used.",
        }
