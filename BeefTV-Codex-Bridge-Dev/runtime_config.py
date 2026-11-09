"""Resolve the native Codex binary and workspace before starting any RPC."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess

from codex_rpc import clean_environment


@dataclass(frozen=True)
class CodexRuntime:
    executable: Path
    version: str


def _absolute_path(value, field):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise RuntimeError(f"{field} must be an absolute path")
    return Path(value).resolve()


def _version(executable, work):
    try:
        result = subprocess.run(
            [str(executable), "--version"], cwd=work, env=clean_environment(),
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.fullmatch(r"codex-cli (\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.+-]+)?)", result.stdout.strip())
    return match[1] if result.returncode == 0 and match else None


def _installed_executables(install_root):
    name = "codex.exe" if os.name == "nt" else "codex"
    candidates = []
    for path in (install_root / name, *install_root.glob(f"*/{name}")):
        resolved = path.resolve()
        if resolved.is_relative_to(install_root) and resolved.is_file():
            candidates.append((resolved.stat().st_mtime_ns, resolved))
    return [path for _, path in sorted(candidates, reverse=True)]


def configure_runtime(root, work):
    config = json.loads((root / "runtime-config.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise RuntimeError("runtime-config.json must contain an object")
    if config.get("codexHome") is not None:
        home = _absolute_path(config["codexHome"], "codexHome")
        if not home.is_dir():
            raise RuntimeError("codexHome must be an existing directory")
        temporary = (home / ".tmp").resolve()
        if not temporary.is_relative_to(home):
            raise RuntimeError("Codex TEMP must stay inside codexHome")
        temporary.mkdir(exist_ok=True)
        os.environ.update(CODEX_HOME=str(home), TEMP=str(temporary), TMP=str(temporary))

    pinned = config.get("codexExecutable")
    if pinned is not None:
        executable = _absolute_path(pinned, "codexExecutable")
        if executable.is_file():
            version = _version(executable, work)
            if version is None:
                raise RuntimeError("Configured Codex executable did not report a valid version")
            return CodexRuntime(executable, version)

    configured_root = config.get("codexInstallRoot")
    if configured_root is None:
        raise RuntimeError("Codex executable is missing; configure an installed codexExecutable or codexInstallRoot")
    install_root = _absolute_path(configured_root, "codexInstallRoot")
    if not install_root.is_dir():
        raise RuntimeError("codexInstallRoot must be an existing directory")
    for executable in _installed_executables(install_root):
        version = _version(executable, work)
        if version is not None:
            return CodexRuntime(executable, version)
    raise RuntimeError("No installed Codex executable reported a valid version")
