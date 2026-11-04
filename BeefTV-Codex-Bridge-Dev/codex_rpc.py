"""Local Codex app-server transport for BeefTV. No inference occurs on construction.

Authentication stays inside Codex. This module never reads or copies auth files.
Only start_turn initiates model work; probe only reads account/model metadata.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tomllib
from typing import Any


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def clean_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Keep native account storage, without inheriting this desktop chat's tools."""
    return {
        key: value for key, value in (os.environ if source is None else source).items()
        if not key.upper().startswith("CODEX_") or key.upper() == "CODEX_HOME"
    }


def isolated_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Retain catalog models/instructions, remove native tool-selection metadata."""
    result = copy.deepcopy(catalog)
    if not isinstance(result.get("models"), list) or not result["models"]:
        raise RpcError("Codex did not return a valid model catalog")
    for model in result["models"]:
        model.update({
            "tool_mode": "direct", "shell_type": "disabled",
            "apply_patch_tool_type": None, "experimental_supported_tools": [],
            "multi_agent_version": "disabled",
        })
    return result


def prepare_catalog(executable: str, cwd: str, environment: dict[str, str]) -> str:
    """Read this executable's bundled metadata, not credentials or inference."""
    target = Path(cwd) / "codex-rpc-model-catalog.json"
    marker = Path(cwd) / "codex-rpc-model-catalog.source.json"
    stat = Path(executable).stat()
    signature = {"executable": str(Path(executable).resolve()),
                 "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "format": 1}
    try:
        if target.is_file() and json.loads(marker.read_text(encoding="utf-8")) == signature:
            return str(target)
    except (OSError, ValueError):
        pass
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        [executable, "debug", "models", "--bundled"], cwd=cwd, env=environment,
        capture_output=True, encoding="utf-8", timeout=30, creationflags=flags,
    )
    if completed.returncode:
        raise RpcError("Cannot load the installed Codex model catalog")
    try:
        result = isolated_catalog(json.loads(completed.stdout))
    except (ValueError, TypeError) as exc:
        raise RpcError("The installed Codex model catalog is invalid") from exc
    # replace is atomic; concurrent constructors may safely produce identical data.
    suffix = f".{os.getpid()}.{threading.get_ident()}.tmp"
    temp = target.with_name(target.name + suffix)
    temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)
    temp_marker = marker.with_name(marker.name + suffix)
    temp_marker.write_text(json.dumps(signature), encoding="utf-8")
    temp_marker.replace(marker)
    return str(target)


