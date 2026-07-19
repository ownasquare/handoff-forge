"""Opt-in, deduplicated lifecycle integration for configured Codex hooks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypeVar

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from handoff_forge.errors import CapabilityError, StorageError
from handoff_forge.models import HandoffMode, utc_now
from handoff_forge.security import (
    FILE_MODE,
    enforce_private_file,
    ensure_directory,
    sanitize_json_value,
    sha256_file,
)

ADAPTER_ID = "handoff-forge-codex-precompact-v1"
_HOOK_MATCHER = "^(manual|auto)$"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_PROCESSING_LEASE = timedelta(minutes=5)
ModelT = TypeVar("ModelT", bound=BaseModel)
CodexFeatureEvidence = Literal[
    "features-list",
    "features-command-unavailable",
    "features-command-failed",
    "hooks-row-missing",
    "hooks-row-malformed",
]


class LifecycleArtifact(BaseModel):
    """Verified generated artifact returned by the application boundary."""

    model_config = ConfigDict(extra="forbid")

    output_id: str
    project_id: str
    path: Path
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: Literal["codex-precompact-v1", "codex-post-chat-v1"]


class CodexPreCompactCapability(BaseModel):
    """Effective Codex hooks feature state, explicitly excluding hook trust."""

    model_config = ConfigDict(extra="forbid")

    feature_enabled: bool | None
    version: str | None = None
    feature_evidence: CodexFeatureEvidence


class LifecycleBinding(BaseModel):
    """One explicit Codex workspace-to-project binding."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    workspace: Path
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def binding_id(self) -> str:
        return self.id


class LifecycleReceipt(BaseModel):
    """Durable, sanitized exactly-once receipt for one hook delivery."""

    model_config = ConfigDict(extra="forbid")

    id: str
    binding_id: str
    project_id: str
    event: HandoffMode
    trigger: Literal["manual", "auto"]
    session_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    turn_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["processing", "complete", "failed"] = "processing"
    attempt_count: int = Field(default=1, ge=1)
    output_id: str | None = None
    output_path: Path | None = None
    output_sha256: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class LifecycleVerification(BaseModel):
    """Static configuration and feature readback without claiming runtime trust."""

    model_config = ConfigDict(extra="forbid")

    binding_id: str
    hooks_path: Path
    configured: bool
    binding_enabled: bool
    feature_enabled: bool | None
    codex_version: str | None = None
    feature_evidence: CodexFeatureEvidence
    trust_status: Literal["unverified"] = "unverified"
    runtime_activation: Literal["unverified"] = "unverified"


