import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections
from django.test import Client
from django.urls import reverse

from webhooks.delivery import DeliveryResult
from webhooks.limits import DeliveryLimitError
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def event(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 20
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    return Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 'PEDIDO-001'},
    )


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user(username='sender', is_staff=True)
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label='webhooks', content_type__model='event', codename='send_event',
    ))
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    sender = Mock(return_value=DeliveryResult(204, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    return sender


def send_url(event_id):
    return reverse('webhooks:event-send', kwargs={'pk': event_id})


@pytest.mark.parametrize(('result', 'status'), [
    (DeliveryResult(204, ''), 'delivered'), (DeliveryResult(302, 'http_error'), 'failed'),
    (DeliveryResult(500, 'http_error'), 'failed'),
    (DeliveryResult(None, 'timeout'), 'failed'), (DeliveryResult(None, 'network_error'), 'failed'),
    (ValidationError('Destino bloqueado.', code='non_public_destination'), 'failed'),
])
def test_sending_returns_persisted_attempt_with_authenticated_operator(
    operator_client, operator, event, sender, result, status,
):
    if isinstance(result, Exception):
        sender.side_effect = result
    else:
        sender.return_value = result

    response = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 200
    attempt = DeliveryAttempt.objects.get(event=event)
    body = response.json()
    assert set(body) == {
        'id', 'event_id', 'event_status', 'started_at', 'finished_at', 'http_status', 'error_code',
    }
    assert body['id'] == str(attempt.pk)
    assert body['event_id'] == str(event.pk)
    assert body['event_status'] == attempt.event.status == status
    assert body['http_status'] == attempt.http_status
    assert body['error_code'] == attempt.error_code
    assert attempt.finished_at >= attempt.started_at
    assert attempt.initiated_by_id == operator.pk
    sender.assert_called_once()
    assert sender.call_args.args[0] == event.endpoint.url
    assert sender.call_args.args[1]['payload'] == event.payload
    assert sender.call_args.kwargs == {}


def test_empty_body_is_allowed_and_result_can_be_read_afterwards(
    operator_client, operator, event, sender,
):
    operator.user_permissions.add(Permission.objects.get(
        content_type__app_label='webhooks', content_type__model='deliveryattempt',
        codename='view_deliveryattempt',
    ))
    sent = operator_client.post(send_url(event.pk), '', content_type='application/json')
    detail = operator_client.get(f'/api/eventos/{event.pk}/tentativa/')

    assert sent.status_code == detail.status_code == 200
    assert sent.json() == detail.json()
    sender.assert_called_once()


@pytest.mark.parametrize('role', [
    'anonymous', 'ordinary', 'staff', 'inactive', 'add_event', 'view_event', 'view_deliveryattempt',
])
def test_sending_requires_active_staff_session_and_sending_permission(
    client, operator, event, sender, role,
):
    if role != 'anonymous':
        if role == 'ordinary':
            operator.is_staff = False
        elif role == 'inactive':
            operator.is_active = False
        else:
            operator.user_permissions.set(Permission.objects.filter(
                content_type__app_label='webhooks', codename=role,
            ))
        operator.save()
        client.force_login(operator)

    response = client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 403
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('data', [
    [], None, 'text', 123, {'url': 'https://127.0.0.1/'}, {'payload': {}},
    {'headers': {}}, {'method': 'GET'}, {'initiated_by': 123}, {'endpoint_id': str(uuid.UUID(int=1))},
])
def test_sending_rejects_nonempty_or_nonobject_body(operator_client, event, sender, data):
    response = operator_client.post(
        send_url(event.pk), json.dumps(data), content_type='application/json',
    )

    assert response.status_code == 400
    assert not DeliveryAttempt.objects.exists()
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    sender.assert_not_called()


@pytest.mark.parametrize(('data', 'content_type', 'expected'), [
    ('{', 'application/json', 400), ('{"value": NaN}', 'application/json', 400),
    ('{}', 'text/plain', 415), ('value=x', 'application/x-www-form-urlencoded', 415),
])
def test_sending_rejects_invalid_json_and_unsupported_media(
    operator_client, event, sender, data, content_type, expected,
):
    response = operator_client.post(send_url(event.pk), data, content_type=content_type)

    assert response.status_code == expected
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('result', [
    DeliveryResult(204, ''), DeliveryResult(500, 'http_error'), DeliveryResult(None, 'timeout'),
])
def test_repeated_send_returns_conflict_without_resending(operator_client, event, sender, result):
    sender.return_value = result
    first = operator_client.post(send_url(event.pk), {}, content_type='application/json')
    repeated = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert first.status_code == 200
    assert repeated.status_code == 409
    assert repeated.json()['code'] == 'delivery_already_started'
    assert DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()


@pytest.mark.parametrize('quota', ['global', 'operator'])
def test_budget_rejection_returns_retry_after_and_keeps_event_pending(
    operator_client, event, sender, settings, quota,
):
    name = 'WEBHOOK_MAX_DELIVERIES_PER_DAY' if quota == 'global' else 'WEBHOOK_MAX_DELIVERIES_PER_MINUTE'
    setattr(settings, name, 1)
    first = operator_client.post(send_url(event.pk), {}, content_type='application/json')
    other = Event.objects.create(endpoint=event.endpoint, event_type=event.event_type, payload={})
    response = operator_client.post(send_url(other.pk), {}, content_type='application/json')

    assert first.status_code == 200
    assert response.status_code == 429
    assert response.json()['code'] == f'{quota}_limit'
    assert int(response['Retry-After']) >= 1
    other.refresh_from_db()
    assert other.status == Event.Status.PENDING
    assert DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()


def test_disabled_delivery_returns_unavailable_without_attempt(operator_client, event, sender, settings):
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 0
    response = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 503
    assert response.json()['code'] == 'disabled'
    assert 'Retry-After' not in response
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_busy_budget_returns_unavailable_with_short_retry_after(
    operator_client, event, sender, monkeypatch,
):
    operation = Mock(side_effect=DeliveryLimitError('busy', 'Reserva ocupada.', retry_after=1))
    monkeypatch.setattr('webhooks.views.deliver_event', operation)
    response = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 503
    assert response.json()['code'] == 'busy'
    assert response['Retry-After'] == '1'
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('invalid', ['destination', 'payload'])
def test_invalid_stored_event_returns_safe_error_without_attempt(
    operator_client, event, sender, settings, invalid,
):
    if invalid == 'destination':
        settings.WEBHOOK_ALLOWED_URLS = []
    else:
        Event.objects.filter(pk=event.pk).update(payload=[])
    response = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 400
    assert response.json()['code'] == 'invalid_event'
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('failure', ['transport', 'body', 'database'])
def test_internal_failure_preserves_uncertain_attempt_without_exposing_details(
    operator_client, event, sender, monkeypatch, caplog, failure,
):
    secret = 'INTERNAL_SECRET_DO_NOT_EXPOSE'
    if failure == 'database':
        monkeypatch.setattr(Event, 'save', Mock(side_effect=IntegrityError(secret)))
    elif failure == 'body':
        sender.side_effect = ValidationError(secret, code='invalid_delivery_body')
    else:
        sender.side_effect = RuntimeError(secret)
    response = operator_client.post(send_url(event.pk), {}, content_type='application/json')

    assert response.status_code == 500
    assert response.json()['code'] == 'delivery_error'
    assert secret not in response.content.decode()
    assert secret not in caplog.text
    attempt = DeliveryAttempt.objects.get(event=event)
    assert attempt.event.status == Event.Status.SENDING
    assert attempt.finished_at is None
    assert attempt.http_status is None
    repeated = operator_client.post(send_url(event.pk), {}, content_type='application/json')
    assert repeated.status_code == 409
    sender.assert_called_once()


def test_sending_requires_csrf_even_with_authorized_session(operator, event, sender):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    response = client.post(send_url(event.pk), {}, content_type='application/json', secure=True)

    assert response.status_code == 403
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_session_and_csrf_allow_sending_over_simulated_https(operator, event, sender):
    client = Client(enforce_csrf_checks=True)
    client.get(reverse('admin:login'), secure=True)
    client.force_login(operator)
    response = client.post(
        send_url(event.pk), {}, content_type='application/json', secure=True,
        HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,
        HTTP_REFERER='https://testserver/admin/',
    )

    assert response.status_code == 200
    assert DeliveryAttempt.objects.get().initiated_by_id == operator.pk
    sender.assert_called_once()


@pytest.mark.parametrize('event_id', [uuid.UUID(int=1), 'invalid'])
def test_missing_or_invalid_event_returns_not_found(operator_client, event, sender, event_id):
    response = operator_client.post(
        f'/api/eventos/{event_id}/enviar/', {}, content_type='application/json',
    )

    assert response.status_code == 404
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
def test_other_methods_never_send(operator_client, event, sender, method):
    response = operator_client.generic(method, send_url(event.pk))

    assert response.status_code == (200 if method == 'OPTIONS' else 405)
    assert set(response['Allow'].replace(' ', '').split(',')) == {'POST', 'OPTIONS'}
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('scenario', ['same_event', 'last_slot'])
def test_http_concurrent_requests_only_send_once(operator, event, sender, settings, monkeypatch, scenario):
    events = [event, event]
    if scenario == 'last_slot':
        settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 1
        events[1] = Event.objects.create(endpoint=event.endpoint, event_type=event.event_type, payload={})
    clients = [Client(), Client()]
    for client in clients:
        client.force_login(operator)
    ready = Barrier(2, timeout=10)
    full_clean = Event.full_clean

    def validate_together(instance, *args, **kwargs):
        full_clean(instance, *args, **kwargs)
        ready.wait()

    monkeypatch.setattr(Event, 'full_clean', validate_together)

    def send(client, event_id):
        try:
            return client.post(send_url(event_id), {}, content_type='application/json').status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, client, item.pk) for client, item in zip(clients, events)]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(results) == [200, 409 if scenario == 'same_event' else 429]
    assert DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()
