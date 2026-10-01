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
        from .agent import read_profile

        self.state = read_profile(self.path, max_bytes=1048576) if self.path.exists() else {}

    def begin(self, task_key, task):
        with self.lock:
            self.state = {"task_key": task_key, "task": task, "revision": 0, "steps": []}
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
            return {**deepcopy(self.state), "claims_verified": False}

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

    def update_work(self, steps: list[dict], expected_revision: int) -> dict:
        """Replace task steps with an optimistic revision check. Each step needs id, goal, status, required, evidence and note. Status: pending/running/completed/blocked. Evidence: {source_id} or {path,sha256}. A completed declaration is not a truth verdict; blocked work needs an explicit limitation note."""
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError("expected_revision must be a nonnegative integer")
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
        with self.lock:
            if self.state.get("task_key") != self.owner.task["key"]:
                raise ValidationError("Work state belongs to another task")
            if self.state["revision"] != expected_revision:
                raise ValidationError("Work revision changed; read_work before updating")
            # Existing required goals cannot silently disappear or become optional.
            required = {s["id"] for s in self.state["steps"] if s["required"]}
            if not required <= {s["id"] for s in clean if s["required"]}:
                raise ValidationError("Existing required steps must be retained as required")
            if self.require_task_completion:
                prior = {s["id"]: s for s in self.state["steps"] if s["required"]}
                if any(s["id"] in prior and s["goal"] != prior[s["id"]]["goal"] for s in clean):
                    raise ValidationError("Required goal text is immutable within this task")
            proposed = {**self.state, "revision": expected_revision + 1, "steps": clean}
            if len(__import__("json").dumps(proposed).encode()) > 1048576:
                raise ValidationError("Work state exceeds its storage bound")
            atomic_json(self.path, proposed)
            self.state = proposed
            return self.read_work()

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
