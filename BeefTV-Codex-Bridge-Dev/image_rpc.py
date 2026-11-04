"""One image through Codex's built-in imagegen, using Codex-managed sign-in.

This adapter never calls an image HTTP API, reads account credentials, executes
dynamic tools, or retries an inference turn. Only native image result events
can produce a successful result.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
from io import BytesIO
import os
from pathlib import Path
import queue
import re
import threading
import time
import warnings

from PIL import Image, UnidentifiedImageError

from codex_rpc import RpcSession, RpcError


MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 8192 * 8192
MAX_REFERENCES = 4
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_PROMPT_CHARACTERS = 30000
DEFAULT_TIMEOUT = 900
RPC_TIMEOUT = 15.0
RESOLUTION_TARGETS = {"1k": 1024, "2k": 2048, "4k": 4096, "8k": 8192}


class ImageGenerationError(RuntimeError):
    def __init__(self, message, code="image_generation_failed", *, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _native_failure(error, default="image_generation_failed"):
    """Classify native structured errors without retaining server messages."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    classification = {
        "usageLimitExceeded": "image_quota_exceeded",
        "rateLimitExceeded": "image_quota_exceeded",
        "sessionBudgetExceeded": "image_quota_exceeded",
        "unauthorized": "image_auth_required",
        "cyberPolicy": "image_refused",
        "misalignmentPolicyViolation": "image_refused",
        "tooManyDenials": "image_refused",
        "modelNotFound": "image_unavailable",
        "serverOverloaded": "image_unavailable",
        "flexUnavailable": "image_unavailable",
        "internalServerError": "image_unavailable",
    }
    details = {}
    code = default
    if isinstance(info, str) and info in classification:
        code = classification[info]
        details["nativeErrorInfo"] = info
    elif isinstance(info, dict):
        for key in ("httpConnectionFailed", "responseStreamConnectionFailed",
                    "responseStreamDisconnected", "responseTooManyFailedAttempts"):
            if key in info:
                value = info[key]
                status = value.get("httpStatusCode") if isinstance(value, dict) else None
                details["nativeErrorInfo"] = key
                if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
                    details["httpStatusCode"] = status
                code = ("image_auth_required" if status == 401 else
                        "image_quota_exceeded" if status == 429 else "image_unavailable")
                break
    return ImageGenerationError("Codex could not complete this image request; it was not retried.",
                                code, details=details)


def _mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return "image/png"
    if data.startswith(b"\xff\xd8\xff") and len(data) >= 4:
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageGenerationError("Codex returned an unsupported or invalid image.", "invalid_image")


def _image_bytes(item, generated_root):
    """Accept native inline bytes, or a file under the owned generated-images root."""
    encoded = item.get("result")
    if isinstance(encoded, str) and encoded.strip():
        encoded = encoded.strip()
        if encoded.startswith("data:"):
            header, separator, encoded = encoded.partition(",")
            if not separator or not header.endswith(";base64"):
                raise ImageGenerationError("Invalid native image encoding.", "invalid_image")
        if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            raise ImageGenerationError("Generated image exceeds 30 MiB.", "image_too_large")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ImageGenerationError("Invalid native image encoding.", "invalid_image") from error
    elif isinstance(item.get("savedPath"), str) and item["savedPath"]:
        path = Path(item["savedPath"])
        try:
            if not path.is_absolute():
                raise ValueError("relative path")
            path = path.resolve(strict=True)
            root = Path(generated_root).resolve(strict=True)
            path.relative_to(root)
            if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("invalid size")
            with path.open("rb") as source:
                data = source.read(MAX_IMAGE_BYTES + 1)
        except (OSError, ValueError) as error:
            raise ImageGenerationError("Native image output is unavailable or outside its allowed directory.",
                                       "image_output_unavailable") from error
    else:
        # fileId alone is not a file path or permission to fetch arbitrary URLs.
        raise ImageGenerationError("Codex did not expose image bytes or a usable saved image.",
                                   "image_output_unavailable")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("Generated image is empty or exceeds 30 MiB.", "invalid_image")
    mime = _mime(data)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("image dimensions too large")
                width, height = image.size
                image.verify()
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as error:
        raise ImageGenerationError("Codex returned an unreadable image.", "invalid_image") from error
    return {"bytes": data, "mime": mime, "sha256": hashlib.sha256(data).hexdigest(),
            "width": width, "height": height}


