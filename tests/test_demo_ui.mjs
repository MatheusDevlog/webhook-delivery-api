import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../webhooks/static/webhooks/demo.js', import.meta.url), 'utf8');
const eventId = '11111111-1111-4111-8111-111111111111';
const result = (state = 'delivered', status = 200, error = '') => ({
  id: '22222222-2222-4222-8222-222222222222', event_id: eventId,
  event_status: state, started_at: '2026-10-30T12:00:00Z',
  finished_at: '2026-10-30T12:00:01Z', http_status: status, error_code: error,
});
const reply = (status, data, headers = {}) => ({
  ok: status >= 200 && status < 300, status, headers: { get: (name) => headers[name] ?? null },
  json: async () => { if (data instanceof Error) throw data; return data; },
});

function page({ enabled = true, responses = [], saved = null, storageBlocked = false, clock = () => '10:42:08' } = {}) {
  const nodes = {};
  for (const id of ['demo-config', 'conteudo', 'send-success', 'send-failure', 'consult-result',
    'result-message', 'result-status', 'result-panel', 'result-event-id', 'result-http-status',
    'result-error', 'result-json', 'result-data', 'consult-confirmation',
    'consult-confirmation-title', 'consult-confirmation-message']) {
    nodes[id] = {
      textContent: '', dataset: {}, attributes: {}, disabled: true, hidden: true,
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, handler) { this[name] = handler; },
    };
    Object.defineProperty(nodes[id], 'innerHTML', { set() { throw new Error('unsafe_html'); } });
  }
  nodes['demo-config'].textContent = JSON.stringify({ enabled });
  nodes.conteudo.dataset = {
    sendUrl: '/demonstracao/enviar/',
    detailUrl: '/demonstracao/eventos/00000000-0000-0000-0000-000000000000/',
  };
  nodes['send-success'].dataset.scenario = 'success';
  nodes['send-failure'].dataset.scenario = 'failure';
  const classes = new Set();
  nodes['result-panel'].classList = {
    add: (name) => classes.add(name), remove: (name) => classes.delete(name),
    contains: (name) => classes.has(name),
  };
  let storage = JSON.stringify(saved);
  const calls = [];
  runInNewContext(source, {
    Date: class extends Date { toLocaleTimeString() { return clock(); } },
    document: {
      getElementById: (id) => nodes[id],
      querySelector: (selector) => {
        assert.equal(selector, '[name=csrfmiddlewaretoken]');
        return { value: 'TEST_CSRF_TOKEN' };
      },
    },
    sessionStorage: {
      getItem: () => { if (storageBlocked) throw new Error('blocked'); return storage; },
      setItem: (key, value) => { if (storageBlocked) throw new Error('blocked'); storage = value; },
    },
    fetch: async (url, options) => {
      calls.push({ url, ...JSON.parse(JSON.stringify(options)) });
      const response = responses.shift();
      if (response instanceof Error) throw response;
      return typeof response === 'function' ? response() : response;
    },
  });
  return { nodes, calls, stored: () => JSON.parse(storage), click: (id) => nodes[id].click() };
}

test('abrir a página habilita controles sem enviar ou consultar automaticamente', () => {
  const ui = page();
  assert.equal(ui.nodes['send-success'].disabled, false);
  assert.equal(ui.nodes['consult-result'].disabled, true);
  assert.equal(ui.calls.length, 0);
});

test('demonstração desabilitada não inicia requisição mesmo com acionamento do botão', async () => {
  const ui = page({ enabled: false });
  await ui.click('send-success');
  assert.equal(ui.nodes['send-success'].disabled, true);
  assert.equal(ui.calls.length, 0);
});

