(() => {
  const element = (id) => document.getElementById(id);
  const config = JSON.parse(element('demo-config').textContent);
  const root = element('conteudo');
  const buttons = [element('send-success'), element('send-failure')];
  const consult = element('consult-result');
  const message = element('result-message');
  const badge = element('result-status');
  const storageKey = 'webhook-demo-result';
  const validId = (value) => typeof value === 'string'
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
  const errors = {
    invalid_visitor: 'Sua identificação expirou. Reabra a página antes de testar.',
    demo_unavailable: 'A demonstração está indisponível no momento.',
    disabled: 'Os envios estão desabilitados no momento.',
    busy: 'O serviço está ocupado. Tente novamente mais tarde.',
    global_limit: 'O limite compartilhado de envios foi atingido.',
    visitor_minute_limit: 'O limite de envios por minuto deste navegador foi atingido.',
    visitor_day_limit: 'O limite de envios em 24 horas deste navegador foi atingido.',
  };
  const states = {
    pending: ['Pendente', 'O evento ainda não tem resultado final.'],
    sending: ['Em andamento', 'A tentativa começou, mas ainda não há resultado final. Consulte novamente; isso não reenvia o evento.'],
    delivered: ['Entregue', 'O receptor respondeu com sucesso e o resultado foi registrado.'],
    failed: ['Falha registrada', 'A tentativa terminou com falha. Não há reenvio automático.'],
  };
  let eventId = null;
  let uncertain = false;
  let busy = false;
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    eventId = validId(saved?.eventId) ? saved.eventId : null;
    uncertain = saved?.uncertain === true;
  } catch {}

  function remember() {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({ eventId, uncertain }));
    } catch {}
  }

  function updateControls() {
    buttons.forEach((button) => { button.disabled = busy || !config.enabled || uncertain; });
    consult.disabled = busy || !eventId;
    element('result-panel').setAttribute('aria-busy', String(busy));
  }

  function announce(text, label, state = '') {
    message.textContent = text;
    badge.textContent = label;
    badge.dataset.state = state;
  }

  function showResult(data) {
    eventId = data.event_id;
    uncertain = ['pending', 'sending'].includes(data.event_status);
    const [label, description] = states[data.event_status];
    const text = data.error_code === 'timeout'
      ? 'O tempo de espera terminou. Isso não confirma se o receptor recebeu o evento. Não há reenvio automático.'
      : description;
    announce(text, label, data.event_status);
    element('result-event-id').textContent = eventId;
    element('result-http-status').textContent = data.http_status ?? 'Sem resposta HTTP';
    element('result-error').textContent = data.error_code || 'Nenhum';
    element('result-json').textContent = JSON.stringify(data, null, 2);
    element('result-data').hidden = false;
    remember();
  }

  async function request(scenario = null) {
    const sending = scenario !== null;
    if (busy || (sending ? !config.enabled || uncertain : !eventId)) return;
    busy = true;
    element('result-data').hidden = true;
    if (sending) {
      eventId = null;
      uncertain = true;
      remember();
    }
    updateControls();
    announce(sending ? 'Enviando o evento. Aguarde o resultado.' : 'Consultando o registro salvo.', 'Aguarde', 'sending');
    try {
      const url = sending ? root.dataset.sendUrl
        : root.dataset.detailUrl.replace('00000000-0000-0000-0000-000000000000', eventId);
      const options = {
        method: sending ? 'POST' : 'GET', credentials: 'same-origin', mode: 'same-origin',
        redirect: 'error', cache: 'no-store', headers: { Accept: 'application/json' },
      };
      if (sending) {
        options.headers['Content-Type'] = 'application/json';
        options.headers['X-CSRFToken'] = document.querySelector('[name=csrfmiddlewaretoken]').value;
        options.body = JSON.stringify({ scenario });
      }
      const response = await fetch(url, options);
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        if (sending) {
          uncertain = response.status !== 403 && !Object.hasOwn(errors, data?.code);
          if (validId(data?.event_id)) eventId = data.event_id;
          remember();
        }
        let text = Object.hasOwn(errors, data?.code) ? errors[data.code]
          : response.status === 404 && !sending ? 'Resultado não encontrado para este navegador.'
          : response.status === 403 ? 'A solicitação foi recusada. Verifique os cookies e reabra a página.'
          : 'Não foi possível confirmar o resultado. Consulte o registro, se disponível; evite repetir o envio.';
        const wait = response.headers.get('Retry-After');
        if (wait && /^\d+$/.test(wait)) text += ` Aguarde ${wait} segundos antes de tentar novamente.`;
        announce(text, 'Atenção');
        return;
      }
      if (!validId(data?.event_id) || !Object.hasOwn(states, data?.event_status)
        || (!sending && data.event_id !== eventId)) throw new Error('invalid_response');
      showResult(data);
    } catch {
      announce(sending
        ? 'Não recebemos confirmação do envio. O receptor pode ter recebido o evento. Evite repetir o teste.'
        : 'Não foi possível consultar agora. Você pode tentar a consulta novamente; ela não reenvia o evento.', 'Sem confirmação');
    } finally {
      busy = false;
      updateControls();
    }
  }

  buttons.forEach((button) => { button.addEventListener('click', () => request(button.dataset.scenario)); });
  consult.addEventListener('click', () => request());
  if (uncertain) announce('Há uma tentativa sem confirmação nesta aba. Consulte o resultado, se disponível, antes de iniciar outro envio.', 'Sem confirmação');
  else if (eventId) announce('Há um resultado desta aba. Clique em Consultar resultado para buscá-lo.', 'Disponível para consulta');
  updateControls();
})();