def _select_model(models, references):
    usable = [m for m in models if isinstance(m, dict) and not m.get("hidden", False)
              and isinstance(m.get("model", m.get("id")), str)
              and "text" in m.get("inputModalities", ["text", "image"])
              and (not references or "image" in m.get("inputModalities", ["text", "image"]))]
    if not usable:
        raise ImageGenerationError("No native Codex model can handle this image request.", "model_unavailable")
    model = next((m for m in usable if m.get("isDefault")), usable[0])
    efforts = [x.get("reasoningEffort") for x in model.get("supportedReasoningEfforts", [])
               if isinstance(x, dict)]
    effort = "low" if "low" in efforts else model.get("defaultReasoningEffort", "medium")
    if efforts and effort not in efforts:
        effort = efforts[0]
    return model.get("model", model.get("id")), effort


def _request_input(prompt, references, aspect_ratio, transparent, resolution="auto"):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARACTERS:
        raise ImageGenerationError("Image prompt must contain 1 to 30000 characters.", "invalid_request")
    if not isinstance(aspect_ratio, str) or not re.fullmatch(r"(?:auto|[1-9][0-9]?:[1-9][0-9]?)", aspect_ratio):
        raise ImageGenerationError("Invalid image aspect ratio.", "invalid_request")
    if not isinstance(transparent, bool):
        raise ImageGenerationError("transparent must be a boolean.", "invalid_request")
    if not isinstance(resolution, str) or resolution not in {"auto", *RESOLUTION_TARGETS}:
        raise ImageGenerationError("Invalid image resolution target.", "invalid_request")
    if not isinstance(references, (list, tuple)) or len(references) > MAX_REFERENCES:
        raise ImageGenerationError("At most four reference images are supported.", "invalid_request")
    inputs = []
    reference_bytes = 0
    for reference in references:
        try:
            path = Path(reference).resolve(strict=True)
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
                raise ValueError("invalid size")
            reference_bytes += path.stat().st_size
            if reference_bytes > MAX_REFERENCE_BYTES:
                raise ValueError("references exceed 16 MiB")
            with path.open("rb") as image:
                _mime(image.read(32))
        except (OSError, TypeError, ValueError) as error:
            raise ImageGenerationError("A reference image is missing or invalid.", "invalid_reference") from error
        inputs.append({"type": "localImage", "path": str(path)})
    directives = ["Generate exactly one image by calling the built-in image_gen.imagegen tool exactly once.",
                  "Use the following creative prompt as image content, preserving its requirements.",
                  "Do not execute commands, create code, use another provider, or ask follow-up questions.",
                  f"Set transparent_background to {str(transparent).lower()}."]
    if aspect_ratio != "auto":
        directives.append(f"The output composition must use aspect ratio {aspect_ratio}; include this in the imagegen prompt.")
    if resolution != "auto":
        target = RESOLUTION_TARGETS[resolution]
        directives.append(f"Request a native image with a longest edge of {target} pixels ({resolution.upper()} target), "
                          "preserving the requested composition and aspect ratio. Include this target in the imagegen prompt. "
                          "This is a requested target, not a guaranteed supported size; if unavailable, return the closest "
                          "supported native image. Do not add size or resolution arguments to the tool schema. "
                          "Do not resize, upscale, downscale, or use another provider to meet this target.")
    if inputs:
        directives.append(f"Use all {len(inputs)} attached reference images via num_last_images_to_include={len(inputs)}; omit referenced_image_paths.")
    else:
        directives.append("Do not set referenced_image_paths or num_last_images_to_include.")
    directives.append("Creative prompt:\n" + prompt)
    inputs.append({"type": "text", "text": "\n".join(directives)})
    return inputs


