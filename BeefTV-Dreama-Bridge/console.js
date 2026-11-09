'use strict';
const $ = id => document.getElementById(id);
let token = location.hash.slice(1) || sessionStorage.getItem('dreama-local-key') || '';
history.replaceState(null, '', location.pathname);
if (token) sessionStorage.setItem('dreama-local-key', token);
const statuses = {
    unknown: '预检待核对',
    recovery_required: '待核对',
    submission_unknown: '回执待核对',
    download_failed: '结果待取回',
    binding_verification_required: '项目归属待核验',
};

async function read(path) {
    const response = await fetch(path, { method: 'GET', headers: { Authorization: 'Bearer ' + token } });
    const body = await response.json();
    if (!response.ok) throw Error(body.error?.message || '读取失败');
    return body;
}

function text(parent, tag, value) {
    const element = document.createElement(tag);
    element.textContent = value;
    parent.append(element);
}

async function refresh() {
    if (!token || $('refresh').disabled) return;
    $('refresh').disabled = true;
    try {
        const health = await read('/health');
        const { items } = await read('/integration/v1/tasks?scope=exceptions');
        $('execution-policy').textContent = health.generationEnabled
            ? '新任务在预检与预算通过后提交。'
            : '预算尚未配置。已有任务可在 BeefTV 任务中心查询与取回。';
        $('login').hidden = true;
        $('jobs').replaceChildren();
        $('message').textContent = '';
        if (!items.length) text($('jobs'), 'p', '暂无需要核对的任务。');
        for (const task of items) {
            const card = document.createElement('section');
            card.className = 'job';
            $('jobs').append(card);
            const label = task.stage === 'budget_blocked' ? '预算待配置或不足' : statuses[task.status] || '预检待核对';
            text(card, 'h2', label);
            text(card, 'p', '任务 ' + task.id);
            if (task.pointsEstimate != null) text(card, 'p', '预计 ' + task.pointsEstimate + ' 积分');
            if (task.error) text(card, 'p', task.error);
        }
    } catch (error) {
        $('message').textContent = error.message;
    } finally {
        $('refresh').disabled = false;
    }
}

$('signin').onclick = () => {
    token = $('key').value.trim();
    sessionStorage.setItem('dreama-local-key', token);
    return refresh();
};
$('refresh').onclick = refresh;
refresh();