class LifecycleStateStore:
    """Atomic private bindings and receipts below the Handoff Forge data root."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root.expanduser().resolve()
        ensure_directory(self.data_root)
        self.root = ensure_directory(self.data_root / "lifecycle" / "codex")
        self.bindings_root = ensure_directory(self.root / "bindings")
        self.receipts_root = ensure_directory(self.root / "receipts")
        self._lock = FileLock(str(self.root / ".lifecycle.lock"), mode=FILE_MODE)

    def create_binding(
        self,
        *,
        project_id: str,
        workspace: Path,
        enable_existing: bool = True,
    ) -> LifecycleBinding:
        binding, _previous = self.create_binding_with_previous(
            project_id=project_id,
            workspace=workspace,
            enable_existing=enable_existing,
        )
        return binding

    def create_binding_with_previous(
        self,
        *,
        project_id: str,
        workspace: Path,
        enable_existing: bool = True,
    ) -> tuple[LifecycleBinding, LifecycleBinding | None]:
        """Atomically return the active binding and its prior state, if any."""

        _validate_id(project_id, "project")
        resolved = _resolve_workspace(workspace)
        digest = hashlib.sha256(f"codex\0{project_id}\0{resolved}".encode()).hexdigest()[:32]
        binding_id = f"bnd_{digest}"
        with self._lock:
            existing = self._read_binding(binding_id)
            if existing is not None:
                if enable_existing and not existing.enabled:
                    enabled = existing.model_copy(update={"enabled": True, "updated_at": utc_now()})
                    self._write_model(self._binding_path(binding_id), enabled)
                active = self._read_model(self._binding_path(binding_id), LifecycleBinding)
                return active, existing
            binding = LifecycleBinding(
                id=binding_id,
                project_id=project_id,
                workspace=resolved,
            )
            self._write_model(self._binding_path(binding_id), binding)
            active = self._read_model(self._binding_path(binding_id), LifecycleBinding)
            return active, None

    def get_binding(self, binding_id: str) -> LifecycleBinding | None:
        _validate_id(binding_id, "binding")
        return self._read_binding(binding_id)

    def list_bindings(self) -> tuple[LifecycleBinding, ...]:
        bindings = [
            self._read_model(candidate, LifecycleBinding)
            for candidate in sorted(self.bindings_root.glob("*.json"))
            if candidate.is_file() and not candidate.is_symlink()
        ]
        return tuple(bindings)

    def set_binding_enabled(self, binding_id: str, enabled: bool) -> LifecycleBinding:
        _validate_id(binding_id, "binding")
        with self._lock:
            binding = self._read_binding(binding_id)
            if binding is None:
                raise StorageError(f"unknown lifecycle binding: {binding_id}")
            updated = binding.model_copy(update={"enabled": enabled, "updated_at": utc_now()})
            self._write_model(self._binding_path(binding_id), updated)
            return self._read_model(self._binding_path(binding_id), LifecycleBinding)

    def delete_binding(self, binding_id: str) -> None:
        _validate_id(binding_id, "binding")
        with self._lock:
            self._binding_path(binding_id).unlink(missing_ok=True)

    def claim_receipt(
        self,
        *,
        receipt_id: str,
        binding: LifecycleBinding,
        event: HandoffMode,
        trigger: Literal["manual", "auto"],
        session_hash: str,
        turn_hash: str,
    ) -> tuple[LifecycleReceipt, bool]:
        _validate_id(receipt_id, "receipt")
        path = self._receipt_path(receipt_id)
        with self._lock:
            existing = self._read_receipt(receipt_id)
            if existing is not None:
                if existing.binding_id != binding.id or existing.project_id != binding.project_id:
                    raise StorageError("lifecycle receipt identity collision")
                if existing.event is not event:
                    raise StorageError("lifecycle receipt event collision")
                if existing.status == "complete":
                    return existing, False
                if (
                    existing.status == "processing"
                    and utc_now() - existing.updated_at < _PROCESSING_LEASE
                ):
                    return existing, False
                receipt = existing.model_copy(
                    update={
                        "status": "processing",
                        "attempt_count": existing.attempt_count + 1,
                        "error": None,
                        "updated_at": utc_now(),
                    }
                )
            else:
                receipt = LifecycleReceipt(
                    id=receipt_id,
                    binding_id=binding.id,
                    project_id=binding.project_id,
                    event=event,
                    trigger=trigger,
                    session_hash=session_hash,
                    turn_hash=turn_hash,
                )
            self._write_model(path, receipt)
            return self._read_model(path, LifecycleReceipt), True

    def complete_receipt(
        self,
        receipt_id: str,
        artifact: LifecycleArtifact,
    ) -> LifecycleReceipt:
        with self._lock:
            receipt = self._required_receipt(receipt_id)
            updated = receipt.model_copy(
                update={
                    "status": "complete",
                    "output_id": artifact.output_id,
                    "output_path": artifact.path,
                    "output_sha256": artifact.sha256,
                    "error": None,
                    "updated_at": utc_now(),
                }
            )
            self._write_model(self._receipt_path(receipt_id), updated)
            return self._read_model(self._receipt_path(receipt_id), LifecycleReceipt)

    def fail_receipt(self, receipt_id: str, error: Exception | str) -> LifecycleReceipt:
        with self._lock:
            receipt = self._required_receipt(receipt_id)
            sanitized = _sanitize_failure(error)
            updated = receipt.model_copy(
                update={
                    "status": "failed",
                    "output_id": None,
                    "output_path": None,
                    "output_sha256": None,
                    "error": sanitized,
                    "updated_at": utc_now(),
                }
            )
            self._write_model(self._receipt_path(receipt_id), updated)
            return self._read_model(self._receipt_path(receipt_id), LifecycleReceipt)

    def get_receipt(self, receipt_id: str) -> LifecycleReceipt | None:
        _validate_id(receipt_id, "receipt")
        return self._read_receipt(receipt_id)

    def list_receipts(self) -> tuple[LifecycleReceipt, ...]:
        receipts = [
            self._read_model(candidate, LifecycleReceipt)
            for candidate in sorted(self.receipts_root.glob("*.json"))
            if candidate.is_file() and not candidate.is_symlink()
        ]
        return tuple(receipts)

    def _required_receipt(self, receipt_id: str) -> LifecycleReceipt:
        receipt = self._read_receipt(receipt_id)
        if receipt is None:
            raise StorageError(f"unknown lifecycle receipt: {receipt_id}")
        return receipt

    def _read_binding(self, binding_id: str) -> LifecycleBinding | None:
        path = self._binding_path(binding_id)
        return None if not path.exists() else self._read_model(path, LifecycleBinding)

    def _read_receipt(self, receipt_id: str) -> LifecycleReceipt | None:
        path = self._receipt_path(receipt_id)
        return None if not path.exists() else self._read_model(path, LifecycleReceipt)

    def _binding_path(self, binding_id: str) -> Path:
        _validate_id(binding_id, "binding")
        return self.bindings_root / f"{binding_id}.json"

    def _receipt_path(self, receipt_id: str) -> Path:
        _validate_id(receipt_id, "receipt")
        return self.receipts_root / f"{receipt_id}.json"

    def _write_model(self, path: Path, model: BaseModel) -> None:
        payload = (
            json.dumps(
                sanitize_json_value(model.model_dump(mode="json")),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        _atomic_write(path, payload)

    @staticmethod
    def _read_model(path: Path, model: type[ModelT]) -> ModelT:
        if path.is_symlink() or not path.is_file():
            raise StorageError(f"lifecycle state is not a regular file: {path.name}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return model.model_validate(value)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise StorageError(f"could not read lifecycle state: {path.name}") from error


LifecycleGenerator = Callable[[LifecycleBinding, HandoffMode, str], LifecycleArtifact]


class CodexLifecycleAdapter:
    """Route native PreCompact or explicit lifecycle events into verified artifacts."""

    def __init__(
        self,
        *,
        state_store: LifecycleStateStore,
        generator: LifecycleGenerator,
    ) -> None:
        self.state_store = state_store
        self.generator = generator

    def handle(self, payload: Mapping[str, object], binding_id: str) -> dict[str, object]:
        """Handle only Codex's native PreCompact event; Stop is always ignored."""

        event_name = _first_string(
            payload.get("hook_event_name"),
            payload.get("hookEventName"),
            payload.get("event"),
        )
        if event_name.casefold() != "precompact":
            # Stop is turn-scoped, not a post-task signal. Never map it here.
            return {"continue": True}

        cwd_value = _first_string(payload.get("cwd"), payload.get("workspaceRoot"))
        if not cwd_value:
            return {"continue": True}
        session_id = _first_string(payload.get("session_id"), payload.get("sessionId"))
        turn_id = _first_string(payload.get("turn_id"), payload.get("turnId"))
        transcript_path = _first_string(
            payload.get("transcript_path"),
            payload.get("transcriptPath"),
        )
        trigger_value = _first_string(payload.get("trigger"), "auto").casefold()
        trigger: Literal["manual", "auto"] = "manual" if trigger_value == "manual" else "auto"
        if not session_id or not turn_id:
            return {
                "continue": True,
                "systemMessage": (
                    "Handoff Forge could not identify this pre-compaction event. "
                    "Use the manual Create handoff workflow."
                ),
            }
        return self._handle_event(
            event=HandoffMode.PRE_COMPACT,
            binding_id=binding_id,
            cwd_value=cwd_value,
            session_id=session_id,
            turn_id=turn_id,
            trigger=trigger,
            require_enabled=True,
            transcript_path_value=transcript_path,
        )

    def run_explicit(
        self,
        *,
        event: HandoffMode,
        binding_id: str,
        cwd: Path,
        event_key: str,
    ) -> dict[str, object]:
        """Run an explicit lifecycle event with a caller-stable deduplication key."""

        normalized_key = event_key.strip()
        if not normalized_key or len(normalized_key) > 256:
            raise StorageError("lifecycle event key must contain 1 to 256 characters")
        return self._handle_event(
            event=event,
            binding_id=binding_id,
            cwd_value=str(cwd),
            session_id=f"explicit:{binding_id}",
            turn_id=normalized_key,
            trigger="manual",
            require_enabled=False,
            transcript_path_value=None,
        )

    def _handle_event(
        self,
        *,
        event: HandoffMode,
        binding_id: str,
        cwd_value: str,
        session_id: str,
        turn_id: str,
        trigger: Literal["manual", "auto"],
        require_enabled: bool,
        transcript_path_value: str | None,
    ) -> dict[str, object]:
        binding = self.state_store.get_binding(binding_id)
        if binding is None or (require_enabled and not binding.enabled) or not cwd_value.strip():
            return {"continue": True}
        try:
            cwd = _resolve_workspace(Path(cwd_value))
        except (OSError, StorageError):
            return {"continue": True}
        if not cwd.is_relative_to(binding.workspace):
            return {"continue": True}

        transcript_revision: str | None = None
        if require_enabled:
            transcript_revision = _hash_regular_transcript(transcript_path_value)
            if transcript_revision is None:
                return {
                    "continue": True,
                    "systemMessage": (
                        "Handoff Forge could not safely fingerprint this pre-compaction "
                        "transcript. Use the manual Create handoff workflow."
                    ),
                }

        session_hash = hashlib.sha256(session_id.encode()).hexdigest()
        turn_hash = hashlib.sha256(turn_id.encode()).hexdigest()
        receipt_id = lifecycle_event_id(
            binding=binding,
            event=event,
            session_id=session_id,
            turn_id=turn_id,
            trigger=trigger,
            transcript_revision=transcript_revision,
        )
        receipt, claimed = self.state_store.claim_receipt(
            receipt_id=receipt_id,
            binding=binding,
            event=event,
            trigger=trigger,
            session_hash=session_hash,
            turn_hash=turn_hash,
        )
        if not claimed:
            verified_path = self._verified_receipt_path(receipt)
            if receipt.status == "complete" and verified_path is not None:
                return self._success_response(event, verified_path)
            if receipt.status == "complete":
                self.state_store.fail_receipt(receipt.id, "completed output failed readback")
            return {"continue": True}

        try:
            artifact = self.generator(binding, event, receipt_id)
            self._validate_artifact(binding, event, artifact)
            completed = self.state_store.complete_receipt(receipt_id, artifact)
            verified_path = self._verified_receipt_path(completed)
            if completed.status != "complete" or verified_path is None:
                raise StorageError("lifecycle receipt failed persistence readback")
            return self._success_response(event, verified_path)
        except Exception as error:
            self.state_store.fail_receipt(receipt_id, error)
            return {
                "continue": True,
                "systemMessage": (
                    "Lifecycle handoff failed safely. Retry with the same event key, "
                    "or use the manual Create handoff workflow."
                ),
            }

    @staticmethod
    def _validate_artifact(
        binding: LifecycleBinding,
        event: HandoffMode,
        artifact: LifecycleArtifact,
    ) -> None:
        if artifact.project_id != binding.project_id:
            raise StorageError("lifecycle output belongs to a different project")
        if artifact.profile != _profile_for_event(event):
            raise StorageError("lifecycle output used the wrong handoff profile")
        if artifact.path.is_symlink():
            raise StorageError("lifecycle output cannot be a symlink")
        resolved = artifact.path.resolve(strict=True)
        if not resolved.is_file() or sha256_file(resolved) != artifact.sha256:
            raise StorageError("lifecycle output failed hash readback")

    @staticmethod
    def _verified_receipt_path(receipt: LifecycleReceipt) -> Path | None:
        if receipt.output_path is None or receipt.output_sha256 is None:
            return None
        path = Path(receipt.output_path)
        if path.is_symlink():
            return None
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_file() or sha256_file(resolved) != receipt.output_sha256:
            return None
        return resolved

    @staticmethod
    def _success_response(event: HandoffMode, output_path: Path) -> dict[str, object]:
        label = "pre-compaction" if event is HandoffMode.PRE_COMPACT else "post-task"
        return {
            "continue": True,
            "systemMessage": (
                f"Handoff Forge saved and verified the {label} handoff: {output_path}"
            ),
        }


