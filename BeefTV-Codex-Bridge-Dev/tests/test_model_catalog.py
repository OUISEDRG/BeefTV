import copy
import json
from pathlib import Path
import queue
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_catalog import CatalogError, ModelCatalog, normalize_models, probe_native_catalog, load_catalog_cache


def fixture(model_id="test-astra", efforts=("low", "medium", "high", "ultra"), default="medium", **extra):
    return {"id": model_id, "model": model_id, "displayName": model_id,
            "hidden": False, "isDefault": True, "inputModalities": ["text", "image"],
            "defaultReasoningEffort": default,
            "supportedReasoningEfforts": [{"reasoningEffort": x, "description": x} for x in efforts], **extra}


class FakeOutput:
    def __init__(self):
        self.lines = queue.Queue()
    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line


class FakeInput:
    def __init__(self, process):
        self.process = process
    def write(self, data):
        self.process.receive(json.loads(data))
    def flush(self):
        pass
    def close(self):
        self.process.stdout.lines.put(None)


class FakeProcess:
    def __init__(self):
        self.stdout = FakeOutput()
        self.stdin = FakeInput(self)
        self.stderr = None
        self.messages = []
    def receive(self, message):
        self.messages.append(message)
        method = message.get("method")
        results = {
            "initialize": {"userAgent": "fake-codex"},
            "account/read": {"account": {"type": "chatgpt", "planType": "pro", "email": "not-for-cache@example.com"}},
            "model/list": {"data": [fixture()], "nextCursor": None},
        }
        if method in results:
            self.stdout.lines.put(json.dumps({"id": message["id"], "result": results[method]}) + "\n")
    def wait(self, timeout=None):
        return 0
    def terminate(self):
        self.stdout.lines.put(None)
    kill = terminate


class CatalogTests(unittest.TestCase):
    def test_native_defaults_and_exact_effort_are_retained(self):
        catalog = ModelCatalog({"data": [fixture()]})
        self.assertEqual(catalog.validate("test-astra"), ("test-astra", "medium"))
        self.assertEqual(catalog.validate("test-astra", "high"), ("test-astra", "high"))
        self.assertEqual(catalog.model_ids, ["test-astra"])
        self.assertEqual(catalog.default_model_id, "test-astra")
        self.assertEqual(catalog.reasoning_options("test-astra")[-1]["reasoningEffort"], "ultra")
    def test_unknown_model_and_unsupported_effort_fail_without_fallback(self):
        catalog = ModelCatalog([fixture("test-luna", ("low", "medium", "high"))])
        with self.assertRaises(CatalogError):
            catalog.validate("test-luna", "ultra")
        with self.assertRaises(CatalogError):
            catalog.validate("not-in-native-list", "high")
        with self.assertRaises(CatalogError):
            catalog.validate("test-luna", "")
    def test_hidden_nontext_and_id_filters(self):
        raw = [fixture(), fixture("hidden", hidden=True), fixture("audio", inputModalities=["audio"])]
        self.assertEqual([m["id"] for m in normalize_models(raw)], ["test-astra"])
        self.assertEqual(len(normalize_models(raw, include_hidden=True)), 2)
        self.assertEqual([m["id"] for m in normalize_models(raw, include_hidden=True, allowed_ids=["hidden"])], ["hidden"])
    def test_invalid_defaults_duplicates_and_schema_rejected(self):
        for raw in [[fixture(default="max")], [fixture(), fixture()], [fixture(supportedReasoningEfforts=[])], {"models": "invalid"}]:
            with self.subTest(raw=raw), self.assertRaises(CatalogError):
                ModelCatalog(raw)
    def test_returned_data_cannot_mutate_catalog(self):
        raw = [fixture()]
        catalog = ModelCatalog(raw)
        raw[0]["defaultReasoningEffort"] = "high"
        catalog.get_model("test-astra")["supportedReasoningEfforts"].clear()
        self.assertEqual(catalog.validate("test-astra"), ("test-astra", "medium"))
    def test_probe_never_starts_inference_or_uses_bundled_override(self):
        fake = FakeProcess()
        captured = {}
        def factory(args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            return fake
        with tempfile.TemporaryDirectory() as directory:
            proof = probe_native_catalog("fake-codex", directory, popen_factory=factory)
            cached = load_catalog_cache(Path(directory) / "native-model-catalog.json")
            self.assertEqual(proof, cached)
            self.assertNotIn("email", proof["account"])
            self.assertEqual(proof["source"], "codex.app-server.model/list")
        self.assertEqual(captured["args"], ["fake-codex", "app-server", "--listen", "stdio://"])
        self.assertEqual([m["method"] for m in fake.messages], ["initialize", "initialized", "account/read", "model/list"])
        self.assertNotIn("CODEX_APP_TOOLS_PIPE_PATH", captured["kwargs"]["env"])
    def test_actual_readonly_fixture_has_eight_models_and_model_specific_efforts(self):
        path = Path(__file__).resolve().parents[1] / "codex-models-native-readonly-proof.json"
        if not path.exists():
            self.skipTest("Machine-specific readonly probe fixture is not shipped")
        catalog = ModelCatalog(json.loads(path.read_text(encoding="utf-8")))
        self.assertEqual(len(catalog.model_ids), 8)
        self.assertNotIn("gpt-5.5", catalog.model_ids)
        self.assertEqual(catalog.validate("gpt-6-astra"), ("gpt-6-astra", "medium"))
        self.assertEqual(catalog.validate("gpt-6-astra", "high"), ("gpt-6-astra", "high"))
        with self.assertRaises(CatalogError):
            catalog.validate("gpt-6-luna", "ultra")
        self.assertEqual(catalog.validate("gpt-6.1-sol", "ultra"), ("gpt-6.1-sol", "ultra"))


if __name__ == "__main__":
    unittest.main()
