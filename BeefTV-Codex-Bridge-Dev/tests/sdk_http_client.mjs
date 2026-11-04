// Invoked by test_sdk_http_contract.py against an isolated localhost mock only.
import assert from 'node:assert/strict';
import { stream } from 'file:///D:/BeefTv/agent-host/node_modules/@earendil-works/pi-ai/dist/api/openai-completions.js';
import { normalizeContext } from 'file:///D:/BeefTv/agent-host/node_modules/@earendil-works/pi-ai/dist/utils/transcript.js';
const baseUrl = process.argv[2];
assert.match(baseUrl, /^http:\/\/127\.0\.0\.1:\d+\/v1$/);
const model = { id: 'gpt-6-astra', name: 'Local Codex', provider: 'beeftv', api: 'openai-completions',
  baseUrl, reasoning: false, input: ['text', 'image'], contextWindow: 200000, maxTokens: 16384,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const options = { apiKey: 'offline-mock-token', maxRetries: 0 };
const tools = [{ name: 'canvas_get', description: 'Read the current canvas',
  parameters: { type: 'object', properties: { canvasId: { type: 'string' } }, required: ['canvasId'] } }];
const context = normalizeContext({ systemPrompt: 'Use only declared tools.', tools,
  messages: [{ role: 'user', content: 'Read two canvases.', timestamp: 1 }] });
const callIds = [];
for (let index = 0; index < 2; index++) {
  const answer = await stream(model, context, options).result();
  assert.equal(answer.stopReason, 'toolUse', answer.errorMessage);
  const calls = answer.content.filter(c => c.type === 'toolCall');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].name, 'canvas_get');
  assert.deepEqual(calls[0].arguments, { canvasId: 'canvas-test-' + (index + 1) });
  callIds.push(calls[0].id);
  context.messages.push(answer, { role: 'toolResult', toolCallId: calls[0].id,
    toolName: calls[0].name, content: [{ type: 'text', text: JSON.stringify({ canvasId: 'canvas-test-' + (index + 1), revision: 1, nodes: [] }) }],
    isError: false, timestamp: index + 2 });
}
assert.notEqual(callIds[0], callIds[1]);
const final = await stream(model, context, options).result();
assert.equal(final.stopReason, 'stop', final.errorMessage);
assert.equal(final.content.filter(c => c.type === 'text').map(c => c.text).join(''), '两个画布读取完成。');
const plain = normalizeContext({ messages: [{ role: 'user', content: '原样  不修改\n文本', timestamp: 1 }] });
const text = await stream(model, plain, options).result();
assert.equal(text.stopReason, 'stop', text.errorMessage);
assert.equal(text.content[0].text, '本机文本返回。');
const response = await fetch(baseUrl + '/chat/completions', { method: 'POST',
  headers: { 'Content-Type': 'application/json', Authorization: 'Bearer offline-mock-token' },
  body: JSON.stringify({ model: model.id, messages: [{ role: 'user', content: '非流式文本' }], stream: false }) });
assert.equal(response.status, 200);
const json = await response.json();
assert.equal(json.choices[0].message.content, '本机文本返回。');
assert.equal(json.choices[0].finish_reason, 'stop');
console.log(JSON.stringify({ passed: true, httpRequests: 5, sdk: '@earendil-works/pi-ai@1.3.0',
  scenarios: ['two queued tools with two native continuations', 'streamed text', 'nonstream text'],
  remoteInferenceRequests: 0, canvasWrites: 0 }));
