import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from completion_bridge import BridgeError
from image_jobs import ImageJobs, normalize_request


buffer = io.BytesIO()
Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
PNG = buffer.getvalue()
DATA_URL = "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")


def payload(prompt="test", **changes):
    return {"model": "codex-image", "prompt": prompt, **changes}


def output():
    return {"images": [{"bytes": PNG, "mime": "image/png"}]}


def wait_for(check, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("condition did not become true")


class ImageJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def jobs(self, generator=lambda **kwargs: output(), **options):
        jobs = ImageJobs(self.root, generator, **options)
        self.addCleanup(jobs.close)
        return jobs

    def finished(self, jobs, job_id):
        return wait_for(lambda: (row if (row := jobs.get(job_id))["status"] not in
                                ("queued", "running") else None))

    def test_all_eight_builtin_aspect_ratios_normalize_without_generation(self):
        for ratio in ("9:16", "16:9", "1:1", "3:4", "4:3", "2:3", "3:2", "21:9"):
            with self.subTest(ratio=ratio):
                request, encoded, _, references = normalize_request(payload(aspectRatio=ratio))
                self.assertEqual(request["aspectRatio"], ratio)
                self.assertEqual(json.loads(encoded)["aspectRatio"], ratio)
                self.assertEqual(references, [])

    def test_auto_resolution_preserves_legacy_fingerprint_and_persistent_key(self):
        legacy = ('{"aspectRatio":"1:1","model":"codex-image","prompt":"test",'
                  '"references":[],"transparentBackground":false}')
        for body in (payload(), payload(resolution="auto")):
            request, encoded, digest, _ = normalize_request(body)
            self.assertNotIn("resolution", request)
            self.assertEqual(encoded, legacy)
            self.assertEqual(digest, hashlib.sha256(legacy.encode("utf-8")).hexdigest())
        calls = []
        jobs = self.jobs(lambda **kwargs: (calls.append(kwargs), output())[1])
        row = jobs.create(payload(), "legacy-key")
        result = self.finished(jobs, row["id"])
        self.assertEqual(calls[0]["resolution"], "auto")
        jobs.close()
        restored = self.jobs(lambda **kwargs: calls.append(kwargs))
        self.assertEqual(restored.create(payload(resolution="auto"), "legacy-key"), result)
        self.assertEqual(len(calls), 1)

    def test_resolution_forwarding_conflict_and_actual_dimensions_receipt(self):
        calls = []
        def generator(**kwargs):
            calls.append(kwargs)
            return {**output(), "requested_resolution": kwargs["resolution"], "actual_dimensions": "2x2"}
        jobs = self.jobs(generator)
        for resolution in ("1k", "2k", "4k", "8k"):
            row = jobs.create(payload(resolution=resolution), f"target-{resolution}")
            result = self.finished(jobs, row["id"])
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(calls[-1]["resolution"], resolution)
            with jobs.condition:
                stored = json.loads(jobs._row(row["id"])["request_json"])
            self.assertEqual(stored["resolution"], resolution)
            self.assertEqual(result["metadata"]["requested_resolution"], resolution)
            self.assertEqual(result["metadata"]["actual_dimensions"], "2x2")
            self.assertIn(f"请求目标：{resolution.upper()}", result["message"])
            self.assertIn("实际尺寸：2x2", result["message"])
            self.assertIn("目标分辨率不保证达到", result["message"])
            self.assertEqual(result["images"][0]["dataUrl"], DATA_URL)
        with self.assertRaises(BridgeError) as caught:
            jobs.create(payload(resolution="8k"), "target-2k")
        self.assertEqual(caught.exception.code, "image_idempotency_conflict")
        self.assertEqual(len(calls), 4)
        jobs.close()
        restored = self.jobs()
        self.assertEqual(restored.get(row["id"]), result)

    def test_8192_square_output_is_valid_but_reference_pixel_limit_is_unchanged(self):
        large = io.BytesIO()
        with Image.new("1", (8192, 8192)) as picture:
            picture.save(large, format="PNG")
        data = large.getvalue()
        reference = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        with self.assertRaises(BridgeError):
            normalize_request(payload(images=[reference]))
        jobs = self.jobs(lambda **kwargs: {"images": [{"bytes": data, "mime": "image/png"}]})
        row = jobs.create(payload(resolution="8k"), "8k-pixels")
        result = self.finished(jobs, row["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["metadata"]["actual_dimensions"], "8192x8192")
        self.assertEqual(base64.b64decode(result["images"][0]["dataUrl"].split(",", 1)[1]), data)

    def test_concurrent_same_key_is_one_generation_and_persistent_result(self):
        calls = []
        release = threading.Event()
        def generator(**kwargs):
            calls.append(kwargs)
            release.wait(3)
            return output()
        jobs = self.jobs(generator)
        with ThreadPoolExecutor(max_workers=12) as pool:
            rows = list(pool.map(lambda _: jobs.create(payload(images=[DATA_URL]), "same-key"), range(30)))
        self.assertEqual(len({row["id"] for row in rows}), 1)
        wait_for(lambda: len(calls) == 1)
        self.assertEqual(calls[0]["references"][0].read_bytes(), PNG)
        self.assertEqual(calls[0]["aspect_ratio"], "1:1")
        release.set()
        result = self.finished(jobs, rows[0]["id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["images"], [{"dataUrl": DATA_URL, "mimeType": "image/png"}])
        self.assertEqual(jobs.create(payload(images=[DATA_URL]), "same-key"), result)
        jobs.close()
        restored_calls = []
        restored = self.jobs(lambda **kwargs: restored_calls.append(kwargs))
        self.assertEqual(restored.get(result["id"]), result)
        self.assertEqual(restored.create(payload(images=[DATA_URL]), "same-key"), result)
        self.assertEqual(restored_calls, [])

    def test_key_conflict_and_missing_key_are_rejected_without_generation(self):
        calls = []
        jobs = self.jobs(lambda **kwargs: (calls.append(kwargs), output())[1])
        row = jobs.create(payload(), "stable-key")
        self.finished(jobs, row["id"])
        with self.assertRaises(BridgeError) as caught:
            jobs.create(payload("changed"), "stable-key")
        self.assertEqual(caught.exception.status, 409)
        for key in (None, "", " ", "x" * 201, "line\nbreak"):
            with self.assertRaises(BridgeError): jobs.create(payload(), key)
        self.assertEqual(len(calls), 1)

    def test_queue_limit_and_cancel_queued_does_not_generate(self):
        calls, entered, release = [], threading.Event(), threading.Event()
        def generator(**kwargs):
            calls.append(kwargs["prompt"])
            entered.set()
            release.wait(3)
            return output()
        jobs = self.jobs(generator, max_pending=2)
        first = jobs.create(payload("first"), "first")
        self.assertTrue(entered.wait(2))
        second = jobs.create(payload("second"), "second")
        with self.assertRaises(BridgeError) as caught: jobs.create(payload("third"), "third")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(jobs.cancel(second["id"])["status"], "cancelled")
        release.set()
        self.assertEqual(self.finished(jobs, first["id"])["status"], "succeeded")
        self.assertEqual(jobs.active_count, 0)
        self.assertEqual(calls, ["first"])
        self.assertEqual(jobs.create(payload("second"), "second")["status"], "cancelled")

    def test_cancel_running_signals_generator_and_cannot_become_success(self):
        entered, observed = threading.Event(), threading.Event()
        def generator(**kwargs):
            entered.set()
            if kwargs["cancelled"].wait(3): observed.set()
            return output()
        jobs = self.jobs(generator)
        row = jobs.create(payload(), "running-cancel")
        self.assertTrue(entered.wait(2))
        self.assertEqual(jobs.cancel(row["id"])["status"], "cancelled")
        self.assertTrue(observed.wait(2))
        wait_for(lambda: jobs.active_count == 0)
        self.assertEqual(jobs.get(row["id"])["status"], "cancelled")
        self.assertEqual(jobs.get(row["id"])["images"], [])

    def test_process_crash_marks_running_and_queued_failed_without_replay(self):
        source = str(Path(__file__).resolve().parents[1])
        code = """
import json,os,sys,threading,time
sys.path.insert(0,sys.argv[1])
from image_jobs import ImageJobs
started=threading.Event()
def blocked(**kwargs):
    started.set(); threading.Event().wait(60)
jobs=ImageJobs(sys.argv[2],blocked)
first=jobs.create({'model':'codex-image','prompt':'running'},'running')
assert started.wait(3)
second=jobs.create({'model':'codex-image','prompt':'queued'},'queued')
print(json.dumps([first['id'],second['id']]),flush=True)
os._exit(0)
"""
        completed = subprocess.run([sys.executable, "-c", code, source, str(self.root)],
                                   capture_output=True, text=True, timeout=8,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        ids = json.loads(completed.stdout)
        calls = []
        jobs = self.jobs(lambda **kwargs: calls.append(kwargs))
        for job_id in ids:
            row = jobs.get(job_id)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"]["code"], "image_service_restarted")
        self.assertEqual(jobs.create(payload("running"), "running")["id"], ids[0])
        self.assertEqual(jobs.create(payload("queued"), "queued")["id"], ids[1])
        self.assertEqual(calls, [])

    def test_close_signals_current_and_preserves_failed_admission(self):
        entered, cancelled = threading.Event(), threading.Event()
        def generator(**kwargs):
            entered.set()
            if kwargs["cancelled"].wait(3): cancelled.set()
            return output()
        jobs = self.jobs(generator)
        first = jobs.create(payload("current"), "current")
        self.assertTrue(entered.wait(2))
        second = jobs.create(payload("queued"), "queued")
        jobs.close()
        self.assertTrue(cancelled.is_set())
        self.assertFalse(jobs.worker.is_alive())
        with self.assertRaises(BridgeError) as caught: jobs.create(payload(), "new")
        self.assertEqual(caught.exception.status, 503)
        restored = self.jobs()
        for row in (first, second):
            self.assertEqual(restored.get(row["id"])["error"]["code"], "image_service_stopped")

    def test_invalid_images_requests_and_paths_never_generate(self):
        calls = []
        jobs = self.jobs(lambda **kwargs: calls.append(kwargs))
        invalid = [payload(images=["http://127.0.0.1/secret"]), payload(images=["C:/secret.png"]),
                   payload(images=["data:image/svg+xml;base64,PHN2Zz4="]),
                   payload(images=[DATA_URL.replace("image/png", "image/jpeg")]),
                   payload(images=["data:image/png;base64,a==="]), payload(images=[DATA_URL] * 5),
                   payload(images=[{"path": "C:/secret.png"}]), payload(images=None),
                   payload(prompt=""), payload(prompt="x" * 30001), payload(aspectRatio=[]),
                   payload(aspectRatio="900:1"), payload(transparentBackground="false"),
                   payload(resolution=[]), payload(resolution=None), payload(resolution="16k"),
                   payload(resolution="8K"),
                   payload(n=5), {"model": "other", "prompt": "test"}]
        for number, body in enumerate(invalid):
            with self.subTest(number=number), self.assertRaises(BridgeError):
                jobs.create(body, f"invalid-{number}")
        with patch("image_jobs.MAX_REFERENCE", 32):
            with self.assertRaises(BridgeError): jobs.create(payload(images=[DATA_URL]), "large-single")
        with patch("image_jobs.MAX_REFERENCES_TOTAL", len(PNG)):
            with self.assertRaises(BridgeError): jobs.create(payload(images=[DATA_URL, DATA_URL]), "large-total")
        for job_id in ("../secret", "a" * 32, 42):
            with self.assertRaises(BridgeError) as caught: jobs.get(job_id)
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(calls, [])
        self.assertEqual(jobs.active_count, 0)

    def test_no_result_invalid_image_and_untrusted_errors_are_not_success(self):
        responses = [[], {"images": []}, {"images": [{"bytes": b"not an image", "mime": "image/png"}]},
                     {"images": output()["images"] * 2},
                     RuntimeError("secret prompt token sk-do-not-copy"),
                     BridgeError("secret prompt token sk-do-not-copy", 500, "image_timeout")]
        def generator(**kwargs):
            result = responses[int(kwargs["prompt"])]
            if isinstance(result, Exception): raise result
            kwargs["on_progress"]("secret prompt token sk-do-not-copy")
            return result
        jobs = self.jobs(generator)
        for number in range(len(responses)):
            row = jobs.create(payload(str(number)), f"failure-{number}")
            result = self.finished(jobs, row["id"])
            self.assertEqual(result["status"], "failed")
            self.assertNotIn("sk-do-not-copy", json.dumps(result))
            self.assertEqual(result["images"], [])
        self.assertEqual(result["error"]["code"], "image_timeout")

    def test_changed_result_file_reports_error_without_regeneration(self):
        calls = []
        jobs = self.jobs(lambda **kwargs: (calls.append(kwargs), output())[1])
        row = jobs.create(payload(), "saved")
        self.finished(jobs, row["id"])
        (jobs.directory / row["id"] / "output.png").write_bytes(b"modified")
        with self.assertRaises(BridgeError) as caught: jobs.get(row["id"])
        self.assertEqual(caught.exception.code, "image_result_unavailable")
        self.assertEqual(len(calls), 1)

    def test_second_owner_cannot_reset_live_jobs(self):
        entered, release = threading.Event(), threading.Event()
        def generator(**kwargs):
            entered.set()
            release.wait(3)
            return output()
        jobs = self.jobs(generator)
        row = jobs.create(payload(), "live")
        self.assertTrue(entered.wait(2))
        with self.assertRaises(BridgeError) as caught:
            ImageJobs(self.root, lambda **kwargs: output())
        self.assertEqual(caught.exception.code, "image_store_busy")
        self.assertEqual(jobs.get(row["id"])["status"], "running")
        release.set()
        self.assertEqual(self.finished(jobs, row["id"])["status"], "succeeded")

    def test_native_receipt_metadata_is_allowlisted_and_persisted(self):
        receipt = {**output(), "model": "native-model", "thread_id": "thread-123",
                   "turn_id": "turn-456", "native_image_id": "image-789", "generation_id": "generation-123",
                   "saved_path_name": "result.png", "source": "saved_path",
                   "requested_resolution": "auto", "actual_dimensions": "2x2",
                   "prompt": "private prompt", "token": "private token", "saved_path": "C:/private/result.png"}
        jobs = self.jobs(lambda **kwargs: receipt)
        row = jobs.create(payload(), "receipt")
        result = self.finished(jobs, row["id"])
        self.assertEqual(set(result["metadata"]), {"model", "thread_id", "turn_id", "native_image_id",
                                                  "generation_id", "saved_path_name", "source",
                                                  "requested_resolution", "actual_dimensions"})
        self.assertNotIn("private", json.dumps(result))
        jobs.close()
        restored = self.jobs()
        self.assertEqual(restored.get(row["id"])["metadata"], result["metadata"])

    def test_close_waits_for_native_cleanup_longer_than_old_five_second_limit(self):
        entered, cleaned = threading.Event(), threading.Event()
        def generator(**kwargs):
            entered.set()
            kwargs["cancelled"].wait(3)
            time.sleep(5.2)  # Simulates a slow but bounded native interrupt/cleanup.
            cleaned.set()
            return output()
        jobs = self.jobs(generator)
        row = jobs.create(payload(), "slow-cleanup")
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        jobs.close()
        self.assertGreaterEqual(time.monotonic() - start, 5)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(jobs.worker.is_alive())
        restored = self.jobs()
        self.assertEqual(restored.get(row["id"])["error"]["code"], "image_service_stopped")

    def test_close_timeout_is_explicit_and_does_not_release_live_owner(self):
        entered, release = threading.Event(), threading.Event()
        def generator(**kwargs):
            entered.set()
            release.wait(3)
            return output()
        jobs = self.jobs(generator)
        self.addCleanup(release.set)
        jobs.create(payload(), "stuck-cleanup")
        self.assertTrue(entered.wait(2))
        with self.assertRaises(BridgeError) as caught:
            jobs.close(timeout=.05)
        self.assertEqual(caught.exception.code, "image_shutdown_timeout")
        self.assertTrue(jobs.worker.is_alive())
        with self.assertRaises(BridgeError) as caught:
            ImageJobs(self.root, lambda **kwargs: output())
        self.assertEqual(caught.exception.code, "image_store_busy")
        release.set()
        jobs.close(timeout=2)
        self.assertFalse(jobs.worker.is_alive())


if __name__ == "__main__":
    unittest.main()
