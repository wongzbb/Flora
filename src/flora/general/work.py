# SPDX-License-Identifier: Apache-2.0
"""Task-scoped durable work state. References are checked; claims are not graded."""

from __future__ import annotations

import hashlib
import re
import threading
from copy import deepcopy

from flora.support.errors import ValidationError

from .storage import atomic_json

TASK_OBLIGATION_ID = "task"
TASK_OBLIGATION_GOAL = (
    "Complete the current user task in work.task, or explicitly record its unresolved limitations."
)


class WorkLedger:
    def __init__(self, owner):
        self.owner = owner
        self.path = owner.directory / "work.json"
        self.lock = threading.RLock()
        self.require_task_completion = owner.profile["general"].get(
            "require_task_completion", False
        )
        self.refinement_enabled = owner.profile["general"].get("tool_schema_version", 1) >= 4
        from .agent import read_profile

        self.state = read_profile(self.path, max_bytes=1048576) if self.path.exists() else {}

    def begin(self, task_key, task):
        with self.lock:
            self.state = {"task_key": task_key, "task": task, "revision": 0, "steps": []}
            if self.refinement_enabled:
                self.state["history"] = []
            if self.require_task_completion:
                self.state["steps"] = [
                    {
                        "id": TASK_OBLIGATION_ID,
                        "goal": TASK_OBLIGATION_GOAL,
                        "status": "pending",
                        "required": True,
                        "evidence": [],
                        "note": "",
                    }
                ]
            atomic_json(self.path, self.state)

    def read_work(self) -> dict:
        """Read the current task's durable steps, unresolved work and checked evidence references."""
        with self.lock:
            if self.refinement_enabled:
                result = deepcopy(
                    {key: value for key, value in self.state.items() if key != "history"}
                )
                result["history_count"] = len(self.state.get("history", []))
            else:
                result = deepcopy(self.state)
            return {**result, "claims_verified": False}

    def evidence(self, references):
        if not isinstance(references, list) or len(references) > 64:
            raise ValidationError("evidence must be a list of at most 64 references")
        checked = []
        for ref in references:
            if not isinstance(ref, dict):
                raise ValidationError("Each evidence reference must be an object")
            if set(ref) == {"source_id"}:
                source = self.owner.store.read_source(ref["source_id"], limit=1)
                checked.append({"source_id": ref["source_id"], "sha256": source["sha256"]})
            elif set(ref) == {"path", "sha256"}:
                raw = self.owner.files.read_bytes(ref["path"])
                actual = hashlib.sha256(raw).hexdigest()
                if ref["sha256"] != actual:
                    raise ValidationError("Evidence file hash does not match current content")
                checked.append({"path": ref["path"], "sha256": actual})
            else:
                raise ValidationError(
                    "Evidence is {source_id} or {path,sha256}; no invented observations"
                )
        return checked

    def recheck_evidence(self, references):
        refs = [{"source_id": r["source_id"]} if "source_id" in r else r for r in references]
        if self.evidence(refs) != references:
            raise ValidationError("Recorded evidence no longer matches its observed content")

    def _check_revision(self, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("expected_revision must be a nonnegative integer")
        if (
            self.state.get("task_key") != self.owner.task["key"]
            or self.state.get("task") != self.owner.task["task"]
        ):
            raise ValidationError("Work state belongs to another task")
        if self.state["revision"] != expected_revision:
            raise ValidationError("Work revision changed; read_work before updating")

    def _clean_steps(self, steps):
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise ValidationError("Provide 1–64 work steps")
        clean, ids = [], set()
        for step in steps:
            if not isinstance(step, dict) or set(step) != {
                "id",
                "goal",
                "status",
                "required",
                "evidence",
                "note",
            }:
                raise ValidationError(
                    "Each step needs exactly id,goal,status,required,evidence,note"
                )
            ident = step["id"]
            if (
                not isinstance(ident, str)
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", ident)
                or ident in ids
            ):
                raise ValidationError("Work step IDs must be unique bounded identifiers")
            ids.add(ident)
            if (
                not isinstance(step["status"], str)
                or step["status"] not in {"pending", "running", "completed", "blocked"}
                or type(step["required"]) is not bool
            ):
                raise ValidationError("Invalid work step status or required flag")
            if (
                not isinstance(step["goal"], str)
                or not 1 <= len(step["goal"]) <= 2000
                or not isinstance(step["note"], str)
                or len(step["note"]) > 4000
            ):
                raise ValidationError("Work goal/note exceeds text bounds")
            if step["status"] == "blocked" and not step["note"].strip():
                raise ValidationError("Blocked work requires an explicit limitation")
            clean.append({**step, "evidence": self.evidence(step["evidence"])})
        return clean

    def _commit(self, proposed):
        if len(__import__("json").dumps(proposed).encode()) > 1048576:
            raise ValidationError("Work state exceeds its storage bound")
        atomic_json(self.path, proposed)
        self.state = proposed
        return self.read_work()

    def _historical_ids(self):
        ids = set()
        for entry in self.state.get("history", []):
            ids.update(step["id"] for step in entry["superseded"])
            ids.update(entry["replacement_ids"])
        return ids

    def update_work(self, steps: list[dict], expected_revision: int) -> dict:
        """Replace task steps with an optimistic revision check. Each step needs id, goal, status, required, evidence and note. Status: pending/running/completed/blocked. Evidence: {source_id} or {path,sha256}. A completed declaration is not a truth verdict; blocked work needs an explicit limitation note."""
        with self.lock:
            self._check_revision(expected_revision)
            clean = self._clean_steps(steps)
            current_ids = {step["id"] for step in self.state["steps"]}
            if self.refinement_enabled and self._historical_ids() & (
                {step["id"] for step in clean} - current_ids
            ):
                raise ValidationError("Historical work IDs cannot be reused")
            # Existing required goals cannot silently disappear or become optional.
            required = {s["id"] for s in self.state["steps"] if s["required"]}
            if not required <= {s["id"] for s in clean if s["required"]}:
                raise ValidationError("Existing required steps must be retained as required")
            if self.require_task_completion or self.refinement_enabled:
                prior = {s["id"]: s for s in self.state["steps"] if s["required"]}
                if any(s["id"] in prior and s["goal"] != prior[s["id"]]["goal"] for s in clean):
                    raise ValidationError("Required goal text is immutable within this task")
            proposed = {**self.state, "revision": expected_revision + 1, "steps": clean}
            return self._commit(proposed)

    def complete_task(self, note: str, evidence: list[dict], expected_revision: int) -> dict:
        """Complete the host-created task obligation without rewriting its goal."""
        with self.lock:
            self._check_revision(expected_revision)
            if not self.require_task_completion:
                raise ValidationError("Task completion contract is disabled")
            task_step = next(
                (step for step in self.state["steps"] if step["id"] == TASK_OBLIGATION_ID),
                None,
            )
            if task_step is None or not task_step["required"]:
                raise ValidationError("Required task obligation is missing")
            other_pending = [
                step["id"]
                for step in self.state["steps"]
                if step["id"] != TASK_OBLIGATION_ID
                and step["required"]
                and step["status"] in {"pending", "running", "blocked"}
            ]
            if other_pending:
                raise ValidationError(
                    "Required work remains unresolved: " + ", ".join(other_pending)
                )
            if getattr(self.owner, "delegation", None):
                children = self.owner.delegation.completion()
                if not children.get("ready"):
                    raise ValidationError("Required child work remains unreviewed or incomplete")
            if not isinstance(note, str) or not 1 <= len(note.strip()) <= 4000:
                raise ValidationError("Completion note must be nonempty and at most 4000 characters")
            checked = self.evidence(evidence)
            steps = [
                {**step, "status": "completed", "note": note, "evidence": checked}
                if step["id"] == TASK_OBLIGATION_ID
                else deepcopy(step)
                for step in self.state["steps"]
            ]
            return self._commit({**self.state, "revision": expected_revision + 1, "steps": steps})

    def refine_work(
        self,
        superseded_ids: list[str],
        replacements: list[dict],
        reason: str,
        evidence: list[dict],
        expected_revision: int,
    ) -> dict:
        """Explicitly replace model-authored work with a revised plan. Preserve the user task anchor. Replacement steps use id,goal,status,required,evidence,note, with fresh IDs and pending/running status. Required work needs a required successor. Record a reason and optional observed evidence; the host does not prove semantic equivalence or correctness. Superseded steps remain historical snapshots, not current completion claims."""
        with self.lock:
            if not self.refinement_enabled:
                raise ValidationError("Work refinement requires tool_schema_version 4")
            self._check_revision(expected_revision)
            if (
                not isinstance(superseded_ids, list)
                or not 1 <= len(superseded_ids) <= 64
                or any(not isinstance(ident, str) for ident in superseded_ids)
                or len(set(superseded_ids)) != len(superseded_ids)
            ):
                raise ValidationError("Provide 1–64 unique superseded work IDs")
            current = {step["id"]: step for step in self.state["steps"]}
            selected = set(superseded_ids)
            if TASK_OBLIGATION_ID in selected or not selected <= current.keys():
                raise ValidationError("Only existing non-task steps may be superseded")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
                raise ValidationError(
                    "Refinement needs a nonempty reason of at most 4000 characters"
                )
            clean = self._clean_steps(replacements)
            used = current.keys() | self._historical_ids() | {TASK_OBLIGATION_ID}
            if any(step["id"] in used for step in clean):
                raise ValidationError("Replacement work IDs must be fresh")
            if any(step["status"] not in {"pending", "running"} for step in clean):
                raise ValidationError("Replacement work must start pending or running")
            if any(current[ident]["required"] for ident in selected) and not any(
                step["required"] for step in clean
            ):
                raise ValidationError("Required work needs at least one required successor")
            steps = [step for step in self.state["steps"] if step["id"] not in selected] + clean
            if len(steps) > 64:
                raise ValidationError("Provide at most 64 current work steps")
            entry = {
                "revision": expected_revision + 1,
                "superseded": [deepcopy(current[ident]) for ident in superseded_ids],
                "replacement_ids": [step["id"] for step in clean],
                "reason": reason,
                "evidence": self.evidence(evidence),
            }
            return self._commit(
                {
                    **self.state,
                    "revision": expected_revision + 1,
                    "steps": steps,
                    "history": [*self.state.get("history", []), entry],
                }
            )

    def read_work_history(self, offset: int = 0, limit: int = 20) -> dict:
        """Read a bounded page of immutable plan-refinement history. Historical goals and evidence are past model claims, not current obligations or verified conclusions."""
        with self.lock:
            if not self.refinement_enabled:
                raise ValidationError("Work history requires tool_schema_version 4")
            if (
                type(offset) is not int
                or offset < 0
                or type(limit) is not int
                or not 1 <= limit <= 20
            ):
                raise ValidationError("History offset must be nonnegative and limit must be 1–20")
            history = self.state.get("history", [])
            end = min(offset + limit, len(history))
            return {
                "task_key": self.state.get("task_key"),
                "revision": self.state.get("revision", 0),
                "entries": deepcopy(history[offset:end]),
                "total_entries": len(history),
                "next_offset": end if end < len(history) else None,
                "claims_verified": False,
            }

    def completion(self):
        with self.lock:
            # task.json and work.json are separate durable writes. An interruption
            # between them must not let the preceding task's completion authorize
            # the new task. Do not silently relabel or discard that older state.
            task_binding_valid = (
                not self.owner.task.get("key") and not self.state.get("task_key")
            ) or (
                self.state.get("task_key") == self.owner.task.get("key")
                and self.state.get("task") == self.owner.task.get("task")
            )
            pending = [
                s["id"]
                for s in self.state.get("steps", [])
                if s["required"] and s["status"] in {"pending", "running"}
            ]
            if self.require_task_completion and not any(
                s["id"] == TASK_OBLIGATION_ID and s["required"] for s in self.state.get("steps", [])
            ):
                pending.append(TASK_OBLIGATION_ID)
            stale = []
            limitations = []
            for step in self.state.get("steps", []):
                if step["status"] == "blocked":
                    limitations.append({"id": step["id"], "note": step["note"]})
                if step["status"] == "completed":
                    try:
                        self.recheck_evidence(step["evidence"])
                    except (OSError, ValidationError):
                        stale.append(step["id"])
            return {
                "ready": task_binding_valid and not pending and not stale,
                "task_binding_valid": task_binding_valid,
                "recovery_required": None
                if task_binding_valid
                else (
                    "Saved work belongs to a different task. Preserve it and inspect the "
                    "session; start the new task again only when no unfinished kernel turn exists."
                ),
                "pending_steps": pending,
                "stale_evidence": stale,
                "limitations": limitations,
                "claims_verified": False,
            }
