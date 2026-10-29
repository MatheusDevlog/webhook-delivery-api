import json
from io import BytesIO
from runpy import run_path
from unittest.mock import Mock

import pytest
from django.test import Client
from django.urls import reverse

from config import settings as project_settings
from webhooks.receivers import DemoBodyTooLarge, DemoJSONParser, MAX_DEMO_BODY_BYTES


@pytest.fixture(autouse=True)
def enable_receiver(settings):
    settings.WEBHOOK_DEMO_RECEIVER_ENABLED = True


@pytest.fixture(params=[
    ('demo-receiver-success', 200, 'received'),
    ('demo-receiver-failure', 500, 'rejected'),
], ids=['success', 'failure'])
def receiver(request):
    return request.param


@pytest.fixture
def envelope():
    return {
        'id': '11111111-1111-4111-8111-111111111111',
        'event_type': 'pedido.pago',
        'created_at': '2026-10-29T12:00:00Z',
        'payload': {'pedido_id': 123},
    }


def receive(client, data, receiver):
    return client.post(
        reverse(receiver[0]), json.dumps(data), content_type='application/json',
    )


def test_receivers_return_expected_result_without_session_or_csrf(envelope, monkeypatch, receiver):
    sender = Mock()
    resolver = Mock()
    monkeypatch.setattr('webhooks.services.post_json', sender)
    monkeypatch.setattr('socket.getaddrinfo', resolver)
    client = Client(enforce_csrf_checks=True)
    client.cookies['sessionid'] = 'invalid-session'
    client.cookies['csrftoken'] = 'invalid-token'

    response = receive(client, envelope, receiver)

    assert response.status_code == receiver[1]
    assert response.json() == {'status': receiver[2], 'event_id': envelope['id']}
    assert response.headers['Content-Type'] == 'application/json'
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'sessionid' not in response.cookies
    sender.assert_not_called()
    resolver.assert_not_called()


@pytest.mark.parametrize('body', [b'{}', b'{', b' ' * (MAX_DEMO_BODY_BYTES + 1)])
def test_disabled_receiver_returns_not_found_before_parsing(client, settings, body, receiver):
    settings.WEBHOOK_DEMO_RECEIVER_ENABLED = False

    response = client.post(reverse(receiver[0]), body, content_type='application/json')

    assert response.status_code == 404


@pytest.mark.parametrize(('value', 'expected'), [
    (None, False), ('False', False), ('true', False), ('1', False), ('True', True),
])
def test_receiver_requires_explicit_true_environment_value(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('WEBHOOK_DEMO_RECEIVER_ENABLED', raising=False)
    else:
        monkeypatch.setenv('WEBHOOK_DEMO_RECEIVER_ENABLED', value)

    assert run_path(project_settings.__file__)['WEBHOOK_DEMO_RECEIVER_ENABLED'] is expected


@pytest.mark.parametrize('method', ['get', 'head', 'options', 'put', 'patch', 'delete'])
def test_receiver_rejects_other_methods(client, method, receiver):
    response = getattr(client, method)(reverse(receiver[0]))

    assert response.status_code == 405
    assert response.headers['Allow'] == 'POST'


@pytest.mark.parametrize('field', ['id', 'event_type', 'created_at', 'payload'])
def test_receiver_requires_every_envelope_field(client, envelope, field, receiver):
    del envelope[field]

    response = receive(client, envelope, receiver)

    assert response.status_code == 400
    assert field in response.json()


@pytest.mark.parametrize(('field', 'value'), [
    ('id', 'invalid'), ('created_at', 'yesterday'), ('event_type', 'cliente.cadastrado'),
    ('payload', {}), ('payload', []), ('payload', None), ('payload', {'pedido_id': 999}),
    ('payload', {'pedido_id': '123'}), ('payload', {'pedido_id': 123.0}),
    ('payload', {'pedido_id': 123, 'email': 'private@example.com'}),
])
def test_receiver_rejects_invalid_or_nonfictitious_values(client, envelope, field, value, receiver):
    envelope[field] = value

    response = receive(client, envelope, receiver)

    assert response.status_code == 400
    assert field in response.json()
    assert b'private@example.com' not in response.content


@pytest.mark.parametrize('field', ['url', 'headers', 'method', 'endpoint_id'])
def test_receiver_rejects_additional_fields(client, envelope, field, receiver):
    envelope[field] = 'private-value'

    response = receive(client, envelope, receiver)

    assert response.status_code == 400
    assert b'private-value' not in response.content


@pytest.mark.parametrize('body', [[], None, 'text', 123, True])
def test_receiver_rejects_nonobject_json(client, body, receiver):
    assert receive(client, body, receiver).status_code == 400


@pytest.mark.parametrize('body', [b'{', b'{"value": NaN}', b'"\xff"', b'[' * 2000])
def test_receiver_rejects_malformed_json_and_excessive_nesting(client, body, receiver):
    response = client.post(reverse(receiver[0]), body, content_type='application/json')

    assert response.status_code == 400
    assert response.json() == {'detail': 'Envie um objeto JSON válido em UTF-8.'}


@pytest.mark.parametrize('content_type', ['text/plain', 'application/x-www-form-urlencoded'])
def test_receiver_only_accepts_json(client, envelope, content_type, receiver):
    response = client.post(
        reverse(receiver[0]), json.dumps(envelope), content_type=content_type,
    )

    assert response.status_code == 415


@pytest.mark.parametrize('extra', [0, 1])
def test_receiver_enforces_envelope_byte_limit(client, envelope, extra, receiver):
    body = json.dumps(envelope).encode('utf-8')
    body += b' ' * (MAX_DEMO_BODY_BYTES + extra - len(body))

    response = client.post(reverse(receiver[0]), body, content_type='application/json')

    assert response.status_code == (413 if extra else receiver[1])


def test_receiver_limit_counts_bytes_instead_of_characters(client, receiver):
    body = json.dumps('é' * 9000, ensure_ascii=False).encode('utf-8')

    response = client.post(reverse(receiver[0]), body, content_type='application/json')

    assert response.status_code == 413


def test_receiver_parser_does_not_read_entire_oversized_body():
    stream = BytesIO(b' ' * (MAX_DEMO_BODY_BYTES * 2))

    with pytest.raises(DemoBodyTooLarge):
        DemoJSONParser().parse(stream)

    assert stream.tell() == MAX_DEMO_BODY_BYTES + 1