test('sucesso envia somente cenário com CSRF e cookies da mesma origem', async () => {
  const ui = page({ responses: [reply(200, result())] });
  await ui.click('send-success');
  const sent = ui.calls[0];
  assert.equal(sent.url, '/demonstracao/enviar/');
  assert.equal(sent.method, 'POST');
  assert.deepEqual(JSON.parse(sent.body), { scenario: 'success' });
  assert.equal(sent.headers['X-CSRFToken'], 'TEST_CSRF_TOKEN');
  assert.equal(sent.headers['Content-Type'], 'application/json');
  assert.equal(sent.credentials, 'same-origin');
  assert.equal(sent.mode, 'same-origin');
  assert.equal(sent.redirect, 'error');
  assert.equal(sent.cache, 'no-store');
  assert.equal(ui.nodes['result-status'].textContent, 'Entregue');
  assert.equal(ui.nodes['result-data'].hidden, false);
  assert.equal(ui.nodes['consult-result'].disabled, false);
  assert.equal(ui.nodes['consult-confirmation'].hidden, true);
  assert.deepEqual(ui.stored(), { eventId, uncertain: false });
  assert.equal(ui.calls.length, 1);
});

test('HTTP 500 do receptor aparece como falha registrada sem repetir envio', async () => {
  const ui = page({ responses: [reply(200, result('failed', 500, 'http_error'))] });
  await ui.click('send-failure');
  assert.deepEqual(JSON.parse(ui.calls[0].body), { scenario: 'failure' });
  assert.equal(ui.nodes['result-status'].textContent, 'Falha registrada');
  assert.equal(ui.nodes['result-http-status'].textContent, 500);
  assert.equal(ui.nodes['result-error'].textContent, 'http_error');
  assert.equal(ui.calls.length, 1);
});

test('timeout explica a incerteza de recebimento e não reenvia', async () => {
  const ui = page({ responses: [reply(200, result('failed', null, 'timeout'))] });
  await ui.click('send-success');
  assert.match(ui.nodes['result-message'].textContent, /não confirma se o receptor recebeu/);
  assert.equal(ui.nodes['result-http-status'].textContent, 'Sem resposta HTTP');
  assert.equal(ui.calls.length, 1);
});

test('cliques concorrentes são bloqueados enquanto uma requisição aguarda', async () => {
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const ui = page({ responses: [() => pending] });
  const first = ui.click('send-success');
  await ui.click('send-failure');
  assert.equal(ui.nodes['send-success'].disabled, true);
  assert.equal(ui.nodes['result-panel'].attributes['aria-busy'], 'true');
  assert.equal(ui.calls.length, 1);
  finish(reply(200, result()));
  await first;
  assert.equal(ui.nodes['result-panel'].attributes['aria-busy'], 'false');
  assert.equal(ui.nodes['send-success'].disabled, false);
});

for (const enabled of [true, false]) {
  test(`resultado lembrado é consultado só por GET mesmo com envio habilitado=${enabled}`, async () => {
    const ui = page({ enabled, saved: { eventId, uncertain: false }, responses: [reply(200, result())] });
    assert.equal(ui.calls.length, 0);
    await ui.click('consult-result');
    assert.equal(ui.calls[0].url, `/demonstracao/eventos/${eventId}/`);
    assert.equal(ui.calls[0].method, 'GET');
    assert.equal(ui.calls[0].body, undefined);
    assert.equal(ui.calls.length, 1);
  });
}

test('erro interno permite consultar o ID informado sem iniciar novo envio', async () => {
  const ui = page({ responses: [reply(500, { code: 'delivery_error', event_id: eventId }), reply(200, result())] });
  await ui.click('send-success');
  assert.equal(ui.nodes['send-success'].disabled, true);
  assert.equal(ui.nodes['consult-result'].disabled, false);
  await ui.click('consult-result');
  assert.deepEqual(ui.calls.map((call) => call.method), ['POST', 'GET']);
  assert.equal(ui.nodes['send-success'].disabled, false);
});

test('perda da resposta de envio preserva aviso e bloqueio após recarregar a aba', async () => {
  const ui = page({ responses: [new Error('PRIVATE_NETWORK_DETAILS')] });
  await ui.click('send-success');
  await ui.click('send-success');
  assert.match(ui.nodes['result-message'].textContent, /O receptor pode ter recebido/);
  assert.doesNotMatch(ui.nodes['result-message'].textContent, /PRIVATE/);
  assert.equal(ui.calls.length, 1);
  const restored = page({ saved: ui.stored() });
  assert.equal(restored.nodes['send-success'].disabled, true);
  assert.equal(restored.calls.length, 0);
});

