"""Native Codex model metadata and strict model/effort validation.

This module has no inference or thread-start capability. The native metadata
probe deliberately does NOT use RpcSession's isolated bundled model catalog.
The runtime may still use that catalog for tool isolation after validation.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Any, Iterable


class CatalogError(ValueError):
    """Invalid metadata or a model/effort selection not in the native catalog."""


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise CatalogError(f"{label} must be a nonempty string without surrounding spaces")
    if any(ord(char) < 32 for char in value):
        raise CatalogError(f"{label} contains an invalid control character")
    return value


def normalize_models(raw: Any, *, include_hidden: bool = False,
                     require_text: bool = True,
                     allowed_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Validate native entries while preserving their field names and metadata.

    Accepts an app-server {data:[...]} response, a probe/cache {models:[...]},
    or a list of model objects. Hidden/non-text models are intentionally filtered.
    Unknown model names and new reasoning levels are not invented or hardcoded.
    """
    if isinstance(raw, dict):
        entries = raw.get("models", raw.get("data"))
    else:
        entries = raw
    if not isinstance(entries, list):
        raise CatalogError("The native catalog must contain a models/data array")
    allowed = set(allowed_ids) if allowed_ids is not None else None
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise CatalogError("Every native model entry must be an object")
        hidden = entry.get("hidden", False)
        if not isinstance(hidden, bool):
            raise CatalogError("Model hidden must be a boolean")
        if hidden and not include_hidden:
            continue
        model_id = _identifier(entry.get("id"), "Model id")
        if allowed is not None and model_id not in allowed:
            continue
        modalities = entry.get("inputModalities", ["text", "image"])
        if not isinstance(modalities, list) or any(not isinstance(x, str) for x in modalities):
            raise CatalogError(f"{model_id}: inputModalities must be an array of strings")
        if require_text and "text" not in modalities:
            continue
        if model_id in seen:
            raise CatalogError(f"Duplicate native model id: {model_id}")
        seen.add(model_id)
        actual_model = _identifier(entry.get("model", model_id), f"{model_id} model")
        display_name = _identifier(entry.get("displayName", model_id), f"{model_id} displayName")
        options = entry.get("supportedReasoningEfforts")
        if not isinstance(options, list) or not options:
            raise CatalogError(f"{model_id}: supportedReasoningEfforts must be nonempty")
        efforts = []
        seen_efforts: set[str] = set()
        for option in options:
            if not isinstance(option, dict):
                raise CatalogError(f"{model_id}: every reasoning option must be an object")
            effort = _identifier(option.get("reasoningEffort"), f"{model_id} reasoningEffort")
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", effort):
                raise CatalogError(f"{model_id}: invalid reasoning effort name")
            if effort in seen_efforts:
                raise CatalogError(f"{model_id}: duplicate reasoning effort {effort}")
            seen_efforts.add(effort)
            description = option.get("description", "")
            if not isinstance(description, str):
                raise CatalogError(f"{model_id}: reasoning description must be a string")
            clean = copy.deepcopy(option)
            clean.update(reasoningEffort=effort, description=description)
            efforts.append(clean)
        default_effort = entry.get("defaultReasoningEffort")
        if default_effort not in seen_efforts:
            raise CatalogError(f"{model_id}: defaultReasoningEffort is not supported")
        is_default = entry.get("isDefault", False)
        if not isinstance(is_default, bool):
            raise CatalogError(f"{model_id}: isDefault must be a boolean")
        model = copy.deepcopy(entry)
        model.update(id=model_id, model=actual_model, displayName=display_name,
                     hidden=hidden, isDefault=is_default, inputModalities=list(modalities),
                     supportedReasoningEfforts=efforts,
                     defaultReasoningEffort=default_effort)
        normalized.append(model)
    if not normalized:
        raise CatalogError("No selectable text models were returned by native Codex")
    return normalized


class ModelCatalog:
    def __init__(self, raw: Any, **filters):
        self._models = normalize_models(raw, **filters)
        self._by_id = {model["id"]: model for model in self._models}

    @property
    def model_ids(self) -> list[str]:
        return list(self._by_id)

    @property
    def default_model_id(self) -> str:
        return next((m["id"] for m in self._models if m["isDefault"]), self._models[0]["id"])

    def list_models(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._models)

    def get_model(self, model_id: str) -> dict[str, Any]:
        if not isinstance(model_id, str) or model_id not in self._by_id:
            raise CatalogError(f"Model is not in the current native Codex catalog: {model_id}")
        return copy.deepcopy(self._by_id[model_id])

    def reasoning_options(self, model_id: str) -> list[dict[str, Any]]:
        return self.get_model(model_id)["supportedReasoningEfforts"]

    def validate(self, model_id: str, effort: str | None = None) -> tuple[str, str]:
        model = self.get_model(model_id)
        selected = model["defaultReasoningEffort"] if effort is None else effort
        supported = [item["reasoningEffort"] for item in model["supportedReasoningEfforts"]]
        if selected not in supported:
            raise CatalogError(f"{model_id} does not support reasoning effort {selected!r}; supported: {', '.join(supported)}")
        return model["model"], selected


