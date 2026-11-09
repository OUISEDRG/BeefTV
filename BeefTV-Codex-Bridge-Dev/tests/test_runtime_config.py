import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import server


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.install = self.root / "installed"
        self.home = self.root / "home"
        self.home.mkdir()

    def executable(self, version, modified):
        path = self.install / version / ("codex.exe" if os.name == "nt" else "codex")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
        os.utime(path, (modified, modified))
        return path

    def probe(self, **config):
        (self.root / "runtime-config.json").write_text(json.dumps({
            "codexHome": str(self.home), **config,
        }), encoding="utf-8")
        with (patch.object(sys, "argv", ["server.py", "--root", str(self.root), "--probe"]),
              patch.dict(os.environ, {"CODEX_APP_TOOLS_PIPE_PATH": "fixture-pipe"}),
              contextlib.redirect_stdout(io.StringIO()),
              patch("model_catalog.probe_native_catalog") as catalog):
            catalog.side_effect = lambda *args, **kwargs: self.capture_probe(*args)
            server.main()
            return catalog.call_args.args[0]

    def capture_probe(self, executable, work):
        self.assertEqual(Path(work), self.root / "runtime")
        self.assertEqual(os.environ["CODEX_HOME"], str(self.home))
        self.assertEqual(os.environ["TEMP"], str(self.home / ".tmp"))
        self.assertEqual(os.environ["TMP"], str(self.home / ".tmp"))
        return {"account": {"signedIn": False}, "models": []}

    def test_stale_pin_discovers_valid_install_and_uses_new_version_on_restart(self):
        old = self.executable("first", 100)
        unusable = self.executable("broken", 200)
        missing = self.install / "missing" / old.name
        def probe_version(argv, **kwargs):
            self.assertEqual(argv[1:], ["--version"])
            self.assertEqual(kwargs["env"]["CODEX_HOME"], str(self.home))
            self.assertNotIn("CODEX_APP_TOOLS_PIPE_PATH", kwargs["env"])
            output = "wrong CLI" if Path(argv[0]) == unusable else "codex-cli 0.167.0-alpha.3.2\n"
            return subprocess.CompletedProcess(argv, 0, stdout=output)
        with patch("subprocess.run", side_effect=probe_version) as version:
            self.assertEqual(Path(self.probe(codexExecutable=str(missing), codexInstallRoot=str(self.install))), old)
            newest = self.executable("newest", 300)
            self.assertEqual(Path(self.probe(codexInstallRoot=str(self.install))), newest)
        self.assertEqual(version.call_count, 3)

    def test_existing_explicit_pin_remains_authoritative(self):
        pinned = self.executable("pinned", 100)
        self.executable("newer", 200)
        with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="codex-cli 0.166.0\n")) as version:
            self.assertEqual(Path(self.probe(codexExecutable=str(pinned), codexInstallRoot=str(self.install))), pinned)
        self.assertEqual(version.call_args.args[0], [str(pinned), "--version"])

    def test_bad_executable_fails_before_the_rpc_probe(self):
        bad = self.executable("bad", 100)
        with (patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="unrelated 2.0.0\n")),
              self.assertRaisesRegex(RuntimeError, "Codex.*version")):
            self.probe(codexExecutable=str(bad))

    def test_relative_install_root_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "codexInstallRoot.*absolute"):
            self.probe(codexInstallRoot="relative")
