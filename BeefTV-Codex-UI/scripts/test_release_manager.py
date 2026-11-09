"""Exercise the upgrade assessment using actual temporary Git trees."""

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("release_manager.py")


class UpgradeAssessmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-b", "custom")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release-test@example.invalid")
        for name in ("adopt", "retain", "same", "conflict", "obsolete"):
            (self.repo / name).write_text("base\n", encoding="utf-8")
        self.base = self.commit()
        self.git("switch", "-c", "upstream")
        for name in ("adopt", "same", "conflict"):
            (self.repo / name).write_text("official\n", encoding="utf-8")
        (self.repo / "obsolete").unlink()
        self.candidate = self.commit()
        self.git("tag", "v2.1.6")
        self.git("switch", "custom")
        for name in ("retain", "conflict"):
            (self.repo / name).write_text("custom\n", encoding="utf-8")
        (self.repo / "same").write_text("official\n", encoding="utf-8")
        (self.repo / "obsolete").unlink()
        plugin = self.repo / "plugin-packages" / "local" / "manifest.json"
        plugin.parent.mkdir(parents=True)
        plugin.write_text('{"id":"local"}', encoding="utf-8")
        host = self.repo / "agent-host/plugin-host.mjs"
        host.parent.mkdir()
        host.write_text("// changed host contract\n", encoding="utf-8")
        self.custom = self.commit()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def compare(self, pin):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "compare", "--repository", str(self.repo),
             "--selected", self.base, "--candidate", "v2.1.6", "--expect-candidate", pin,
             "--customization", self.custom, "--production", self.base],
            capture_output=True, text=True,
        )

    def test_compares_pinned_trees_and_separates_plugin_review(self):
        result = self.compare(self.candidate)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["revisions"], {
            "upstream": self.candidate, "selectedUpstream": self.base,
            "customization": self.custom, "production": self.base,
        })
        self.assertEqual({row["path"]: row["decision"] for row in report["files"]}, {
            "adopt": "adopt", "retain": "retain", "same": "retire-duplicate",
            "conflict": "conflict", "obsolete": "retire-duplicate",
            "plugin-packages/local/manifest.json": "retain",
            "agent-host/plugin-host.mjs": "retain",
        })
        self.assertEqual(report["pluginReview"], ["agent-host/plugin-host.mjs", "plugin-packages/local/manifest.json"])
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.custom)

    def test_rejects_an_official_ref_that_moved_since_verification(self):
        result = self.compare(self.base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("candidate does not match the pinned commit", result.stderr)


class DistributionVerificationTests(unittest.TestCase):
    def test_build_receipt_rejects_a_stale_commit_or_changed_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            build.mkdir()
            binary = build / "BeefTV.exe"
            binary.write_bytes(b"fresh build")
            sys.path.insert(0, str(SCRIPT.parent))
            from release_manager import record_build, verify_build

            receipt = record_build(build, "pinned", "v2.1.1")
            verify_build(build, receipt, "pinned", "v2.1.1")
            with self.assertRaisesRegex(ValueError, "source revision"):
                verify_build(build, receipt, "different commit", "v2.1.1")
            binary.write_bytes(b"stale build")
            with self.assertRaisesRegex(ValueError, "Build payload"):
                verify_build(build, receipt, "pinned", "v2.1.1")

    def test_pinned_manifest_and_exact_payload_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app").mkdir()
            file = root / "app/BeefTV.exe"
            file.write_bytes(b"release")
            manifest = root / "release.json"
            manifest.write_text(json.dumps({"schemaVersion": 1, "files": [
                {"area": "app", "path": "BeefTV.exe", "sha256": hashlib.sha256(b"release").hexdigest()},
            ]}), encoding="utf-8")
            pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
            command = [sys.executable, "-B", str(SCRIPT), "verify", "--package", str(root),
                       "--manifest-sha256", pin]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            unexpected = root / "app/unlisted.exe"
            unexpected.write_bytes(b"unlisted")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unexpected release member", result.stderr)
            unexpected.unlink()
            file.write_bytes(b"tampered")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("hash mismatch", result.stderr)
            manifest.write_text("{}", encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("manifest hash mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