class CodexHookConfigManager:
    """Merge and remove only Handoff Forge-owned Codex hook handlers."""

    def __init__(
        self,
        *,
        hooks_path: Path,
        state_store: LifecycleStateStore,
        capability_resolver: Callable[
            [str, Path], CodexPreCompactCapability
        ] = lambda executable, workspace: probe_codex_precompact(executable, cwd=workspace),
    ) -> None:
        self.hooks_path = Path(os.path.abspath(hooks_path.expanduser()))
        self.state_store = state_store
        self.capability_resolver = capability_resolver
        self._lock = FileLock(str(self.hooks_path.parent / ".handoff-forge-hooks.lock"))

    def install(
        self,
        *,
        project_id: str,
        workspace: Path,
        executable: Path,
        codex_executable: str = "codex",
    ) -> LifecycleBinding:
        resolved_workspace = _resolve_workspace(workspace)
        # Validate existing static configuration before probing or creating state.
        self._read_document()
        capability = self.capability_resolver(codex_executable, resolved_workspace)
        if not _feature_configuration_is_authorized(capability):
            found = capability.version or "unavailable"
            raise CapabilityError(
                "Codex PreCompact hooks feature is disabled or could not be verified "
                f"({capability.feature_evidence}, runtime {found}); no hooks were changed. "
                "Handoff Forge does not enable Codex features automatically."
            )
        ensure_directory(self.hooks_path.parent)
        with self._lock:
            snapshot = self._snapshot_document()
            document = self._document_from_bytes(snapshot[0])
            hooks = document.setdefault("hooks", {})
            if not isinstance(hooks, dict):
                raise StorageError("Codex hooks root must be an object")
            groups = hooks.setdefault("PreCompact", [])
            if not isinstance(groups, list):
                raise StorageError("Codex PreCompact hooks must be a list")
            group = next(
                (
                    item
                    for item in groups
                    if isinstance(item, dict)
                    and item.get("matcher") == _HOOK_MATCHER
                    and isinstance(item.get("hooks"), list)
                ),
                None,
            )
            binding, prior_binding = self.state_store.create_binding_with_previous(
                project_id=project_id,
                workspace=resolved_workspace,
            )
            handler = self._handler(binding, executable)
            target_index: int | None = None
            for candidate in groups:
                if not isinstance(candidate, dict) or not isinstance(
                    candidate.get("hooks"),
                    list,
                ):
                    continue
                retained_handlers: list[object] = []
                for item in candidate["hooks"]:
                    if not _handler_owns_binding(item, binding.id):
                        retained_handlers.append(item)
                    elif candidate is group and target_index is None:
                        target_index = len(retained_handlers)
                candidate["hooks"] = retained_handlers
            if group is None:
                group = {"matcher": _HOOK_MATCHER, "hooks": []}
                groups.append(group)
            if target_index is None:
                group["hooks"].append(handler)
            else:
                group["hooks"].insert(target_index, handler)
            try:
                self._write_document(document)
            except Exception as write_error:
                try:
                    self._restore_document(snapshot)
                except Exception as rollback_error:
                    with suppress(StorageError):
                        self.state_store.set_binding_enabled(binding.id, False)
                    raise StorageError(
                        "Codex hooks update failed and rollback could not be verified; "
                        "the lifecycle binding was disabled"
                    ) from rollback_error
                if prior_binding is None:
                    self.state_store.delete_binding(binding.id)
                elif not prior_binding.enabled:
                    self.state_store.set_binding_enabled(binding.id, False)
                raise write_error
        return self.state_store.get_binding(binding.id) or binding

    def verify(
        self,
        binding_id: str,
        *,
        codex_executable: str = "codex",
    ) -> LifecycleVerification:
        binding = self.state_store.get_binding(binding_id)
        capability = self.capability_resolver(
            codex_executable,
            binding.workspace if binding is not None else Path.cwd().resolve(),
        )
        configured = False
        try:
            document = self._read_document()
            configured = _document_has_owned_binding(document, binding_id)
        except StorageError:
            configured = False
        return LifecycleVerification(
            binding_id=binding_id,
            hooks_path=self.hooks_path,
            configured=configured,
            binding_enabled=bool(binding and binding.enabled),
            feature_enabled=capability.feature_enabled,
            codex_version=capability.version,
            feature_evidence=capability.feature_evidence,
        )

    def disable(self, binding_id: str) -> LifecycleBinding:
        return self.state_store.set_binding_enabled(binding_id, False)

    def uninstall(self, binding_id: str) -> None:
        # Disable first so a partially completed uninstall remains fail-safe.
        binding = self.state_store.get_binding(binding_id)
        if binding is not None:
            self.state_store.set_binding_enabled(binding_id, False)
        ensure_directory(self.hooks_path.parent)
        with self._lock:
            document = self._read_document()
            hooks = document.get("hooks")
            if isinstance(hooks, dict):
                groups = hooks.get("PreCompact")
                if isinstance(groups, list):
                    retained_groups: list[object] = []
                    for group in groups:
                        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                            retained_groups.append(group)
                            continue
                        retained_handlers = [
                            handler
                            for handler in group["hooks"]
                            if not _handler_owns_binding(handler, binding_id)
                        ]
                        if len(retained_handlers) == len(group["hooks"]):
                            retained_groups.append(group)
                            continue
                        updated = dict(group)
                        updated["hooks"] = retained_handlers
                        if retained_handlers or set(updated) - {"matcher", "hooks"}:
                            retained_groups.append(updated)
                    hooks["PreCompact"] = retained_groups
            self._write_document(document)
        self.state_store.delete_binding(binding_id)

    def _handler(self, binding: LifecycleBinding, executable: Path) -> dict[str, object]:
        argv = [
            str(executable),
            "--data-root",
            str(self.state_store.data_root),
            "lifecycle",
            "codex",
            "handle",
            "--binding",
            binding.id,
            "--adapter-id",
            ADAPTER_ID,
        ]
        return {
            "type": "command",
            "command": shlex.join(argv),
            "commandWindows": subprocess.list2cmdline(argv),
            "timeout": 120,
            "statusMessage": "Creating a verified Handoff Forge snapshot",
        }

    def _read_document(self) -> dict[str, Any]:
        return self._document_from_bytes(self._snapshot_document()[0])

    def _snapshot_document(self) -> tuple[bytes | None, int | None]:
        """Capture the exact current file so a failed install can restore it."""

        if self.hooks_path.is_symlink():
            raise StorageError("Codex hooks file cannot be a symlink")
        if not self.hooks_path.exists():
            return None, None
        if not self.hooks_path.is_file():
            raise StorageError("Codex hooks path is not a regular file")
        try:
            content = self.hooks_path.read_bytes()
            mode = stat.S_IMODE(self.hooks_path.stat().st_mode)
        except OSError as error:
            raise StorageError("could not read Codex hooks JSON") from error
        return content, mode

    @staticmethod
    def _document_from_bytes(content: bytes | None) -> dict[str, Any]:
        if content is None:
            return {"hooks": {}}
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise StorageError("could not read Codex hooks JSON") from error
        if not isinstance(value, dict):
            raise StorageError("Codex hooks JSON root must be an object")
        if set(value) - {"description", "hooks"}:
            raise StorageError("Codex hooks JSON contains unsupported top-level fields")
        if "description" in value and not isinstance(value["description"], str):
            raise StorageError("Codex hooks description must be a string")
        if "hooks" in value and not isinstance(value["hooks"], dict):
            raise StorageError("Codex hooks root must be an object")
        return value

    def _restore_document(self, snapshot: tuple[bytes | None, int | None]) -> None:
        """Restore and read back the exact pre-install hooks file."""

        content, mode = snapshot
        if content is None:
            if self.hooks_path.is_symlink():
                raise StorageError("Codex hooks rollback target became a symlink")
            self.hooks_path.unlink(missing_ok=True)
            _fsync_directory(self.hooks_path.parent)
            if self.hooks_path.exists() or self.hooks_path.is_symlink():
                raise StorageError("Codex hooks rollback failed readback")
            return
        _atomic_write(self.hooks_path, content)
        if mode is not None:
            os.chmod(self.hooks_path, mode)
        _fsync_directory(self.hooks_path.parent)
        if self.hooks_path.is_symlink() or self.hooks_path.read_bytes() != content:
            raise StorageError("Codex hooks rollback failed readback")

    def _write_document(self, document: Mapping[str, object]) -> None:
        payload = (
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
        )
        _atomic_write(self.hooks_path, payload)