def _write_cache(path: Path, proof: dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def load_catalog_cache(path: str | Path) -> dict[str, Any]:
    """Read and validate an explicitly saved native-model/list snapshot."""
    proof = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(proof, dict) or proof.get("source") != "codex.app-server.model/list":
        raise CatalogError("Cache is not a native Codex model/list snapshot")
    ModelCatalog(proof)
    return proof


def probe_native_catalog(executable: str, cwd: str, *, cache_path: str | Path | None = None,
                         timeout: float = 45.0, popen_factory=None) -> dict[str, Any]:
    """Read native account/model metadata, atomically save a sanitized cache.

    No custom model catalog, no thread creation, no turns, no inference. The
    cache defaults to cwd/native-model-catalog.json. A failed refresh never
    overwrites a successful cache. popen_factory is for offline transport tests.
    """
    working_dir = Path(cwd).resolve()
    working_dir.mkdir(parents=True, exist_ok=True)
    target = Path(cache_path) if cache_path is not None else working_dir / "native-model-catalog.json"
    from codex_rpc import clean_environment
    env = clean_environment()
    factory = popen_factory or subprocess.Popen
    process = factory([executable, "app-server", "--listen", "stdio://"],
                      cwd=str(working_dir), env=env, stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                      encoding="utf-8", bufsize=1,
                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    messages: queue.Queue = queue.Queue()
    ended = object()

    def read_stdout():
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if isinstance(message, dict):
                    messages.put(message)
        finally:
            messages.put(ended)

    def drain_stderr():
        # Never retain authentication/diagnostic details in the model cache.
        try:
            for _ in process.stderr:
                pass
        except (OSError, ValueError):
            pass

    threading.Thread(target=read_stdout, daemon=True).start()
    if process.stderr is not None:
        threading.Thread(target=drain_stderr, daemon=True).start()
    next_id = 0

    def send(message):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()

    def request(method, params):
        nonlocal next_id
        # A fixed method allowlist makes this probe incapable of starting work.
        if method not in {"initialize", "account/read", "model/list"}:
            raise CatalogError("The metadata probe does not support inference methods")
        next_id += 1
        rpc_id = f"catalog-{next_id}"
        send({"id": rpc_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            try:
                reply = messages.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise CatalogError(f"Native Codex metadata request timed out: {method}") from exc
            if reply is ended:
                raise CatalogError("Native Codex metadata process exited")
            if "method" in reply and "id" in reply:
                send({"id": reply["id"], "error": {"code": -32601, "message": "Read-only model catalog probe"}})
            elif reply.get("id") == rpc_id:
                if "error" in reply:
                    raise CatalogError(f"Native Codex metadata request failed: {method}")
                return reply.get("result", {})
            if time.monotonic() >= deadline:
                raise CatalogError(f"Native Codex metadata request timed out: {method}")

    try:
        initialize = request("initialize", {
            "clientInfo": {"name": "beeftv-native-model-catalog", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True},
        })
        send({"method": "initialized", "params": {}})
        account_response = request("account/read", {"refreshToken": False})
        account = account_response.get("account") or {}
        models = []
        cursor = None
        seen_cursors = set()
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            page = request("model/list", params)
            if not isinstance(page.get("data"), list):
                raise CatalogError("Native model/list returned invalid data")
            models.extend(page["data"])
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen_cursors or len(seen_cursors) >= 100:
                raise CatalogError("Native model/list returned invalid pagination")
            seen_cursors.add(cursor)
        # Validate before replacing a known-good disk snapshot.
        normalized = ModelCatalog(models).list_models()
        proof = {
            "schemaVersion": 1,
            "source": "codex.app-server.model/list",
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "probe": "initialize/account/read/model/list only; no thread/start or turn/start",
            "userAgent": initialize.get("userAgent"),
            "account": {"signedIn": bool(account), "type": account.get("type"),
                        "planType": account.get("planType")},
            "models": normalized,
            "nextCursor": None,
        }
        _write_cache(target, proof)
        return proof
    finally:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
