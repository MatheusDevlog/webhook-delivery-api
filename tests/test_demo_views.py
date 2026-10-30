import json
from html.parser import HTMLParser
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from django.test import Client
from django.urls import reverse

from webhooks.demo_access import VISITOR_COOKIE_NAME, get_visitor_id
from webhooks.demo_views import MAX_DEMO_REQUEST_BYTES
from webhooks.delivery import DeliveryResult
from webhooks.limits import DeliveryLimitError
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


pytestmark = pytest.mark.django_db(transaction=True)


class DemoPage(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.config = ''
        self.in_config = False
        self.example = ''
        self.in_example = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'script' and attrs.get('id') == 'demo-config':
            self.in_config = True
        if tag == 'pre' and attrs.get('aria-label') == 'Dados fictícios do evento':
            self.in_example = True

    def handle_endtag(self, tag):
        if tag == 'script':
            self.in_config = False
        if tag == 'pre':
            self.in_example = False

    def handle_data(self, data):
        if self.in_config:
            self.config += data
        if self.in_example:
            self.example += data


@pytest.fixture(autouse=True)
def demo(settings):
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    settings.WEBHOOK_DEMO_ENABLED = True
    settings.WEBHOOK_DEMO_RECEIVER_ENABLED = True
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 20
    settings.WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY = 4
    endpoints = [
        WebhookEndpoint.objects.create(
            url=f'https://receiver.example/demonstracao/receptor/{path}/', event_type='pedido.pago',
        )
        for path in ['sucesso', 'falha']
    ]
    settings.WEBHOOK_ALLOWED_URLS = [endpoint.url for endpoint in endpoints]
    settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID = str(endpoints[0].pk)
    settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID = str(endpoints[1].pk)


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    sender = Mock(return_value=DeliveryResult(200, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    return sender


@pytest.fixture
def browser():
    browser = Client(enforce_csrf_checks=True)
    browser.get(reverse('demo-home'), secure=True)
    return browser


def post(browser, data, content_type='application/json', **headers):
    return browser.post(
        reverse('demo-send'), data, content_type=content_type, secure=True,
        **{
            'HTTP_ORIGIN': 'https://testserver',
            'HTTP_X_CSRFTOKEN': browser.cookies['csrftoken'].value,
            **headers,
        },
    )


def send(browser, scenario='success'):
    return post(browser, json.dumps({'scenario': scenario}))


def detail_url(event_id):
    return reverse('demo-detail', kwargs={'event_id': event_id})


def assert_no_start(sender):
    assert not Event.objects.exists()
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_home_prepares_visitor_and_csrf_without_database_writes(django_assert_num_queries, sender):
    browser = Client(enforce_csrf_checks=True)
    with django_assert_num_queries(0):
        response = browser.get(reverse('demo-home'), secure=True)

    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'text/html; charset=utf-8'
    assert response.context['demo']['enabled'] is True
    assert response.context['demo']['payload'] == {'pedido_id': 'PEDIDO-001'}
    assert response.context['demo']['limits'] == {'per_minute': 3, 'per_day': 4, 'global_per_day': 20}
    assert response.context['csrf_token']
    assert set(response.cookies) == {VISITOR_COOKIE_NAME, 'csrftoken'}
    assert response.cookies[VISITOR_COOKIE_NAME]['httponly']
    assert response.cookies[VISITOR_COOKIE_NAME]['secure']
    assert response.cookies['csrftoken']['secure']
    assert response.headers['Cache-Control'] == 'no-store'
    assert_no_start(sender)


@pytest.mark.parametrize(('name', 'value'), [
    ('WEBHOOK_DEMO_ENABLED', False), ('WEBHOOK_DEMO_RECEIVER_ENABLED', False),
    ('WEBHOOK_MAX_DELIVERIES_PER_DAY', 0),
])
def test_home_shows_disabled_demo_without_starting_delivery(browser, settings, sender, name, value):
    setattr(settings, name, value)

    response = browser.get(reverse('demo-home'), secure=True)

    assert response.context['demo']['enabled'] is False
    assert 'Os envios estão indisponíveis' in response.content.decode()
    assert_no_start(sender)


@pytest.mark.parametrize('enabled', [True, False])
def test_home_renders_controlled_example_and_prepares_safe_browser_controls(browser, settings, sender, enabled):
    settings.WEBHOOK_DEMO_ENABLED = enabled
    response = browser.get(reverse('demo-home'), secure=True)
    html = response.content.decode()
    page = DemoPage(html)

    assert json.loads(page.config) == response.context['demo']
    assert json.loads(page.config)['enabled'] is enabled
    assert json.loads(page.example) == {
        'event_type': 'pedido.pago', 'payload': {'pedido_id': 'PEDIDO-001'},
    }
    assert 'webhooks/demo.html' in [template.name for template in response.templates]
    buttons = [attrs for tag, attrs in page.tags if tag == 'button']
    assert {attrs.get('data-scenario') for attrs in buttons if 'data-scenario' in attrs} == {
        'success', 'failure',
    }
    assert {attrs['id'] for attrs in buttons} == {'send-success', 'send-failure', 'consult-result'}
    assert all(attrs['type'] == 'button' and 'disabled' in attrs for attrs in buttons)
    csrf_inputs = [attrs for tag, attrs in page.tags if attrs.get('name') == 'csrfmiddlewaretoken']
    assert len(csrf_inputs) == 1
    assert csrf_inputs[0]['type'] == 'hidden' and len(csrf_inputs[0]['value']) == 64
    assert any(tag == 'p' and attrs.get('role') == 'status' for tag, attrs in page.tags)
    assert settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID not in html
    assert settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID not in html
    assert all(tag != 'form' for tag, attrs in page.tags)
    assert_no_start(sender)


def test_head_home_preserves_protection_headers_without_body_or_delivery(browser, sender):
    response = browser.head(reverse('demo-home'), secure=True)

    assert response.status_code == 200
    assert response.content == b''
    assert response.headers['Content-Type'] == 'text/html; charset=utf-8'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'Cookie' in response.headers['Vary']
    assert response.cookies[VISITOR_COOKIE_NAME]['httponly']
    assert_no_start(sender)


@pytest.mark.parametrize(('scenario', 'result', 'state'), [
    ('success', DeliveryResult(200, ''), 'delivered'),
    ('failure', DeliveryResult(500, 'http_error'), 'failed'),
    ('success', DeliveryResult(None, 'timeout'), 'failed'),
])
def test_anonymous_visitor_can_send_and_consult_own_result(browser, sender, scenario, result, state):
    sender.return_value = result
    sent = send(browser, scenario)

    assert sent.status_code == 200
    body = sent.json()
    attempt = DeliveryAttempt.objects.get()
    assert body['event_status'] == state
    assert body['http_status'] == result.http_status
    assert body['error_code'] == result.error_code
    assert attempt.visitor_id == get_visitor_id(sent.wsgi_request)
    assert attempt.initiated_by_id is None
    read = browser.get(detail_url(body['event_id']), secure=True)
    assert read.status_code == 200
    assert read.json() == body
    assert read.headers['Cache-Control'] == 'no-store'
    assert 'Cookie' in read.headers['Vary']
    sender.assert_called_once()


@pytest.mark.parametrize('failure', ['missing_token', 'wrong_token', 'wrong_origin'])
def test_demo_post_enforces_csrf_for_anonymous_visitors(browser, sender, failure):
    headers = {
        'HTTP_X_CSRFTOKEN': '' if failure == 'missing_token' else 'x' * 32,
    }
    if failure == 'wrong_origin':
        headers = {'HTTP_ORIGIN': 'https://untrusted.example'}

    response = post(browser, '{"scenario":"success"}', **headers)

    assert response.status_code == 403
    assert_no_start(sender)


@pytest.mark.parametrize('cookie', ['missing', 'unsigned', 'tampered'])
def test_post_requires_valid_visitor_cookie_even_with_valid_csrf(browser, sender, cookie):
    if cookie == 'missing':
        del browser.cookies[VISITOR_COOKIE_NAME]
    elif cookie == 'unsigned':
        browser.cookies[VISITOR_COOKIE_NAME] = str(uuid4())
    else:
        browser.cookies[VISITOR_COOKIE_NAME] = browser.cookies[VISITOR_COOKIE_NAME].value + 'changed'

    response = send(browser)

    assert response.status_code == 403
    assert response.json()['code'] == 'invalid_visitor'
    assert_no_start(sender)


@pytest.mark.parametrize('data', [
    None, [], 'text', 123, {}, {'scenario': 'unknown'},
    *[{'scenario': 'success', field: 'PRIVATE_INPUT'} for field in [
        'url', 'payload', 'headers', 'visitor_id', 'event_id', 'method', 'endpoint_id',
    ]],
])
def test_post_rejects_nonobject_or_unapproved_fields(browser, sender, data):
    response = post(browser, json.dumps(data))

    assert response.status_code == 400
    assert 'PRIVATE_INPUT' not in response.content.decode()
    assert_no_start(sender)


@pytest.mark.parametrize(('body', 'content_type', 'expected'), [
    (b'{', 'application/json', 400), (b'{"value":NaN}', 'application/json', 400),
    (b'"\xff"', 'application/json', 400),
    (b'{"scenario":"success"}', 'text/plain', 415),
    (b'{"scenario":"success"}', 'application/x-www-form-urlencoded', 415),
])
def test_post_rejects_invalid_json_and_media(browser, sender, body, content_type, expected):
    assert post(browser, body, content_type).status_code == expected
    assert_no_start(sender)


@pytest.mark.parametrize(('extra', 'expected'), [(0, 200), (1, 413)])
def test_post_has_bounded_body_size(browser, sender, extra, expected):
    body = b'{"scenario":"success"}'
    body += b' ' * (MAX_DEMO_REQUEST_BYTES + extra - len(body))

    assert post(browser, body).status_code == expected
    if extra:
        assert_no_start(sender)
    else:
        sender.assert_called_once()


@pytest.mark.parametrize('access', ['other_visitor', 'missing_cookie', 'unknown_event'])
def test_result_cannot_be_read_by_event_id_alone(browser, sender, access):
    event_id = send(browser).json()['event_id']
    if access == 'other_visitor':
        browser = Client(enforce_csrf_checks=True)
        browser.get(reverse('demo-home'), secure=True)
    elif access == 'missing_cookie':
        del browser.cookies[VISITOR_COOKIE_NAME]
    else:
        event_id = uuid4()

    response = browser.get(detail_url(event_id), secure=True)

    assert response.status_code == 404
    assert response.json() == {'detail': 'Tentativa não encontrada.', 'code': 'not_found'}
    sender.assert_called_once()


@pytest.mark.parametrize('failure', ['disabled', 'configuration', 'busy'])
def test_expected_unavailability_has_safe_response(browser, sender, settings, monkeypatch, failure):
    if failure == 'disabled':
        settings.WEBHOOK_DEMO_ENABLED = False
    elif failure == 'configuration':
        settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID = 'PRIVATE_CONFIG'
    else:
        monkeypatch.setattr('webhooks.demo_views.deliver_demo', Mock(side_effect=DeliveryLimitError(
            'busy', 'Reserva temporariamente ocupada.', retry_after=1,
        )))

    response = send(browser)

    assert response.status_code == 503
    assert 'PRIVATE_CONFIG' not in response.content.decode()
    if failure == 'busy':
        assert response.headers['Retry-After'] == '1'
    assert_no_start(sender)


def test_exhausted_visitor_quota_returns_429_without_new_event(browser, sender, settings):
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 1
    first = send(browser)
    blocked = send(browser)

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()['code'] == 'visitor_minute_limit'
    assert 1 <= int(blocked.headers['Retry-After']) <= 60
    assert Event.objects.count() == DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()


def test_unexpected_error_exposes_no_details_and_saved_start_remains_readable(browser, sender, caplog):
    secret = 'PRIVATE_EXCEPTION_DETAILS'
    sender.side_effect = RuntimeError(secret)
    response = send(browser)

    assert response.status_code == 500
    assert secret not in response.content.decode()
    assert secret not in caplog.text
    event_id = UUID(response.json()['event_id'])
    attempt = DeliveryAttempt.objects.get(event_id=event_id)
    assert attempt.event.status == Event.Status.SENDING
    assert attempt.finished_at is None
    read = browser.get(detail_url(event_id), secure=True)
    assert read.status_code == 200
    assert read.json()['event_status'] == 'sending'
    sender.assert_called_once()


@pytest.mark.parametrize('resource', ['event', 'attempt'])
def test_visitor_cookie_does_not_grant_operator_api_access(browser, resource):
    event_id = send(browser).json()['event_id']
    url = f'/api/eventos/{event_id}/' + ('tentativa/' if resource == 'attempt' else '')

    assert browser.get(url, secure=True).status_code == 403


@pytest.mark.parametrize(('name', 'method', 'kwargs'), [
    ('demo-home', 'post', {}), ('demo-send', 'get', {}),
    ('demo-detail', 'post', {'event_id': UUID(int=1)}),
    ('demo-send', 'put', {}),
])
def test_demo_routes_reject_unsupported_methods(browser, name, method, kwargs, sender):
    response = getattr(browser, method)(
        reverse(name, kwargs=kwargs), secure=True,
        HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=browser.cookies['csrftoken'].value,
    )

    assert response.status_code == 405
    assert_no_start(sender)