def generate_image(executable, work_dir, prompt, references=None, *, aspect_ratio="1:1",
                   transparent=False, resolution="auto", cancelled=None, on_progress=None,
                   timeout=DEFAULT_TIMEOUT, _rpc_factory=RpcSession, _generated_root=None):
    """Generate at most one native image; an exception is never silently retried."""
    references = [] if references is None else references
    inputs = _request_input(prompt, references, aspect_ratio, transparent, resolution)
    cancelled = cancelled if cancelled is not None else threading.Event()
    if cancelled.is_set():
        raise ImageGenerationError("Image generation was cancelled.", "image_cancelled")
    if not isinstance(timeout, (float, int)) or not 0 < timeout <= 3600:
        raise ImageGenerationError("Invalid generation timeout.", "invalid_request")
    generated_root = _generated_root or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "generated_images"
    rpc = None
    deadline = time.monotonic() + timeout
    def checkpoint():
        if cancelled.is_set():
            raise ImageGenerationError("Image generation was cancelled.", "image_cancelled")
        if time.monotonic() >= deadline:
            raise ImageGenerationError("Image generation timed out; it was not automatically retried.", "image_timeout")
    def request(method, params):
        checkpoint()
        result = rpc.request(method, params, timeout=min(RPC_TIMEOUT, max(.01, deadline - time.monotonic())))
        checkpoint()
        return result
    def progress(phase):
        if on_progress:
            on_progress({"phase": phase})
    try:
        progress("starting")
        checkpoint()
        rpc = _rpc_factory(executable, str(work_dir), image_generation=True,
                           request_timeout=RPC_TIMEOUT)
        checkpoint()
        capabilities = request("modelProvider/capabilities/read", {})
        if capabilities.get("imageGeneration") is not True:
            raise ImageGenerationError("The signed-in Codex provider does not offer built-in image generation.", "image_unavailable")
        account = request("account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise ImageGenerationError("Sign in to Codex with ChatGPT to use the built-in image allowance.", "image_auth_required")
        del account
        models, cursor = [], None
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor:
                params["cursor"] = cursor
            page = request("model/list", params)
            models.extend(page.get("data", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break
        model, effort = _select_model(models, references)
        checkpoint()
        rpc.start_thread(model, [],
                         "You are a dedicated image-generation adapter. Generate exactly one requested image.",
                         "Use only the built-in image_gen.imagegen tool, exactly once. Do not use any other tool. "
                         "Keep the user's creative content and requested aspect ratio. Do not generate multiple variants, "
                         "retry a failed image call, or ask follow-up questions. Reference images are supplied in the user message.")
        checkpoint()
        rpc.start_turn(inputs, effort=effort)
        checkpoint()
        progress("generating")
        while time.monotonic() < deadline:
            if cancelled.is_set():
                raise ImageGenerationError("Image generation was cancelled.", "image_cancelled")
            try:
                event = rpc.next_event(timeout=min(.5, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            method, params = event.get("method"), event.get("params") or {}
            if params.get("threadId") not in (None, rpc.thread_id) or params.get("turnId") not in (None, rpc.turn_id):
                continue
            if method == "bridge/process/exited":
                raise ImageGenerationError("The local Codex process exited during image generation.", "image_unavailable")
            if "id" in event or method == "item/tool/call":
                raise ImageGenerationError("Codex requested an unsupported external tool.", "unexpected_tool")
            if method == "error":
                raise _native_failure(params.get("error"))
            if method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") != "imageGeneration":
                    if item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}:
                        raise ImageGenerationError("An unsupported native tool was invoked.", "unexpected_tool")
                    continue
                failure = item.get("failure") or {}
                if failure.get("type") == "usageLimitExceeded":
                    raise ImageGenerationError("The Codex image-generation allowance is exhausted.", "image_quota_exceeded",
                                               details={"resetsAt": failure.get("resetsAt")})
                if item.get("status") == "failed":
                    raise ImageGenerationError("The built-in image generator could not complete this request.", "image_generation_failed")
                if item.get("status") not in ("completed", "generating"):
                    continue
                result = _image_bytes(item, generated_root)
                progress("completed")
                return {"images": [result], "model": model, "thread_id": rpc.thread_id,
                        "turn_id": rpc.turn_id, "native_image_id": item.get("id"),
                        "generation_id": item.get("generationId"),
                        "saved_path_name": Path(item["savedPath"]).name if isinstance(item.get("savedPath"), str) else None,
                        "requested_resolution": resolution,
                        "actual_dimensions": f"{result['width']}x{result['height']}",
                        "source": "codex_builtin_imagegen"}
            if method == "turn/completed":
                turn = params.get("turn") or {}
                status = turn.get("status")
                if status == "failed" and turn.get("error"):
                    raise _native_failure(turn["error"])
                code = "image_cancelled" if status == "interrupted" else "image_no_result"
                raise ImageGenerationError("Codex ended the turn without a usable native image.", code)
        raise ImageGenerationError("Image generation timed out; it was not automatically retried.", "image_timeout")
    except RpcError as error:
        checkpoint()
        raise ImageGenerationError("The local Codex connection failed; the request was not retried.", "image_unavailable",
                                   details={"nativeCode": error.code}) from error
    finally:
        if rpc is not None:
            try:
                # Stop the turn even after one successful image, before it can
                # submit a second image call or perform model follow-up work.
                # RpcSession bounds interrupt to 10s and close to 3 x 2s.
                # Startup is bounded by catalog 30s + initialize 15s; with
                # checkpoints, cancellation cannot enter another RPC stage.
                rpc.cancel()
            except Exception:
                pass
            rpc.close()