for (const code of ['visitor_minute_limit', 'visitor_day_limit', 'global_limit']) {
  test(`${code} mostra a espera sem tentar novamente sozinho`, async () => {
    const ui = page({ responses: [reply(429, { code }, { 'Retry-After': '60' })] });
    await ui.click('send-success');
    assert.match(ui.nodes['result-message'].textContent, /Aguarde 60 segundos/);
    assert.equal(ui.nodes['send-success'].disabled, false);
    assert.equal(ui.calls.length, 1);
  });
}

test('resposta HTML de recusa CSRF não causa erro nem exibe conteúdo bruto', async () => {
  const ui = page({ responses: [reply(403, new Error('PRIVATE_HTML_BODY'))] });
  await ui.click('send-success');
  assert.match(ui.nodes['result-message'].textContent, /solicitação foi recusada/);
  assert.equal(ui.nodes['result-data'].hidden, true);
  assert.equal(ui.nodes['result-panel'].attributes['aria-busy'], 'false');
  assert.equal(ui.stored().uncertain, false);
  assert.equal(ui.nodes['send-success'].disabled, false);
  assert.equal(ui.calls.length, 1);
});

test('consulta indisponível pode ser repetida sem fazer POST', async () => {
  const ui = page({ saved: { eventId }, responses: [new Error('network'), reply(200, result())] });
  await ui.click('consult-result');
  assert.equal(ui.nodes['consult-result'].disabled, false);
  await ui.click('consult-result');
  assert.deepEqual(ui.calls.map((call) => call.method), ['GET', 'GET']);
});

test('resultado ausente não vira outro envio', async () => {
  const ui = page({ saved: { eventId }, responses: [reply(404, { code: 'not_found' })] });
  await ui.click('consult-result');
  assert.match(ui.nodes['result-message'].textContent, /não encontrado para este navegador/);
  assert.equal(ui.calls[0].method, 'GET');
});

test('valores da resposta são exibidos como texto, sem interpretar HTML', async () => {
  const data = result('failed', 500, '<img src=x onerror=alert(1)>');
  const ui = page({ responses: [reply(200, data)] });
  await ui.click('send-success');
  assert.equal(ui.nodes['result-error'].textContent, data.error_code);
  assert.deepEqual(JSON.parse(ui.nodes['result-json'].textContent), data);
});

test('armazenamento da aba bloqueado não impede o uso da página atual', async () => {
  const ui = page({ storageBlocked: true, responses: [reply(200, result())] });
  await ui.click('send-success');
  assert.equal(ui.nodes['result-status'].textContent, 'Entregue');
});

test('ID inválido no armazenamento não vira URL de consulta', async () => {
  const ui = page({ saved: { eventId: 'https://untrusted.example/' } });
  await ui.click('consult-result');
  assert.equal(ui.nodes['consult-result'].disabled, true);
  assert.equal(ui.calls.length, 0);
});

for (const data of [null, { event_id: eventId, event_status: 'unknown' }]) {
  test(`resposta de envio inválida ${JSON.stringify(data)} mantém resultado incerto`, async () => {
    const ui = page({ responses: [reply(200, data)] });
    await ui.click('send-success');
    assert.equal(ui.nodes['send-success'].disabled, true);
    assert.equal(ui.nodes['result-status'].textContent, 'Sem confirmação');
  });
}

test('sending mantém consulta disponível e bloqueia outro envio após recarregar', async () => {
  const data = { ...result('sending', null), finished_at: null };
  const ui = page({ saved: { eventId }, responses: [reply(200, data)] });
  await ui.click('consult-result');
  assert.equal(ui.nodes['result-status'].textContent, 'Em andamento');
  const restored = page({ saved: ui.stored() });
  assert.equal(restored.nodes['send-success'].disabled, true);
  assert.equal(restored.nodes['consult-result'].disabled, false);
});

test('consulta recusa resposta com ID diferente do resultado solicitado', async () => {
  const data = { ...result(), event_id: '33333333-3333-4333-8333-333333333333' };
  const ui = page({ saved: { eventId }, responses: [reply(200, data)] });
  await ui.click('consult-result');
  assert.equal(ui.nodes['result-data'].hidden, true);
  assert.equal(ui.stored().eventId, eventId);
  assert.equal(ui.nodes['result-status'].textContent, 'Sem confirmação');
});