def lifecycle_event_id(
    *,
    binding: LifecycleBinding,
    event: HandoffMode,
    session_id: str,
    turn_id: str,
    trigger: str,
    transcript_revision: str | None = None,
) -> str:
    identity = {
        "adapter": ADAPTER_ID,
        "binding": binding.id,
        "event": event.value,
        "session": session_id,
        "trigger": trigger,
        "turn": turn_id,
        "workspace": str(binding.workspace),
    }
    if transcript_revision is not None:
        identity["transcript_revision"] = transcript_revision
    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"evt_{hashlib.sha256(canonical.encode()).hexdigest()}"


def lifecycle_job_id(event_id: str) -> str:
    _validate_id(event_id, "lifecycle event")
    return f"job-lifecycle-{hashlib.sha256(event_id.encode()).hexdigest()[:48]}"


def parse_codex_version(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None
    match = _VERSION.search(value)
    return tuple(int(item) for item in match.groups()) if match else None  # type: ignore[return-value]


def detect_codex_version(executable: str = "codex", *, cwd: Path | None = None) -> str | None:
    """Read a version without forwarding provider credentials to the subprocess."""

    completed = _run_codex_command(executable, "--version", cwd=cwd)
    if completed is None:
        return None
    if completed.returncode != 0:
        return None
    rendered = f"{completed.stdout}\n{completed.stderr}".strip()
    match = _VERSION.search(rendered)
    return match.group(0) if match else None


def probe_codex_precompact(
    executable: str = "codex",
    *,
    cwd: Path | None = None,
) -> CodexPreCompactCapability:
    """Read the effective hooks feature row without inferring hook trust or delivery."""

    version = detect_codex_version(executable, cwd=cwd)
    completed = _run_codex_command(executable, "features", "list", cwd=cwd)
    if completed is None:
        return CodexPreCompactCapability(
            feature_enabled=None,
            version=version,
            feature_evidence="features-command-unavailable",
        )
    if completed.returncode != 0:
        return CodexPreCompactCapability(
            feature_enabled=None,
            version=version,
            feature_evidence="features-command-failed",
        )
    feature_enabled, evidence = _parse_hooks_feature_row(completed.stdout)
    return CodexPreCompactCapability(
        feature_enabled=feature_enabled,
        version=version,
        feature_evidence=evidence,
    )


def _run_codex_command(
    executable: str,
    *arguments: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.casefold()
        in {
            "path",
            "systemroot",
            "pathext",
            "comspec",
            "tmp",
            "temp",
            "home",
            "userprofile",
            "codex_home",
        }
    }
    try:
        return subprocess.run(  # noqa: S603  # nosec B603
            [executable, *arguments],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=5,
            env=environment,
            cwd=cwd,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _document_has_owned_binding(document: Mapping[str, object], binding_id: str) -> bool:
    hooks = document.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("PreCompact")
    if not isinstance(groups, list):
        return False
    return any(
        _handler_owns_binding(handler, binding_id)
        for group in groups
        if isinstance(group, dict)
        and group.get("matcher") == _HOOK_MATCHER
        and isinstance(group.get("hooks"), list)
        for handler in group["hooks"]
    )


def _handler_owns_binding(handler: object, binding_id: str) -> bool:
    if not isinstance(handler, dict):
        return False
    if handler.get("type") != "command":
        return False
    command = handler.get("command")
    if not isinstance(command, str):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return (
        len(argv) == 10
        and argv[1] == "--data-root"
        and bool(argv[2])
        and argv[3:6] == ["lifecycle", "codex", "handle"]
        and argv[6:] == ["--binding", binding_id, "--adapter-id", ADAPTER_ID]
    )


def _feature_configuration_is_authorized(capability: CodexPreCompactCapability) -> bool:
    """Authorize static configuration only from the official effective feature row."""

    return capability.feature_enabled is True and capability.feature_evidence == "features-list"


def _parse_hooks_feature_row(output: str) -> tuple[bool | None, CodexFeatureEvidence]:
    """Parse exactly one canonical ``hooks stable true|false`` feature row."""

    matching_rows = [line.split() for line in output.splitlines() if line.split()[:1] == ["hooks"]]
    if not matching_rows:
        return None, "hooks-row-missing"
    if len(matching_rows) != 1:
        return None, "hooks-row-malformed"
    fields = matching_rows[0]
    if len(fields) != 3 or fields[1] != "stable" or fields[2] not in {"true", "false"}:
        return None, "hooks-row-malformed"
    return fields[2] == "true", "features-list"


def _hash_regular_transcript(path_value: str | None) -> str | None:
    """Hash a stable regular transcript without persisting its path or contents."""

    if not path_value:
        return None
    try:
        path = Path(path_value).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_absolute():
        return None

    descriptor: int | None = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            return None
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            return None

        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        if opened.st_size != after.st_size or opened.st_mtime_ns != after.st_mtime_ns:
            return None
        return digest.hexdigest()
    except (OSError, ValueError):
        return None
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _resolve_workspace(path: Path) -> Path:
    raw = path.expanduser()
    if raw.is_symlink():
        raise StorageError("lifecycle workspace cannot be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise StorageError("lifecycle workspace is unavailable") from error
    if not resolved.is_dir():
        raise StorageError("lifecycle workspace must be a directory")
    return resolved


def _profile_for_event(
    event: HandoffMode,
) -> Literal["codex-precompact-v1", "codex-post-chat-v1"]:
    if event is HandoffMode.PRE_COMPACT:
        return "codex-precompact-v1"
    if event is HandoffMode.POST_TASK:
        return "codex-post-chat-v1"
    raise StorageError("unsupported lifecycle event")


def _sanitize_failure(error: Exception | str) -> str:
    """Persist a diagnostic category without exception text, paths, or secrets."""

    category = type(error).__name__ if isinstance(error, Exception) else "LifecycleError"
    return f"{category}: lifecycle operation failed"


def _first_string(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _validate_id(value: str, label: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise StorageError(f"invalid {label} identifier")


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise StorageError("atomic target cannot be a symlink")
    parent = ensure_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        enforce_private_file(path)
        _fsync_directory(parent)
        descriptor = -1
    except Exception:
        if descriptor >= 0:
            with suppress(OSError):
                os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
