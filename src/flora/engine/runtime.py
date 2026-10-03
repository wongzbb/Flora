"""Single-world orchestration of open programs, diagnoses and scoped revisions."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from flora.checks.contracts import ContextCheck, ContractStore, Verdict, check_revision, fit_guard
from flora.checks.diagnostics import check_forecasts, evaluate_diagnostic
from flora.checks.reuse import ReuseLibrary
from flora.engine.budget import Budget
from flora.engine.effects import EffectExecutor
from flora.engine.scheduler import choose
from flora.integrations.tools import ToolRegistry
from flora.language.compiler import CompilerContext, validate_bundle
from flora.language.ir import parse_program
from flora.language.vm import Machine, new_machine, resume, run_until_boundary
from flora.state.trace import MemoryTrace, outcome_from_record
from flora.support.errors import (
    BudgetExceeded,
    CompilerError,
    ValidationError,
)
from flora.support.resources import (
    DEFAULT_CONTEXT_BYTES,
    DEFAULT_CONTRACT_BYTES,
    DEFAULT_REPORT_BYTES,
    ResourceLimitExceeded,
    encoded_size,
    retain_newest,
    validate_limit,
)
from flora.support.values import canonical_json, clone, digest

_VOLATILE_OBSERVATION_KEYS = frozenset(
    {
        "budget",
        "created",
        "updated",
        "event_id",
        "trace_digest",
        "read_digest",
        "read_state_digest",
        "elapsed_seconds",
        "input_tokens",
        "output_tokens",
        "model_calls",
        "tool_calls",
        "reserved_output",
        "unknown_usage_calls",
    }
)


def _semantic_observation(value):
    """Remove accounting and internal cursor metadata before no-progress hashing."""
    if isinstance(value, dict):
        return {
            key: _semantic_observation(item)
            for key, item in value.items()
            if key not in _VOLATILE_OBSERVATION_KEYS
        }
    if isinstance(value, list):
        return [_semantic_observation(item) for item in value]
    return value


def _migration_failure(boundary, receipt_count):
    """Disclose structural failure only; VM messages may contain arbitrary data."""
    details = {"boundary_kind": boundary.kind, "receipt_count": receipt_count}
    code = boundary.details.get("code")
    if code in {
        "TYPE_ERROR",
        "MISSING_KEY",
        "VALUE_LIMIT",
        "JSON_PARSE_ERROR",
        "ASSERTION_FAILED",
        "OPAQUE_VALUE",
        "RECEIPT_UNAVAILABLE",
        "RECEIPT_UNSETTLED",
        "MEMORY_UNAVAILABLE",
        "ARITHMETIC_ERROR",
        "FUEL_EXHAUSTED",
        "UNKNOWN_OPERATION",
        "UNKNOWN_TERMINATOR",
    }:
        details["fault_code"] = code
    if boundary.kind == "return":
        details["return_type"] = {
            type(None): "null",
            bool: "boolean",
            int: "integer",
            float: "number",
            str: "string",
            list: "array",
            dict: "object",
        }.get(type(boundary.value), "unknown")
    return details


@dataclass(frozen=True)
class RuntimeConfig:
    max_steps: int | None = 200
    max_candidates: int = 3
    max_diagnostics: int = 2
    max_diagnostic_calls: int = 3
    pure_fuel: int = 50_000
    diagnostic_fuel: int = 10_000
    max_value_bytes: int = 1_048_576
    max_tool_output_bytes: int = 4_194_304
    max_contexts_per_program: int = 64
    max_context_bytes: int = DEFAULT_CONTEXT_BYTES
    max_contract_bytes: int = DEFAULT_CONTRACT_BYTES
    max_reports: int = 256
    max_reports_bytes: int = DEFAULT_REPORT_BYTES
    max_compile_cycles: int | None = 30
    enable_diagnostics: bool = True
    enable_contracts: bool = True

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key in {"max_steps", "max_compile_cycles"} and value is None:
                continue
            if key.startswith("enable_"):
                if not isinstance(value, bool):
                    raise ValidationError(f"{key} must be boolean")
            elif (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < (0 if key in {"max_diagnostics", "max_diagnostic_calls"} else 1)
            ):
                raise ValidationError(f"Invalid runtime limit: {key}")
        if self.max_candidates > 3 or self.max_diagnostics > 2:
            raise ValidationError(
                "This IR compiler release supports at most 3 normal candidates and 2 diagnostics"
            )
        for name in ("max_context_bytes", "max_contract_bytes", "max_reports_bytes"):
            validate_limit(getattr(self, name), name)


@dataclass
class Candidate:
    id: str
    machine: Machine
    anchor_epoch: int
    anchor_digest: str
    status: str = "ACTIVE"
    kind: str = "normal"
    inputs: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "id": self.id,
            "machine": self.machine.to_dict(),
            "anchor_epoch": self.anchor_epoch,
            "anchor_digest": self.anchor_digest,
            "status": self.status,
            "kind": self.kind,
            "inputs": clone(self.inputs),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            machine=Machine.from_dict(data["machine"]),
            anchor_epoch=data["anchor_epoch"],
            anchor_digest=data["anchor_digest"],
            status=data["status"],
            kind=data.get("kind", "normal"),
            inputs=clone(data.get("inputs", {})),
        )


@dataclass
class RunResult:
    status: str
    value: Any
    reason: str
    steps: int
    epoch: int
    trace_digest: str
    budget: dict
    reports: list[dict]

    def to_dict(self):
        return asdict(self)


class Runtime:
    """Synchronous, capability-bounded execution kernel.

    Host adapters are trusted Python and must impose their own permissions and
    synchronous I/O bounds. Generated programs cannot execute Python or expand
    this capability set. One Runtime instance admits one run at a time.
    """

    def __init__(
        self,
        tools: ToolRegistry,
        *,
        compiler=None,
        trace: MemoryTrace | None = None,
        budget: Budget | None = None,
        config: RuntimeConfig | None = None,
        memory: dict | None = None,
        completion_guard=None,
        on_event=None,
    ) -> None:
        self.tools, self.compiler = tools, compiler
        self.trace, self.budget = trace or MemoryTrace(), budget or Budget()
        self.config = config or RuntimeConfig()
        if completion_guard is not None and not callable(completion_guard):
            raise ValidationError("Completion guard must be a callable workflow-completion check")
        self.completion_guard = completion_guard
        if on_event is not None and not callable(on_event):
            raise ValidationError("on_event must be callable")
        self.on_event = on_event
        self.observer_errors = 0
        self.memory = clone(memory or {})
        self.executor = EffectExecutor(
            tools, self.trace, self.budget, max_output_bytes=self.config.max_tool_output_bytes
        )
        self.candidates: dict[str, Candidate] = {}
        self.diagnostics: dict[str, dict] = {}
        self.incumbent = ""
        self.task = ""
        self.reports: list[dict] = []
        self.contexts: list[dict] = []
        self.contracts = ContractStore(max_bytes=self.config.max_contract_bytes)
        self.reuse = ReuseLibrary()
        self.retention = {
            "contexts_dropped": 0,
            "contexts_skipped": 0,
            "contract_records_dropped": 0,
            "reports_dropped": 0,
            "checkpoint_compactions": 0,
            "checkpoint_failures": 0,
        }
        self.steps = self.compile_cycles = self.diagnostic_calls = 0
        self._last_inserted_diagnostic = False
        self._run_lock = threading.Lock()
        self._completed: dict | None = None
        # Operational recovery accounting is separate from model-owned memory.
        # Scheduling slices and reopen must not grant another recovery attempt.
        self._error_recovery: dict | None = None
        if self.compiler is not None and hasattr(self.compiler, "set_accounting"):
            if hasattr(self.compiler, "max_programs"):
                self.compiler.max_programs = min(
                    self.compiler.max_programs, self.config.max_candidates
                )
            if hasattr(self.compiler, "max_diagnostics"):
                self.compiler.max_diagnostics = min(
                    self.compiler.max_diagnostics, self.config.max_diagnostics
                )
            self.compiler.set_accounting(self._before_model_call, self._after_model_call)

    def _before_model_call(self, info: dict) -> None:
        self.budget.before_model_call(info)
        self._report(
            "model_call_started",
            attempt=info.get("attempt", 0),
            model_calls=self.budget.model_calls,
        )
        self._save()

    def _after_model_call(self, info: dict) -> None:
        try:
            self.budget.record_model_usage(info)
        finally:
            self._report(
                "model_call_finished",
                status=info.get("status", "unknown"),
                usage_known=bool(info.get("usage_known")),
                output_diagnostics=info.get("output_diagnostics", {}),
            )
            self._save()

    def _report(self, kind: str, **data) -> None:
        report = {"kind": kind, "epoch": self.trace.epoch, **data}
        try:
            encoded_size(report, limit=self.config.max_reports_bytes, resource="runtime report")
            report = clone(report)
        except (ResourceLimitExceeded, ValidationError):
            self.retention["reports_dropped"] += 1
            report = {
                "kind": "report_omitted",
                "epoch": self.trace.epoch,
                "original_kind": kind,
                "reason": "REPORT_SIZE_LIMIT",
            }
        self.reports.append(report)
        self.reports, stats = retain_newest(
            self.reports,
            max_bytes=self.config.max_reports_bytes,
            max_records=self.config.max_reports,
            resource="runtime reports",
        )
        self.retention["reports_dropped"] += stats["dropped_records"]
        if self.on_event is not None:
            try:
                self.on_event(clone(report))
            except Exception:
                # Progress/UI failures must not change effect outcomes or retry a
                # completed operation. KeyboardInterrupt still stops the caller.
                self.observer_errors += 1

    def install_bundle(self, bundle: dict) -> None:
        """Validate all candidates before mutating runtime state."""
        bundle = validate_bundle(
            bundle,
            allowed_tools=self.tools.names,
            expected_epoch=self.trace.epoch,
            expected_digest=self.trace.digest,
            max_programs=self.config.max_candidates,
            max_diagnostics=self.config.max_diagnostics,
        )
        if (
            bundle.get("expected_epoch") != self.trace.epoch
            or bundle.get("expected_digest") != self.trace.digest
        ):
            from flora.support.errors import StaleAnchor

            raise StaleAnchor("Compiler bundle references a different actual history")
        specs, diagnoses = bundle.get("programs"), bundle.get("diagnostics", [])
        if not isinstance(specs, list) or not 1 <= len(specs) <= self.config.max_candidates:
            raise ValidationError("Bundle has an invalid normal candidate count")
        if not isinstance(diagnoses, list) or len(diagnoses) > self.config.max_diagnostics:
            raise ValidationError("Bundle has too many diagnostic programs")
        candidates: dict[str, Candidate] = {}
        for spec in specs:
            if (
                not isinstance(spec, dict)
                or set(spec) - {"id", "program", "inputs"}
                or not isinstance(spec.get("id"), str)
                or not spec["id"]
            ):
                raise ValidationError("Invalid candidate specification")
            if spec["id"] in candidates:
                raise ValidationError("Duplicate candidate ID")
            program = parse_program(spec["program"], self.tools.names)
            inputs = clone(spec.get("inputs", {}))
            machine = new_machine(program, inputs)
            candidates[spec["id"]] = Candidate(
                spec["id"], machine, self.trace.epoch, self.trace.digest, inputs=inputs
            )
        if bundle.get("incumbent") not in candidates:
            raise ValidationError("Incumbent is not a normal candidate")
        diagnostics = {}
        from flora.checks.diagnostics import validate_diagnostic

        for diag in diagnoses:
            validated = validate_diagnostic(
                diag, allowed_tools=self.tools.names, candidate_ids=set(candidates)
            )
            # Validator may return normalized data or validate in place; never trust mutation.
            diag = clone(validated if isinstance(validated, dict) else diag)
            if diag["id"] in candidates or diag["id"] in diagnostics:
                raise ValidationError("Diagnostic ID collides with another program")
            machine = new_machine(
                parse_program(diag["program"], self.tools.names), diag.get("inputs", {})
            )
            candidates[diag["id"]] = Candidate(
                diag["id"],
                machine,
                self.trace.epoch,
                self.trace.digest,
                kind="diagnostic",
                inputs=clone(diag.get("inputs", {})),
            )
            diagnostics[diag["id"]] = diag
        # A preserving patch may not bypass its gate through an unchecked bundle
        # replacement. Checked migrations supply the actual incoming machine.
        denied = set()
        for revision in bundle.get("revisions", []):
            target = revision["target_candidate"]
            incoming = target in candidates and candidates[target].kind == "normal"
            if incoming and digest(candidates[target].machine.program) != digest(
                revision["program"]
            ):
                raise ValidationError("Incoming candidate differs from its proposed revision")
            report = self.apply_revision(
                target,
                revision["program"],
                migration=revision["migration"],
                mode=revision["mode"],
                revision_id=revision["id"],
                install=incoming,
            )
            if incoming:
                if report["accepted"]:
                    candidates[target] = Candidate.from_dict(self.candidates[target].to_dict())
                else:
                    denied.add(target)
                    del candidates[target]
        for ident, diag in list(diagnostics.items()):
            if any(f["candidate_id"] in denied for f in diag["forecasts"]):
                del diagnostics[ident]
                del candidates[ident]
        remaining_normal = [c.id for c in candidates.values() if c.kind == "normal"]
        if not remaining_normal:
            self._save()
            raise CompilerError("All incoming programs failed their declared revision gates")
        for old in self.candidates.values():
            if old.status == "ACTIVE":
                old.status = "SUPERSEDED"
        self.candidates = candidates
        self.diagnostics = diagnostics
        self.incumbent = (
            bundle["incumbent"]
            if bundle["incumbent"] in remaining_normal
            else sorted(remaining_normal)[0]
        )
        self._report(
            "bundle_installed",
            normal_candidates=list(s["id"] for s in specs),
            diagnostics=list(diagnostics),
        )
        self._save()

    def _compile(self) -> None:
        if self.compiler is None:
            raise CompilerError("No compiler configured and no executable program remains")
        if (
            self.config.max_compile_cycles is not None
            and self.compile_cycles >= self.config.max_compile_cycles
        ):
            raise BudgetExceeded("Compilation cycle limit exhausted")
        self.budget.check_time()
        self.compile_cycles += 1
        from flora.language.revision_view import revision_state

        previous = [
            {
                "id": c.id,
                "program": c.machine.program,
                "status": c.status,
                "revision_state": revision_state(
                    c, self.contexts, epoch=self.trace.epoch, trace_digest=self.trace.digest
                ),
            }
            for c in self.candidates.values()
        ]
        report = list(self.reports)
        if self.config.enable_contracts and self.contracts.records:
            # Learned routing is evidence-scoped and never asserted as world truth.
            seen = set()
            for context in self.contexts[-8:]:
                program = context["program"]
                signature = digest(program)
                if signature in seen:
                    continue
                seen.add(signature)
                report.append(
                    {
                        "kind": "empirical_guard",
                        "program_hash": signature,
                        "routing": self.contracts.fit_guard(program, relation="DEFINED_PREFIX"),
                    }
                )
        context = CompilerContext(
            task=self.task,
            tools=self.tools.descriptions(),
            epoch=self.trace.epoch,
            trace_digest=self.trace.digest,
            receipts=self.trace.records,
            memory=self.memory,
            reports=report,
            previous_programs=previous,
            remaining_budget=self.budget.to_dict(),
        )
        self.install_bundle(self.compiler.compile(context))

    def _boundary(self, candidate: Candidate):
        if self.config.enable_contracts and candidate.kind == "normal" and self.reuse.variants:
            decision = self.reuse.route(
                candidate.machine,
                receipts=self.trace.records,
                memory=self.memory,
                fuel=self.config.pure_fuel,
            )
            if decision.report["attempts"]:
                self._report(
                    "reuse_checked",
                    candidate=candidate.id,
                    accepted=decision.accepted,
                    report=decision.report,
                )
            if decision.accepted:
                candidate.machine = decision.machine
        return run_until_boundary(
            candidate.machine,
            receipts=self.trace.records,
            memory=self.memory,
            fuel=self.config.pure_fuel,
            max_value_bytes=self.config.max_value_bytes,
        )

    def _capture(self, candidate: Candidate) -> None:
        if not self.config.enable_contracts or candidate.machine.mode != "observed":
            return
        try:
            # Size the whole material before copying the history into multiple
            # witnesses. A skipped consumer has no fabricated successful check.
            material = {
                "inputs": candidate.machine.registers,
                "receipts": self.trace.records,
                "memory": self.memory,
                "reference_program": candidate.machine.program,
                "reference_machine": candidate.machine.to_dict(),
            }
            encoded_size(material, limit=self.config.max_context_bytes, resource="consumer context")
            context = ContextCheck(
                id=f"{candidate.id}@{self.trace.epoch}:{len(self.contexts)}",
                **material,
                relation="DEFINED_PREFIX",
            )
            item = {
                "candidate_id": candidate.id,
                "program": candidate.machine.program,
                "context": context.to_dict(),
            }
            # Include list punctuation. Retention is all-or-nothing, never a
            # shortened receipt history that silently changes read_receipt.
            encoded_size([item], limit=self.config.max_context_bytes, resource="consumer context")
        except (ResourceLimitExceeded, ValidationError):
            self.retention["contexts_skipped"] += 1
            self._report(
                "consumer_check",
                candidate=candidate.id,
                retained=False,
                result={
                    "verdict": "UNKNOWN",
                    "relation": "DEFINED_PREFIX",
                    "witness": {"reason": "CONTEXT_CAPTURE_LIMIT"},
                },
            )
            return
        result = self.contracts.record(
            candidate.machine.program, context, fuel=self.config.pure_fuel
        )
        self.contexts.append(clone(item))
        # Keep the most recent contexts per program; verdicts remain scoped to retained cases.
        own = [i for i, c in enumerate(self.contexts) if c["candidate_id"] == candidate.id]
        drop = set(own[: -self.config.max_contexts_per_program])
        if drop:
            self.contexts = [c for i, c in enumerate(self.contexts) if i not in drop]
            self.retention["contexts_dropped"] += len(drop)
        self.contexts, stats = retain_newest(
            self.contexts, max_bytes=self.config.max_context_bytes, resource="consumer contexts"
        )
        self.retention["contexts_dropped"] += stats["dropped_records"]
        self.retention["contract_records_dropped"] = self.contracts.dropped_records
        report_result = result.to_dict()
        # The contract verdict describes the candidate prefix; this separate
        # digest records the actual observation frontier that preceded the next
        # action. Consumers may use it to distinguish a repeated effect request
        # with a changed receipt from a true no-information replay.
        report_result["observation_digest"] = digest(
            _semantic_observation(
                {
                    "tool": self.trace.records[-1]["tool"] if self.trace.records else None,
                    "args": self.trace.records[-1]["args"] if self.trace.records else None,
                    "last_receipt": self.trace.records[-1] if self.trace.records else None,
                }
            )
        )
        self._report(
            "consumer_check",
            candidate=candidate.id,
            retained=item in self.contexts,
            result=report_result,
        )

    def apply_revision(
        self,
        candidate_id: str,
        program: dict,
        *,
        migration: dict,
        mode: str = "PRESERVE",
        revision_id: str = "revision",
        install: bool = True,
    ) -> dict:
        """Joint program/state revision checked against actual consumer checkpoints.

        Migration is pure IR with one `context` entry parameter and returns the
        input dictionary for the new program. No implicit block-name mapping.
        Preserving revisions require actual PASS evidence. CHANGE is an explicit
        behavioral proposal, not a claim of task improvement.
        """
        if mode not in {"PRESERVE", "EXTEND", "CHANGE"} or candidate_id not in self.candidates:
            raise ValidationError("Invalid revision mode or target")
        program = parse_program(program, self.tools.names)
        migration = parse_program(migration, [])
        if migration["blocks"][migration["entry"]]["params"] != ["context"]:
            raise ValidationError("Migration entry must take exactly context")
        candidate = self.candidates[candidate_id]
        if candidate.machine.mode != "observed":
            raise ValidationError("Revision target must be an observed machine")
        if mode != "CHANGE" and (
            candidate.anchor_epoch != self.trace.epoch
            or candidate.anchor_digest != self.trace.digest
        ):
            report = {
                "id": revision_id,
                "target": candidate_id,
                "mode": mode,
                "accepted": False,
                "results": [],
                "reason": "stale_source_requires_explicit_change",
                "task_improvement": "UNMEASURED",
            }
            self._report("revision_checked", **report)
            return report
        source_machine = Machine.from_dict(candidate.machine.to_dict())
        samples = [x for x in self.contexts if x["candidate_id"] == candidate_id]
        results = []
        mapped_current = None
        for sample in samples:
            data = sample["context"]
            context = ContextCheck.from_dict(data)
            material = {
                "inputs": context.inputs,
                "receipts": context.receipts,
                "memory": context.memory,
            }
            migrated = run_until_boundary(
                new_machine(migration, {"context": material}),
                receipts=context.receipts,
                memory=context.memory,
                fuel=self.config.pure_fuel,
            )
            if migrated.kind != "return" or not isinstance(migrated.value, dict):
                results.append(
                    {
                        "verdict": "UNKNOWN",
                        "reason": "migration_did_not_return_inputs",
                        "context": context.id,
                        "migration_failure": _migration_failure(migrated, len(context.receipts)),
                    }
                )
                continue
            try:
                fresh = new_machine(program, migrated.value)
            except ValidationError as exc:
                results.append(
                    {
                        "verdict": "UNKNOWN",
                        "reason": "migration_inputs_invalid",
                        "details": str(exc),
                        "context": context.id,
                    }
                )
                continue
            relation = "SAME_BOUNDARY"
            if mode == "EXTEND":
                old_defined = check_revision(
                    sample["program"],
                    replace(context, relation="DEFINED_PREFIX"),
                    fuel=self.config.pure_fuel,
                )
                if old_defined.verdict == Verdict.FAIL:
                    relation = "DEFINED_PREFIX"
            mapped = replace(context, candidate_machine=fresh.to_dict(), relation=relation)
            check = check_revision(program, mapped, fuel=self.config.pure_fuel)
            self.contracts.record(program, mapped, check, fuel=self.config.pure_fuel)
            item = check.to_dict()
            item["context"] = context.id
            results.append(item)
        preserved = bool(results) and all(r.get("verdict") == "PASS" for r in results)
        accepted = preserved if mode in {"PRESERVE", "EXTEND"} else True
        report = {
            "id": revision_id,
            "target": candidate_id,
            "mode": mode,
            "accepted": accepted,
            "results": results,
            "task_improvement": "UNMEASURED",
        }
        # Same epoch does not imply same internal state. Always migrate current
        # registers independently; historical examples only constrain the patch.
        if accepted:
            material = {
                "inputs": clone(candidate.machine.registers),
                "receipts": self.trace.records,
                "memory": self.memory,
            }
            migrated = run_until_boundary(
                new_machine(migration, {"context": material}),
                receipts=self.trace.records,
                memory=self.memory,
                fuel=self.config.pure_fuel,
            )
            if migrated.kind != "return" or not isinstance(migrated.value, dict):
                accepted = report["accepted"] = False
                report["reason"] = "current_migration_unavailable"
                report["migration_failure"] = _migration_failure(migrated, len(self.trace.records))
            else:
                try:
                    mapped_current = new_machine(program, migrated.value)
                except ValidationError as exc:
                    report.update(
                        accepted=False, reason="current_migration_inputs_invalid", details=str(exc)
                    )
                    self._report("revision_checked", **report)
                    return report
                current = ContextCheck(
                    id=f"{revision_id}:current",
                    inputs=clone(candidate.machine.registers),
                    receipts=self.trace.records,
                    memory=self.memory,
                    reference_program=candidate.machine.program,
                    reference_machine=candidate.machine.to_dict(),
                    candidate_machine=mapped_current.to_dict(),
                    relation="SAME_BOUNDARY",
                )
                if mode == "CHANGE":
                    current = replace(current, relation="DEFINED_PREFIX")
                elif mode == "EXTEND":
                    prior = replace(current, candidate_machine=None, relation="DEFINED_PREFIX")
                    if (
                        check_revision(
                            candidate.machine.program, prior, fuel=self.config.pure_fuel
                        ).verdict
                        == Verdict.FAIL
                    ):
                        current = replace(current, relation="DEFINED_PREFIX")
                current_check = check_revision(program, current, fuel=self.config.pure_fuel)
                report["current_check"] = current_check.to_dict()
                if current_check.verdict != Verdict.PASS:
                    accepted = report["accepted"] = False
                    report["reason"] = "current_context_check_did_not_pass"
        if accepted and mode in {"PRESERVE", "EXTEND"} and self.config.enable_contracts:
            # Scope each empirical guard to the current source control location.
            # Its labels describe the declared mode's local relation, never reward.
            def scope(machine):
                return (
                    digest(machine.program),
                    machine.block_id,
                    machine.op_index,
                    [(f["resume"], f["bind"], sorted(f["capture"])) for f in machine.call_stack],
                )

            evidence = []
            for sample, check in zip(samples, results):
                raw = sample["context"].get("reference_machine")
                if raw is not None and scope(Machine.from_dict(raw)) == scope(source_machine):
                    evidence.append(
                        {"value": sample["context"]["inputs"], "verdict": check["verdict"]}
                    )
            evidence.append({"value": source_machine.registers, "verdict": "PASS"})
            try:
                fitted = fit_guard(evidence[-256:])
                ident = self.reuse.register(
                    source_machine, program, migration, fitted["guard"], mode=mode
                )
                report["reuse"] = {
                    "variant_id": ident,
                    "routing": fitted,
                    "policy": mode,
                    "current_check_required": True,
                }
            except ValidationError as exc:
                report["reuse"] = {"stored": False, "reason": str(exc)}
        self._report("revision_checked", **report)
        if accepted and install:
            self.candidates[candidate_id] = Candidate(
                candidate_id, mapped_current, self.trace.epoch, self.trace.digest
            )
            self._save()
        return report

    def _save(self) -> None:
        self.contexts, context_stats = retain_newest(
            self.contexts, max_bytes=self.config.max_context_bytes, resource="consumer contexts"
        )
        self.retention["contexts_dropped"] += context_stats["dropped_records"]
        self.contracts.trim_bytes(self.config.max_contract_bytes)
        self.retention["contract_records_dropped"] = self.contracts.dropped_records
        self.reports, report_stats = retain_newest(
            self.reports,
            max_bytes=self.config.max_reports_bytes,
            max_records=self.config.max_reports,
            resource="runtime reports",
        )
        self.retention["reports_dropped"] += report_stats["dropped_records"]
        data = {
            "format": "openharness-checkpoint-v1",
            "task": self.task,
            "epoch": self.trace.epoch,
            "trace_digest": self.trace.digest,
            "memory": self.memory,
            "config": asdict(self.config),
            "budget": self.budget.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates.values()],
            "incumbent": self.incumbent,
            "diagnostics": list(self.diagnostics.values()),
            "reports": self.reports,
            "contexts": self.contexts,
            "contracts": self.contracts.to_dict(),
            "steps": self.steps,
            "compile_cycles": self.compile_cycles,
            "diagnostic_calls": self.diagnostic_calls,
            "last_inserted_diagnostic": self._last_inserted_diagnostic,
            "completed": self._completed,
            "error_recovery": self._error_recovery,
            "retention": self.retention,
            "requires_completion_guard": self.completion_guard is not None,
            "reuse": self.reuse.to_dict(),
        }
        compacted = False
        while True:
            try:
                encoded_size(
                    data, limit=self.trace.max_checkpoint_bytes, resource="runtime checkpoint"
                )
                self.trace.save_checkpoint(data)
                return
            except (ResourceLimitExceeded, ValidationError) as exc:
                if not compacted:
                    self.retention["checkpoint_compactions"] += 1
                    compacted = True
                # Local replay witnesses and reports are optional. Drop whole
                # oldest entries; never drop machines, budgets, task or memory.
                if self.contexts:
                    count = max(1, len(self.contexts) // 2)
                    self.contexts = self.contexts[count:]
                    self.retention["contexts_dropped"] += count
                    data["contexts"] = self.contexts
                elif self.contracts.records:
                    maximum = max(2, encoded_size(self.contracts.records) // 2)
                    self.contracts.trim_bytes(maximum)
                    self.retention["contract_records_dropped"] = self.contracts.dropped_records
                    data["contracts"] = self.contracts.to_dict()
                elif self.reports:
                    count = max(1, len(self.reports) // 2)
                    self.reports = self.reports[count:]
                    self.retention["reports_dropped"] += count
                    data["reports"] = self.reports
                elif self.reuse.drop_oldest():
                    data["reuse"] = self.reuse.to_dict()
                else:
                    self.retention["checkpoint_failures"] += 1
                    if isinstance(exc, ResourceLimitExceeded):
                        raise
                    raise ResourceLimitExceeded(
                        "runtime checkpoint JSON structure", self.trace.max_checkpoint_bytes
                    ) from exc

    def _result(self, status: str, reason: str = "", value=None) -> RunResult:
        if status == "completed":
            self._completed = {
                "status": status,
                "value": clone(value),
                "reason": reason,
                "epoch": self.trace.epoch,
                "trace_digest": self.trace.digest,
            }
        try:
            self._save()
        except ResourceLimitExceeded as exc:
            # Do not recursively retry the same impossible checkpoint while
            # handling BudgetExceeded. The previous durable checkpoint survives.
            status, reason, value = "budget_exhausted", str(exc), None
            self._completed = None
            self._report(
                "checkpoint_unavailable", reason=str(exc), previous_checkpoint_preserved=True
            )
        return RunResult(
            status,
            clone(value),
            reason,
            self.steps,
            self.trace.epoch,
            self.trace.digest,
            self.budget.to_dict(),
            clone(self.reports) + [{"kind": "resource_retention", **self.retention}],
        )

    def run(
        self,
        task: str | None = None,
        *,
        bundle: dict | None = None,
        slice_steps: int | None = None,
        repeated_error_limit: int | None = None,
    ) -> RunResult:
        # A scheduling slice is operational, not a new task or a budget refund.
        # It yields only at committed boundaries; all cumulative limits still apply.
        if slice_steps is not None and (type(slice_steps) is not int or slice_steps < 1):
            raise ValidationError("slice_steps must be a positive integer or null")
        if repeated_error_limit is not None and (
            type(repeated_error_limit) is not int or repeated_error_limit < 2
        ):
            raise ValidationError("repeated_error_limit must be at least two or null")
        if not self._run_lock.acquire(blocking=False):
            raise ValidationError("A runtime instance is already running")
        try:
            if task is not None:
                if self.task and self.task != task:
                    raise ValidationError("A running task cannot be silently replaced")
                self.task = task
            if not self.task:
                raise ValidationError("A nonempty task is required")
            if any(r["status"] in {"pending", "interrupted_unknown"} for r in self.trace.records):
                return self._result(
                    "interrupted_unknown",
                    "Resolve the outstanding effect using external evidence; no retry was sent",
                )
            if self._completed is not None:
                same_history = (
                    self._completed.get("epoch") == self.trace.epoch
                    and self._completed.get("trace_digest") == self.trace.digest
                )
                completion = self.completion_guard() if self.completion_guard is not None else True
                guard_accepts = same_history and (
                    completion is True
                    or (isinstance(completion, dict) and completion.get("ready") is True)
                )
                if guard_accepts:
                    return self._result(
                        "completed", self._completed["reason"], self._completed["value"]
                    )
                self._completed = None
                self._report(
                    "cached_return_invalidated",
                    reason="completion_guard_rejected" if same_history else "real_history_changed",
                )
            if bundle is not None:
                self.install_bundle(bundle)
            start_steps = self.steps
            while self.config.max_steps is None or self.steps < self.config.max_steps:
                if slice_steps is not None and self.steps - start_steps >= slice_steps:
                    return self._result(
                        "yielded", "Scheduling slice committed; resume the same task"
                    )
                self.budget.check_time()
                if not any(
                    c.status == "ACTIVE" and c.kind == "normal" for c in self.candidates.values()
                ):
                    self._compile()
                boundaries = {}
                for candidate in list(self.candidates.values()):
                    if candidate.status != "ACTIVE":
                        continue
                    if (
                        candidate.anchor_epoch != self.trace.epoch
                        or candidate.anchor_digest != self.trace.digest
                    ):
                        candidate.status = "SUSPENDED"
                        self._report(
                            "execution_commitment",
                            candidate=candidate.id,
                            reason="stale_prefix",
                            information_gain=False,
                        )
                        continue
                    boundary = self._boundary(candidate)
                    candidate.machine = boundary.machine
                    if boundary.kind == "alternative":
                        if candidate.kind == "diagnostic":
                            candidate.status = "FAULTED"
                            self._report(
                                "invalid_diagnostic",
                                candidate=candidate.id,
                                reason="diagnostic_must_first_reach_one_effect",
                            )
                            continue
                        candidate.status = "EXPANDED"
                        active_count = sum(
                            c.status == "ACTIVE" and c.kind == "normal"
                            for c in self.candidates.values()
                        )
                        for index, machine in enumerate(boundary.alternatives):
                            if active_count >= self.config.max_candidates:
                                self._report(
                                    "pruned_budget",
                                    candidate=f"{candidate.id}/{index}",
                                    information_gain=False,
                                )
                                continue
                            ident = f"{candidate.id}/{index}"
                            # Keep nested alternatives addressable by compiler
                            # bundles, without overwriting an existing candidate.
                            if len(ident) > 64 or ident in self.candidates:
                                for nonce in range(len(self.candidates) + 1):
                                    suffix = digest(
                                        {"parent": candidate.id, "branch": index, "nonce": nonce}
                                    )[:32]
                                    ident = f"{candidate.id[:24]}/alt_{suffix}"
                                    if ident not in self.candidates:
                                        break
                                else:
                                    raise ValidationError(
                                        "Unable to allocate a unique bounded alternative ID"
                                    )
                            self.candidates[ident] = Candidate(
                                ident,
                                machine,
                                self.trace.epoch,
                                self.trace.digest,
                                inputs=candidate.inputs,
                            )
                            if self.incumbent == candidate.id:
                                self.incumbent = ident
                            active_count += 1
                        continue
                    if boundary.kind in {"fault", "unknown"}:
                        candidate.status = "FAULTED" if boundary.kind == "fault" else "UNKNOWN"
                        self._report(
                            "local_execution",
                            candidate=candidate.id,
                            status=boundary.kind,
                            details=boundary.details,
                        )
                    else:
                        boundaries[candidate.id] = boundary
                normal = [i for i in boundaries if self.candidates[i].kind == "normal"]
                if not normal:
                    self.steps += 1
                    continue
                if self.incumbent not in normal:
                    self.incumbent = sorted(normal)[0]
                if boundaries[self.incumbent].kind == "replan":
                    payload = clone(boundaries[self.incumbent].value)
                    self.memory["__openharness_continuation__"] = payload
                    for candidate in self.candidates.values():
                        if candidate.status == "ACTIVE":
                            candidate.status = (
                                "REPLAN_REQUESTED"
                                if candidate.id == self.incumbent
                                else "DEFERRED_FOR_REPLAN"
                            )
                    self._report(
                        "replan_requested",
                        candidate=self.incumbent,
                        reason=payload["reason"],
                        information_gain=False,
                    )
                    self.steps += 1
                    # Persist the selected observed continuation before making
                    # another charged model call. No world snapshot or reset.
                    self._save()
                    continue
                # Final-answer uncertainty is resolved by incumbent, never by fabricated labels.
                if boundaries[self.incumbent].kind == "return":
                    completion = (
                        self.completion_guard() if self.completion_guard is not None else True
                    )
                    ready = completion is True or (
                        isinstance(completion, dict) and completion.get("ready") is True
                    )
                    if not ready:
                        if isinstance(completion, dict) and completion.get("waiting") is True:
                            self._report(
                                "completion_waiting", details=completion, correctness_feedback=False
                            )
                            return self._result("waiting", "Required workers are still running")
                        self.candidates[self.incumbent].status = "COMPLETION_REJECTED"
                        self._report(
                            "completion_rejected",
                            candidate=self.incumbent,
                            reason="environment_reports_work_remaining",
                            correctness_feedback=False,
                            **({"details": completion} if isinstance(completion, dict) else {}),
                        )
                        self.steps += 1
                        continue
                    self._report(
                        "final_return", candidate=self.incumbent, task_correctness="unverified"
                    )
                    return self._result("completed", value=boundaries[self.incumbent].value)
                rows = []
                evaluated = {}
                for ident, boundary in boundaries.items():
                    if boundary.kind != "effect":
                        continue
                    candidate = self.candidates[ident]
                    try:
                        self.tools.validate_request(boundary.request)
                    except ValidationError as exc:
                        candidate.status = "FAULTED"
                        self._report("invalid_request", candidate=ident, message=str(exc))
                        continue
                    score = 0.0
                    if candidate.kind == "diagnostic" and self.config.enable_diagnostics:
                        relevant = {f["candidate_id"] for f in self.diagnostics[ident]["forecasts"]}
                        if not relevant.issubset(set(normal)):
                            self._report(
                                "diagnostic_inapplicable",
                                diagnostic=ident,
                                reason="forecast_candidate_unavailable",
                            )
                            continue
                        report = evaluate_diagnostic(
                            self.diagnostics[ident],
                            boundary,
                            candidate_ids=set(normal),
                            receipts=self.trace.records,
                            memory=self.memory,
                            anchor_epoch=self.trace.epoch,
                            anchor_digest=self.trace.digest,
                            fuel=self.config.diagnostic_fuel,
                        )
                        evaluated[ident] = report
                        score = report.score
                        self._report(
                            "diagnostic_evaluated", diagnostic=ident, report=report.to_dict()
                        )
                    rows.append(
                        {
                            "id": ident,
                            "request": boundary.request,
                            "kind": candidate.kind,
                            "score": score,
                            "anchor_epoch": self.trace.epoch,
                            "anchor_digest": self.trace.digest,
                        }
                    )
                if not any(r["kind"] == "normal" for r in rows):
                    self.steps += 1
                    continue
                allowed = (
                    self.config.enable_diagnostics
                    and not self._last_inserted_diagnostic
                    and self.diagnostic_calls < self.config.max_diagnostic_calls
                    and (
                        self.budget.limits.max_tool_calls is None
                        or self.budget.limits.max_tool_calls - self.budget.tool_calls >= 2
                    )
                )
                selected = choose(rows, self.incumbent, diagnostic_allowed=allowed)
                chosen = boundaries[selected]
                signature = canonical_json(chosen.request)
                if repeated_error_limit is not None:
                    recent = self.trace.records[-repeated_error_limit:]
                    if len(recent) == repeated_error_limit and all(
                        r["status"] == "raised" for r in recent
                    ):
                        signatures = [
                            digest({"tool": r["tool"], "args": r["args"], "error": r["error"]})
                            for r in recent
                        ]
                        key = signatures[-1]
                        marker = {"signature": key, "epoch": self.trace.epoch, "stalled": False}
                        request_matches = signature == canonical_json(
                            {"tool": recent[-1]["tool"], "args": recent[-1]["args"]}
                        )
                        if (
                            request_matches
                            and len(set(signatures)) == 1
                            and self._error_recovery != marker
                        ):
                            stalled = (self._error_recovery or {}).get("signature") == key
                            self._error_recovery = {**marker, "stalled": stalled}
                            for ident, boundary in boundaries.items():
                                if (
                                    boundary.kind == "effect"
                                    and canonical_json(boundary.request) == signature
                                ):
                                    self.candidates[ident].status = "RECOVERY_REQUESTED"
                            self._report(
                                "repeated_effect_error",
                                tool=recent[-1]["tool"],
                                error=recent[-1]["error"],
                                occurrences=repeated_error_limit,
                                request_digest=key,
                                effects_replayed=False,
                            )
                            if stalled:
                                return self._result(
                                    "stalled",
                                    "Repeated identical observed tool errors after a recovery compilation; change the strategy or input",
                                )
                            self.steps += 1
                            self._save()
                            continue
                matching = [
                    i
                    for i, b in boundaries.items()
                    if b.kind == "effect"
                    and canonical_json(b.request) == signature
                    and self.candidates[i].status == "ACTIVE"
                ]
                normal_match = any(self.candidates[i].kind == "normal" for i in matching)
                self._last_inserted_diagnostic = not normal_match
                if not normal_match:
                    self.diagnostic_calls += 1
                epoch, anchor = self.trace.epoch, self.trace.digest
                self._report(
                    "action_selected",
                    candidate=selected,
                    shared_with=matching,
                    diagnostic=not normal_match,
                    tool=chosen.request["tool"],
                )
                self._save()  # Machines are suspended BEFORE this external request.
                receipt = self.executor.execute(
                    chosen.request,
                    epoch=epoch,
                    trace_digest=anchor,
                    mode=chosen.machine.mode,
                    before_dispatch=lambda event_id: self._save(),
                )
                self.steps += 1
                if self._error_recovery and (
                    receipt["status"] == "returned"
                    or (
                        receipt["status"] == "raised"
                        and digest(
                            {
                                "tool": receipt["tool"],
                                "args": receipt["args"],
                                "error": receipt["error"],
                            }
                        )
                        != self._error_recovery["signature"]
                    )
                ):
                    self._error_recovery = None
                self._report(
                    "tool_result",
                    tool=chosen.request["tool"],
                    status=receipt["status"],
                    event_id=receipt["event_id"],
                )
                if receipt["status"] == "interrupted_unknown":
                    return self._result(
                        "interrupted_unknown",
                        "External effect outcome is unknown; execution stopped without retry",
                    )
                for ident, report in evaluated.items():
                    if canonical_json(report.request) == signature:
                        result = check_forecasts(
                            self.diagnostics[ident],
                            receipt,
                            expected_request=chosen.request,
                            expected_event_id=epoch,
                            expected_previous_hash=anchor,
                        )
                        self._report("forecast_observation", diagnostic=ident, report=result)
                for ident, candidate in self.candidates.items():
                    if candidate.status != "ACTIVE":
                        continue
                    if ident in matching:
                        candidate.machine = resume(
                            boundaries[ident].machine, outcome_from_record(receipt)
                        )
                        candidate.anchor_epoch, candidate.anchor_digest = (
                            self.trace.epoch,
                            self.trace.digest,
                        )
                        candidate.kind = (
                            "normal"  # Actual diagnostic continuation is now a real task program.
                        )
                        self._capture(candidate)
                    else:
                        candidate.status = "SUSPENDED"
                        self._report(
                            "execution_commitment",
                            candidate=ident,
                            reason="different_actual_event",
                            information_gain=False,
                        )
                if self.incumbent not in matching:
                    self.incumbent = selected if selected in matching else sorted(matching)[0]
                self.diagnostics = {}
                self._save()
            return self._result("incomplete", "Runtime step limit exhausted")
        except BudgetExceeded as exc:
            return self._result("budget_exhausted", str(exc))
        except CompilerError as exc:
            return self._result("needs_program", str(exc))
        finally:
            self._run_lock.release()

    @classmethod
    def restore(
        cls,
        tools: ToolRegistry,
        trace: MemoryTrace,
        *,
        compiler=None,
        completion_guard=None,
        on_event=None,
    ) -> Runtime:
        checkpoint = trace.load_checkpoint()
        if not checkpoint or checkpoint.get("format") != "openharness-checkpoint-v1":
            raise ValidationError("Trace has no supported runtime checkpoint")
        for field_name in ("steps", "compile_cycles", "diagnostic_calls"):
            number = checkpoint.get(field_name)
            if type(number) is not int or number < 0:
                raise ValidationError(f"Invalid persisted runtime counter: {field_name}")
        if type(checkpoint.get("last_inserted_diagnostic")) is not bool:
            raise ValidationError("Invalid persisted diagnostic quota flag")
        if not isinstance(checkpoint.get("task"), str) or not isinstance(
            checkpoint.get("candidates"), list
        ):
            raise ValidationError("Invalid checkpoint task or candidate collection")
        if type(checkpoint.get("requires_completion_guard", False)) is not bool:
            raise ValidationError("Invalid completion guard requirement flag")
        if checkpoint.get("requires_completion_guard", False) and completion_guard is None:
            raise ValidationError("Restore requires the original environment completion guard")
        obj = cls(
            tools,
            compiler=compiler,
            trace=trace,
            budget=Budget.from_dict(checkpoint["budget"]),
            config=RuntimeConfig(**checkpoint["config"]),
            memory=checkpoint["memory"],
            completion_guard=completion_guard,
            on_event=on_event,
        )
        if hasattr(obj, "retention"):
            retention = checkpoint.get("retention", obj.retention)
            if (
                not isinstance(retention, dict)
                or set(retention) != set(obj.retention)
                or any(type(v) is not int or v < 0 for v in retention.values())
            ):
                raise ValidationError("Invalid evidence-retention counters")
            obj.retention = clone(retention)
        obj.task = checkpoint["task"]
        obj.candidates = {c["id"]: Candidate.from_dict(c) for c in checkpoint["candidates"]}
        if len(obj.candidates) != len(checkpoint["candidates"]):
            raise ValidationError("Duplicate candidate IDs in checkpoint")
        if any(c.machine.mode != "observed" for c in obj.candidates.values()):
            raise ValidationError("Only observed machines can be restored into a real runtime")
        obj.incumbent = checkpoint["incumbent"]
        obj.diagnostics = {d["id"]: d for d in checkpoint["diagnostics"]}
        obj.reports, obj.contexts = clone(checkpoint["reports"]), clone(checkpoint["contexts"])
        obj.contracts = ContractStore.from_dict(checkpoint["contracts"])
        if "reuse" in checkpoint:
            obj.reuse = ReuseLibrary.from_dict(checkpoint["reuse"])
        obj.steps, obj.compile_cycles = checkpoint["steps"], checkpoint["compile_cycles"]
        obj.diagnostic_calls = checkpoint["diagnostic_calls"]
        obj._last_inserted_diagnostic = checkpoint["last_inserted_diagnostic"]
        obj._completed = clone(checkpoint.get("completed"))
        recovery = checkpoint.get("error_recovery")
        if recovery is not None and (
            not isinstance(recovery, dict)
            or set(recovery) != {"signature", "epoch", "stalled"}
            or not isinstance(recovery.get("signature"), str)
            or len(recovery["signature"]) != 64
            or any(c not in "0123456789abcdef" for c in recovery["signature"])
            or type(recovery.get("epoch")) is not int
            or not 0 <= recovery["epoch"] <= trace.epoch
            or type(recovery.get("stalled")) is not bool
        ):
            raise ValidationError("Invalid persisted error recovery state")
        obj._error_recovery = clone(recovery)
        # A checkpoint may be pre-dispatch while a real receipt was committed.
        # Resume only exact matching stored events, never resend them.
        records = trace.records
        if recovery and any(
            r["status"] == "returned"
            or (
                r["status"] == "raised"
                and digest({"tool": r["tool"], "args": r["args"], "error": r["error"]})
                != recovery["signature"]
            )
            for r in records[recovery["epoch"] :]
        ):
            obj._error_recovery = None
        # Admission is durable before the pre-dispatch budget checkpoint. A
        # crash in that interval must not refund an already-admitted attempt.
        obj.budget.tool_calls = max(obj.budget.tool_calls, len(records))
        for candidate in obj.candidates.values():
            if candidate.status != "ACTIVE":
                continue
            cursor = candidate.anchor_epoch
            expected_digest = candidate.anchor_digest
            if (
                not isinstance(cursor, int)
                or isinstance(cursor, bool)
                or not 0 <= cursor <= len(records)
            ):
                raise ValidationError("Invalid candidate history anchor")
            actual_digest = (
                records[cursor]["anchor_digest"] if cursor < len(records) else trace.digest
            )
            if expected_digest != actual_digest:
                from flora.support.errors import StaleAnchor

                raise StaleAnchor("Persisted candidate is not anchored to the recorded real prefix")
            while cursor < len(records):
                record = records[cursor]
                if record["status"] in {"pending", "interrupted_unknown"}:
                    break
                if record["anchor_digest"] != expected_digest:
                    candidate.status = "SUSPENDED"
                    break
                boundary = run_until_boundary(
                    candidate.machine,
                    receipts=records[:cursor],
                    memory=obj.memory,
                    fuel=obj.config.pure_fuel,
                    max_value_bytes=obj.config.max_value_bytes,
                )
                request = {"tool": record["tool"], "args": record["args"]}
                if boundary.kind != "effect" or canonical_json(boundary.request) != canonical_json(
                    request
                ):
                    candidate.status = "SUSPENDED"
                    break
                candidate.machine = resume(boundary.machine, outcome_from_record(record))
                candidate.kind = "normal"
                cursor += 1
                expected_digest = record.get("settlement_hash", expected_digest)
                candidate.anchor_epoch, candidate.anchor_digest = cursor, expected_digest
            if cursor == len(records) and candidate.status == "ACTIVE":
                candidate.anchor_digest = trace.digest
        obj._report(
            "checkpoint_restored",
            pending_unknown=any(r["status"] in {"pending", "interrupted_unknown"} for r in records),
        )
        return obj