test('indisponibilidade conhecida mostra mensagem segura sem detalhe interno ou repetição', async () => {
  const ui = page({ responses: [reply(503, { code: 'demo_unavailable', detail: 'PRIVATE_CONFIG' })] });
  await ui.click('send-success');
  assert.match(ui.nodes['result-message'].textContent, /indisponível/);
  assert.doesNotMatch(ui.nodes['result-message'].textContent, /PRIVATE_CONFIG/);
  assert.equal(ui.stored().uncertain, false);
  assert.equal(ui.calls.length, 1);
});

for (const [state, status, error, label] of [
  ['delivered', 200, '', 'Entregue'],
  ['failed', 500, 'http_error', 'Falha registrada'],
  ['sending', null, '', 'Em andamento'],
]) {
  test(`consulta de ${state} confirma registro e horário sem mudar dados da entrega`, async () => {
    const data = { ...result(state, status, error), finished_at: state === 'sending' ? null : result().finished_at };
    const ui = page({ saved: { eventId }, responses: [reply(200, data)] });
    await ui.click('consult-result');

    assert.equal(ui.nodes['consult-confirmation'].hidden, false);
    assert.equal(ui.nodes['consult-confirmation-title'].textContent, 'Registro consultado às 10:42:08');
    assert.equal(ui.nodes['consult-confirmation-message'].textContent, `Resultado salvo: ${label}. Nenhum novo envio foi realizado.`);
    assert.equal(ui.nodes['result-panel'].classList.contains('consulted'), true);
    assert.equal(ui.nodes['consult-result'].disabled, false);
    assert.deepEqual(JSON.parse(ui.nodes['result-json'].textContent), data);
    assert.deepEqual(ui.calls.map((call) => call.method), ['GET']);
  });
}

test('nova consulta atualiza o horário e reinicia destaque mesmo sem mudança de resultado', async () => {
  let time = '10:42:08';
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const ui = page({
    saved: { eventId }, clock: () => time,
    responses: [reply(200, result()), () => pending],
  });
  await ui.click('consult-result');
  const second = ui.click('consult-result');
  assert.equal(ui.nodes['consult-confirmation'].hidden, true);
  assert.equal(ui.nodes['result-panel'].classList.contains('consulted'), false);
  time = '10:42:09';
  finish(reply(200, result()));
  await second;

  assert.equal(ui.nodes['consult-confirmation'].hidden, false);
  assert.equal(ui.nodes['consult-confirmation-title'].textContent, 'Registro consultado às 10:42:09');
  assert.equal(ui.nodes['result-panel'].classList.contains('consulted'), true);
  assert.deepEqual(ui.calls.map((call) => call.method), ['GET', 'GET']);
});

for (const [reason, failure] of [['404', reply(404, { code: 'not_found' })], ['rede', new Error('network')]]) {
  test(`consulta com falha de ${reason} oculta confirmação anterior e não mostra outro horário de sucesso`, async () => {
    const ui = page({ saved: { eventId }, responses: [reply(200, result()), failure] });
    await ui.click('consult-result');
    await ui.click('consult-result');

    assert.equal(ui.nodes['consult-confirmation'].hidden, true);
    assert.equal(ui.nodes['result-panel'].classList.contains('consulted'), false);
    assert.deepEqual(ui.calls.map((call) => call.method), ['GET', 'GET']);
  });
}

test('iniciar outro teste remove confirmação da consulta anterior', async () => {
  const next = { ...result(), event_id: '33333333-3333-4333-8333-333333333333' };
  const ui = page({ saved: { eventId }, responses: [reply(200, result()), reply(200, next)] });
  await ui.click('consult-result');
  await ui.click('send-success');

  assert.equal(ui.nodes['consult-confirmation'].hidden, true);
  assert.equal(ui.nodes['result-panel'].classList.contains('consulted'), false);
  assert.equal(ui.nodes['result-status'].textContent, 'Entregue');
  assert.deepEqual(ui.calls.map((call) => call.method), ['GET', 'POST']);
});
