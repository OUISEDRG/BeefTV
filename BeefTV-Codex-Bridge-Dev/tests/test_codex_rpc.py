import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_rpc import RpcSession, RpcError, isolated_catalog, normalize_tools, clean_environment


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
    def emit(self, obj):
        self.stdout.lines.put(json.dumps(obj) + "\n")
    def receive(self, msg):
        self.messages.append(msg)
        method = msg.get("method")
        answers = {
            "initialize": {"userAgent": "fake"},
            "account/read": {"account": {"type": "chatgpt", "planType": "pro", "email": "must-not-leak@example.com"}, "requiresOpenaiAuth": True},
            "model/list": {"data": [{"id": "fake-model", "isDefault": True}], "nextCursor": None},
            "thread/start": {"thread": {"id": "thread-fake"}},
            "turn/start": {"turn": {"id": "turn-fake"}},
            "turn/interrupt": {},
        }
        if method in answers:
            self.emit({"id": msg["id"], "result": answers[method]})
    def wait(self, timeout=None):
        return 0
    def terminate(self):
        self.stdout.lines.put(None)
    def kill(self):
        self.terminate()


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.process = FakeProcess()
        self.session = RpcSession("fake-codex", self.temp.name, transport=self.process, request_timeout=1)
    def tearDown(self):
        self.session.close()
        self.temp.cleanup()
    def test_constructor_and_probe_never_start_inference(self):
        proof = self.session.probe()
        self.assertTrue(proof["account"]["signedIn"])
        self.assertNotIn("email", proof["account"])
        methods = [m.get("method") for m in self.process.messages]
        self.assertEqual(methods, ["initialize", "initialized", "account/read", "model/list"])
    def test_dynamic_tool_request_and_matching_result(self):
        tool = {"type": "function", "function": {"name": "create_text_node", "description": "create", "parameters": {"type": "object"}}}
        self.assertEqual(self.session.start_thread("fake-model", [tool], "base", "developer"), "thread-fake")
        self.assertEqual(self.session.start_turn([{"type": "text", "text": "make a node"}]), "turn-fake")
        self.process.emit({"id": 72, "method": "item/tool/call", "params": {
            "threadId": "thread-fake", "turnId": "turn-fake", "callId": "call-unique", "tool": "create_text_node", "arguments": {"text": "hello"}}})
        event = self.session.next_event(1)
        self.assertEqual(event["params"]["callId"], "call-unique")
        self.session.reply_tool(event["id"], [{"type": "inputText", "text": "node-created"}], True)
        self.assertEqual(self.process.messages[-1]["id"], 72)
        self.assertTrue(self.process.messages[-1]["result"]["success"])
        with self.assertRaises(RpcError):
            self.session.reply_tool(72, [], True)
        self.process.emit({"method": "item/agentMessage/delta", "params": {"delta": "done"}})
        self.assertEqual(self.session.next_event(1)["params"]["delta"], "done")
        self.session.cancel()
        self.assertEqual(self.process.messages[-1]["method"], "turn/interrupt")
    def test_unknown_tool_is_not_forwarded(self):
        self.session.start_thread("fake-model", [])
        self.process.emit({"id": 99, "method": "item/tool/call", "params": {"tool": "exec_command", "arguments": {"cmd": "unsafe"}}})
        self.process.emit({"method": "test/sentinel", "params": {}})
        self.assertEqual(self.session.next_event(1)["method"], "test/sentinel")
        self.assertFalse(next(m for m in self.process.messages if m.get("id") == 99)["result"]["success"])
    def test_approval_requests_fail_closed(self):
        self.process.emit({"id": 1, "method": "item/commandExecution/requestApproval", "params": {}})
        self.process.emit({"method": "test/sentinel", "params": {}})
        self.assertEqual(self.session.next_event(1)["method"], "test/sentinel")
        self.assertEqual(next(m for m in self.process.messages if m.get("id") == 1)["error"]["code"], -32601)
    def test_timeout_returns_queue_empty(self):
        with self.assertRaises(queue.Empty):
            self.session.next_event(.01)
    def test_thread_policy(self):
        self.session.start_thread("fake-model", [])
        params = self.process.messages[-1]["params"]
        self.assertEqual(params["environments"], [])
        self.assertEqual(params["sandbox"], "read-only")
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertTrue(params["ephemeral"])
        self.assertFalse(params["allowProviderModelFallback"])
    def test_environment_strips_current_chat_extensions(self):
        result = clean_environment({"PATH": "p", "CODEX_HOME": "h", "CODEX_APP_TOOLS_PIPE_PATH": "secret-pipe", "CODEX_THREAD_ID": "current"})
        self.assertEqual(result, {"PATH": "p", "CODEX_HOME": "h"})
    def test_new_image_sessions_read_current_native_account_storage(self):
        homes = [Path(self.temp.name) / name for name in ('first-home', 'second-home')]
        for home in homes:
            home.mkdir()
        processes = []
        environments = []
        class AccountProcess(FakeProcess):
            def receive(self, message):
                if message.get('method') == 'account/read':
                    self.messages.append(message)
                    self.emit({'id': message['id'], 'result': {
                        'account': {'type': 'chatgpt', 'planType': self.plan},
                        'requiresOpenaiAuth': True}})
                else:
                    super().receive(message)
        def launch(*args, **kwargs):
            environments.append(dict(kwargs['env']))
            process = AccountProcess()
            process.plan = 'pro' if kwargs['env']['CODEX_HOME'] == str(homes[0]) else 'plus'
            processes.append(process)
            return process
        with (patch('codex_rpc.prepare_catalog', return_value='fixture-catalog'),
              patch('codex_rpc.subprocess.Popen', side_effect=launch)):
            plans = []
            for home in homes:
                with patch.dict(os.environ, {'CODEX_HOME': str(home), 'CODEX_THREAD_ID': 'not-inherited'}):
                    session = RpcSession('fake-codex', self.temp.name, image_generation=True)
                    try:
                        plans.append(session.probe()['account']['planType'])
                    finally:
                        session.close()
        self.assertEqual(plans, ['pro', 'plus'])
        self.assertEqual([environment['CODEX_HOME'] for environment in environments], [str(home) for home in homes])
        self.assertTrue(all('CODEX_THREAD_ID' not in environment for environment in environments))
        self.assertEqual(len(processes), 2)
        self.assertTrue(all([message.get('method') for message in process.messages].count('account/read') == 1
                            for process in processes))
        self.assertTrue(all('turn/start' not in [message.get('method') for message in process.messages]
                            for process in processes))
    def test_catalog_keeps_model_identity_and_disables_native_tools(self):
        original = {"models": [{"slug": "m", "instructions": "keep", "tool_mode": "code_mode_only", "shell_type": "unified_exec", "apply_patch_tool_type": "freeform", "experimental_supported_tools": ["clock"], "multi_agent_version": "v2"}]}
        result = isolated_catalog(original)
        self.assertEqual(result["models"][0]["slug"], "m")
        self.assertEqual(result["models"][0]["instructions"], "keep")
        self.assertEqual(result["models"][0]["tool_mode"], "direct")
        self.assertEqual(result["models"][0]["shell_type"], "disabled")
        self.assertIsNone(result["models"][0]["apply_patch_tool_type"])
        self.assertEqual(result["models"][0]["experimental_supported_tools"], [])
        self.assertEqual(original["models"][0]["tool_mode"], "code_mode_only")
    def test_invalid_tool_schema_rejected(self):
        with self.assertRaises(RpcError):
            normalize_tools([{"name": "bad", "parameters": "not-schema"}])


if __name__ == "__main__":
    unittest.main()