def isolated_overrides(catalog_path: str, environment: dict[str, str], *,
                       image_generation: bool = False) -> list[str]:
    """Process-only policy. Does not change the user's Codex configuration."""
    cfg: dict[str, Any] = {
        "model_catalog_json": catalog_path,
        "web_search": "disabled", "agents.enabled": False,
        "project_doc_max_bytes": 0,
        "skills.include_instructions": False, "skills.bundled.enabled": False,
        "include_apps_instructions": False, "include_environment_context": False,
        "include_collaboration_mode_instructions": False,
        "tools.update_plan.enabled": False,
        "tools.experimental_request_user_input.enabled": False,
        "plugins._default.enabled": False, "apps._default.enabled": False,
    }
    for feature in (
        "shell_tool", "unified_exec", "shell_snapshot", "view_image",
        "code_mode", "code_mode_host", "code_mode_only", "code_mode_prewarm",
        "multi_agent", "multi_agent_v2", "apps", "plugins", "remote_plugin",
        "skill_search", "skill_mcp_dependency_install", "skill_env_var_dependency_prompt",
        "codex_hooks", "hooks", "plugin_hooks", "memory_tool", "goals",
        "image_generation", "sleep_tool", "request_permissions_tool",
        "token_budget", "current_time_reminder", "send_message_to_user_async",
        "tool_suggest", "artifact", "browser_use", "in_app_browser",
        "deferred_executor", "stable_environment_tools", "workspace_dependencies",
    ):
        cfg[f"features.{feature}"] = False
    cfg["features.skip_host_skill_discovery"] = True
    # Opt in only for the dedicated image adapter. Text/tool sessions keep the
    # original deny-by-default tool surface, including image generation.
    if image_generation:
        cfg["features.image_generation"] = True
    codex_home = Path(environment.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_file = codex_home / "config.toml"
    if config_file.exists():
        # Only configuration is read. auth.json/keychain contents never enter Python.
        user_config = tomllib.loads(config_file.read_text(encoding="utf-8"))
        for name in user_config.get("mcp_servers", {}):
            if "." in name:
                raise RpcError("Cannot safely isolate a Codex MCP server name containing a dot")
            # The CLI dotted-key parser splits literal dots; quoting here creates
            # a NEW server whose name contains quote characters.
            cfg[f"mcp_servers.{name}.enabled"] = False
        for name in user_config.get("plugins", {}):
            if name != "_default":
                if "." in name:
                    raise RpcError("Cannot safely isolate a Codex plugin name containing a dot")
                cfg[f"plugins.{name}.enabled"] = False
        for name in user_config.get("apps", {}):
            if name != "_default":
                if "." in name:
                    raise RpcError("Cannot safely isolate a Codex app name containing a dot")
                cfg[f"apps.{name}.enabled"] = False
    # TOML scalar syntax is identical to JSON for these bool/string/integer values.
    return [f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in cfg.items()]


def normalize_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for entry in tools or []:
        if entry.get("type") == "namespace":
            raise RpcError("Use flat BeefTV function tools for this provider")
        function = entry.get("function", entry)
        name = function.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise RpcError("Tool names must be nonempty and unique")
        seen.add(name)
        schema = function.get("inputSchema", function.get("parameters", {"type": "object", "properties": {}}))
        if not isinstance(schema, dict):
            raise RpcError(f"Tool {name} has an invalid input schema")
        result.append({"type": "function", "name": name,
                       "description": function.get("description", ""),
                       "inputSchema": copy.deepcopy(schema), "deferLoading": False})
    return result


class RpcSession:
    """One child process, one ephemeral thread, asynchronous native tool requests.

    next_event returns original protocol notifications/server requests. A tool
    request's JSON-RPC id must be passed to reply_tool; params.callId is the
    separate ID to present as an OpenAI tool_call.id to BeefTV.
    """

    def __init__(self, executable: str, cwd: str, *, transport=None,
                 request_timeout: float = 45.0, catalog_path: str | None = None,
                 image_generation: bool = False):
        self.executable = executable
        self.cwd = str(Path(cwd).resolve())
        Path(self.cwd).mkdir(parents=True, exist_ok=True)
        self.request_timeout = request_timeout
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._events: queue.Queue = queue.Queue()
        self._pending: dict[Any, queue.Queue] = {}
        self._tool_requests: dict[Any, dict[str, Any]] = {}
        self._allowed_tools: set[str] = set()
        self._counter = 0
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._closed = False
        self._process_exited = False
        if transport is None:
            environment = clean_environment()
            catalog_path = catalog_path or prepare_catalog(executable, self.cwd, environment)
            argv = [executable, "app-server", "--listen", "stdio://"]
            for override in isolated_overrides(catalog_path, environment,
                                               image_generation=image_generation):
                argv.extend(["-c", override])
            self.process = subprocess.Popen(
                argv, cwd=self.cwd, env=environment, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        else:
            # Injectable fake subprocess for tests; no live model calls required.
            self.process = transport
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        if self.process.stderr is not None:
            threading.Thread(target=self._drain_stderr, daemon=True).start()
        try:
            self.initialize_result = self.request("initialize", {
                "clientInfo": {"name": "beeftv-local-codex", "title": "BeefTV Local Codex", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    def _drain_stderr(self):
        # Drain without retaining/logging potentially sensitive diagnostics.
        try:
            for _ in self.process.stderr:
                pass
        except (OSError, ValueError):
            pass

    def _read_loop(self):
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        if message["method"] == "item/tool/call":
                            params = message.get("params") or {}
                            if params.get("tool") not in self._allowed_tools or params.get("namespace"):
                                self._send({"id": message["id"], "result": {
                                    "contentItems": [{"type": "inputText", "text": "Tool not enabled by BeefTV."}],
                                    "success": False}})
                                continue
                            with self._state_lock:
                                self._tool_requests[message["id"]] = message
                        else:
                            # Never auto-approve shell/filesystem/auth/other operations.
                            self._send({"id": message["id"], "error": {
                                "code": -32601, "message": "Only BeefTV dynamic tool calls are enabled."}})
                            continue
                    self._events.put(message)
                elif "id" in message:
                    with self._state_lock:
                        pending = self._pending.get(message["id"])
                    if pending is not None:
                        pending.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._process_exited = True
            with self._state_lock:
                pending = list(self._pending.values())
            for waiter in pending:
                waiter.put({"error": {"message": "Local Codex app-server exited"}})
            if not self._closed:
                self._events.put({"method": "bridge/process/exited", "params": {}})

    def _send(self, message: dict[str, Any]):
        if self._closed or self._process_exited:
            raise RpcError("Local Codex session is closed")
        wire = {"jsonrpc": "2.0", **message}
        with self._write_lock:
            try:
                self.process.stdin.write(json.dumps(wire, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RpcError("Cannot communicate with local Codex") from exc

    def request(self, method: str, params: dict[str, Any], *, timeout: float | None = None):
        with self._state_lock:
            self._counter += 1
            request_id = f"beeftv-{self._counter}"
            waiter: queue.Queue = queue.Queue()
            self._pending[request_id] = waiter
        try:
            self._send({"id": request_id, "method": method, "params": params})
            try:
                result = waiter.get(timeout=timeout or self.request_timeout)
            except queue.Empty as exc:
                raise RpcError(f"Local Codex request timed out: {method}") from exc
            if "error" in result:
                error = result["error"]
                raise RpcError(str(error.get("message", "Local Codex request failed")), error.get("code"))
            return result.get("result", {})
        finally:
            with self._state_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]):
        self._send({"method": method, "params": params})

    def probe(self) -> dict[str, Any]:
        account = self.request("account/read", {"refreshToken": False})
        models = []
        cursor = None
        while True:
            params: dict[str, Any] = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            page = self.request("model/list", params)
            models.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        acct = account.get("account") or {}
        return {"account": {"signedIn": bool(acct), "type": acct.get("type"),
                            "planType": acct.get("planType"),
                            "requiresOpenaiAuth": account.get("requiresOpenaiAuth")},
                "models": models}

    def start_thread(self, model: str, tools=None, base_instructions: str | None = None,
                     developer_instructions: str | None = None) -> str:
        if self.thread_id:
            raise RpcError("This session already has a thread")
        specs = normalize_tools(tools)
        self._allowed_tools = {tool["name"] for tool in specs}
        params = {"model": model, "ephemeral": True, "dynamicTools": specs,
                  "environments": [], "cwd": self.cwd, "sandbox": "read-only",
                  "approvalPolicy": "never", "allowProviderModelFallback": False}
        if base_instructions is not None:
            params["baseInstructions"] = base_instructions
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        result = self.request("thread/start", params)
        self.thread_start_result = result
        self.thread_id = result["thread"]["id"]
        return self.thread_id

    def start_turn(self, input_items: list[dict[str, Any]], effort: str = "high") -> str:
        if not self.thread_id:
            raise RpcError("Create a thread before starting a turn")
        if self._tool_requests:
            raise RpcError("Reply to pending tool calls before starting another turn")
        result = self.request("turn/start", {"threadId": self.thread_id,
                              "input": input_items, "effort": effort, "environments": [],
                              "summary": "auto", "serviceTierForTurn": "default"})
        self.turn_id = result["turn"]["id"]
        return self.turn_id

    def next_event(self, timeout: float | None = None) -> dict[str, Any]:
        return self._events.get(timeout=timeout)

    def reply_tool(self, rpc_id: Any, content_items: list[dict[str, Any]], success: bool = True):
        with self._state_lock:
            if rpc_id not in self._tool_requests:
                raise RpcError("Unknown or already completed tool call")
            self._send({"id": rpc_id, "result": {"contentItems": content_items, "success": bool(success)}})
            del self._tool_requests[rpc_id]

    def cancel(self):
        if self.thread_id and self.turn_id and not self._closed and not self._process_exited:
            self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=10)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
