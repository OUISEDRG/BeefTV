'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; }
}

test('local diagnosis reads the budget flag and displays an unknown receipt without any writes', async () => {
    const root = Object.fromEntries(['signin', 'key', 'jobs', 'login', 'message', 'execution-policy', 'refresh'].map(id => [id, new Element('div')]));
    const calls = [];
    const values = new Map();
    const sessionStorage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
    const context = {
        location: { hash: '#fixture-token', pathname: '/console' },
        history: { replaceState() {} },
        sessionStorage,
        document: { getElementById: id => root[id], createElement: tag => new Element(tag) },
        setInterval() { throw new Error('Diagnostic page must not run a timer'); },
        async fetch(url, options) {
            calls.push({ url, options });
            if (url === '/health') return { ok: true, json: async () => ({ generationEnabled: false }) };
            if (url === '/integration/v1/tasks?scope=exceptions') {
                return { ok: true, json: async () => ({ items: [
                    { id: 'unknown-task', kind: 'extension', status: 'submission_unknown', pointsEstimate: 67, cancelable: true, autoExecuteAt: 99 },
                ] }) };
            }
            throw new Error('Unexpected path: ' + url);
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../console.js'), 'utf8'), context);
    await new Promise(setImmediate);
    const flatten = el => [el, ...el.children.flatMap(flatten)];
    assert.match(root['execution-policy'].textContent, /预算/);
    assert.ok(flatten(root.jobs).some(item => item.textContent.includes('unknown-task')));
    assert.ok(flatten(root.jobs).some(item => item.textContent.includes('回执待核对')));
    assert.ok(flatten(root.jobs).every(item => item.tag !== 'button'));
    assert.deepEqual(calls.map(({ url }) => url), ['/health', '/integration/v1/tasks?scope=exceptions']);
    await root.refresh.onclick();
    assert.ok(calls.every(({ options }) => options.method === 'GET' && options.body === undefined));
});
