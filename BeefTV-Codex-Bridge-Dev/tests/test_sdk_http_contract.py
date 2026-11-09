"""Real installed BeefTV SDK -> localhost HTTP -> real bridge -> fake Codex RPC.

No Codex child process or remote model is ever started. All tool executions are
fixture replies in the Node client, so this cannot read or mutate real canvases.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from completion_bridge import CompletionBridge

NODE = Path('D:/BeefTv/agent-host/runtime/node.exe')


class FakeCodexRPC:
    def __init__(self):
        self.events = queue.Queue()
        self.replies = []
        self.started = []
        self.closed = False

    def start_thread(self, **kwargs):
        self.thread_args = kwargs
        return 'mock-thread'

    def start_turn(self, inputs, effort='high'):
        self.started.append(inputs)
        if self.thread_args['tools']:
            for index in range(2):
                self.events.put({'id': 101 + index, 'method': 'item/tool/call', 'params': {
                    'threadId': 'mock-thread', 'turnId': 'mock-turn', 'callId': 'native-' + str(index),
                    'tool': 'canvas_get', 'arguments': {'canvasId': 'canvas-test-' + str(index + 1)}}})
        else:
            self.enqueue_done('本机文本返回。')
        return 'mock-turn'

    def enqueue_done(self, text):
        self.events.put({'method': 'item/agentMessage/delta', 'params': {'delta': text}})
        self.events.put({'method': 'thread/tokenUsage/updated', 'params': {
            'tokenUsage': {'last': {'inputTokens': 17, 'outputTokens': 11, 'totalTokens': 28}}}})
        self.events.put({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})

    def reply_tool(self, rpc_id, content_items, success=True):
        self.replies.append((rpc_id, content_items, success))
        if len(self.replies) == 2:
            self.enqueue_done('两个画布读取完成。')

    def next_event(self, timeout=1):
        return self.events.get(timeout=timeout)

    def cancel(self):
        pass

    def close(self):
        self.closed = True


@unittest.skipUnless(NODE.is_file(), 'Installed Windows BeefTV SDK unavailable')
class SDKHTTPContractTests(unittest.TestCase):
    def test_sdk_streams_text_and_continues_two_queued_tools_over_http(self):
        instances, requests, errors = [], [], []
        def factory():
            value = FakeCodexRPC()
            instances.append(value)
            return value
        bridge = CompletionBridge(factory, models=['gpt-6-astra'], timeout=5,
                                  authorize_submission=lambda: None)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                try:
                    if self.path != '/v1/chat/completions':
                        self.send_error(404); return
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    requests.append(payload)
                    if payload.get('stream'):
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.end_headers()
                        for chunk in bridge.stream(payload):
                            if chunk is not None:
                                self.wfile.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode('utf-8'))
                                self.wfile.flush()
                        self.wfile.write(b'data: [DONE]\n\n')
                        self.wfile.flush()
                    else:
                        data = json.dumps(bridge.complete(payload), ensure_ascii=False).encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                except Exception as error:
                    errors.append(str(error))
                    self.close_connection = True
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = subprocess.run([str(NODE), str(Path(__file__).with_name('sdk_http_client.mjs')),
                                       f'http://127.0.0.1:{server.server_port}/v1'],
                                      capture_output=True, text=True, encoding='utf-8', timeout=30,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.assertEqual(response.returncode, 0, response.stdout + response.stderr + '\n' + repr(errors))
            proof = json.loads(response.stdout)
            self.assertTrue(proof['passed'])
            self.assertEqual(len(requests), 5)
            self.assertEqual(len(instances), 3)
            self.assertEqual([reply[0] for reply in instances[0].replies], [101, 102])
            self.assertEqual(len(instances[0].started), 1, 'Tool continuation must not start a second inference turn')
            self.assertTrue(all(rpc.closed for rpc in instances))
            self.assertEqual(instances[1].started[0][0]['text'], '原样  不修改\n文本')
            self.assertFalse(errors)
            self.assertEqual(requests[1]['messages'][-1]['role'], 'tool')
            self.assertEqual(requests[2]['messages'][-1]['role'], 'tool')
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()
            thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
