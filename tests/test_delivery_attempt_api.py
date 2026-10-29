import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


pytestmark = pytest.mark.django_db


@pytest.fixture
def event(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    return Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
    )


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user(username='reader', is_staff=True)
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label='webhooks', content_type__model='deliveryattempt',
        codename='view_deliveryattempt',
    ))
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def attempt(event, operator):
    Event.objects.filter(pk=event.pk).update(status=Event.Status.SENDING)
    return DeliveryAttempt.objects.create(event=event, initiated_by=operator)


@pytest.fixture(autouse=True)
def no_delivery(monkeypatch):
    sender = Mock(side_effect=AssertionError('A consulta não deve iniciar um envio.'))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    yield
    sender.assert_not_called()


@pytest.mark.parametrize(('http_status', 'error_code', 'status', 'finished'), [
    (None, '', 'sending', False),
    (200, '', 'delivered', True), (204, '', 'delivered', True),
    (302, 'http_error', 'failed', True), (500, 'http_error', 'failed', True),
    (None, 'network_error', 'failed', True), (None, 'timeout', 'failed', True),
    (None, 'destination_rejected', 'failed', True),
])
def test_operator_reads_attempt_metadata_without_changing_records(
    operator_client, attempt, http_status, error_code, status, finished,
):
    attempt.finished_at = attempt.started_at + timedelta(seconds=1) if finished else None
    attempt.http_status = http_status
    attempt.error_code = error_code
    attempt.full_clean()
    attempt.save()
    Event.objects.filter(pk=attempt.event_id).update(status=status)

    response = operator_client.get(f'/api/eventos/{attempt.event_id}/tentativa/')

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        'id', 'event_id', 'event_status', 'started_at', 'finished_at',
        'http_status', 'error_code',
    }
    assert body['id'] == str(attempt.pk)
    assert body['event_id'] == str(attempt.event_id)
    assert body['event_status'] == status
    assert parse_datetime(body['started_at']) == attempt.started_at
    assert body['http_status'] == http_status
    assert body['error_code'] == error_code
    if finished:
        assert parse_datetime(body['finished_at']) == attempt.finished_at
    else:
        assert body['finished_at'] is None
    attempt.refresh_from_db()
    assert attempt.http_status == http_status
    assert attempt.error_code == error_code
    assert attempt.event.status == status
    assert DeliveryAttempt.objects.count() == 1


@pytest.mark.parametrize('method', ['get', 'head', 'options'])
@pytest.mark.parametrize('role', ['anonymous', 'ordinary', 'staff_without_permission', 'inactive'])
def test_attempt_reading_requires_active_staff_session_and_permission(
    client, operator, attempt, method, role,
):
    if role != 'anonymous':
        if role == 'ordinary':
            operator.is_staff = False
        elif role == 'staff_without_permission':
            operator.user_permissions.clear()
        else:
            operator.is_active = False
        operator.save()
        client.force_login(operator)

    response = getattr(client, method)(f'/api/eventos/{attempt.event_id}/tentativa/')

    assert response.status_code == 403
    assert DeliveryAttempt.objects.count() == 1


@pytest.mark.parametrize('codename', ['add_event', 'view_event', 'send_event'])
def test_event_permissions_do_not_grant_attempt_reading(client, operator, attempt, codename):
    operator.user_permissions.set(Permission.objects.filter(
        content_type__app_label='webhooks', content_type__model='event', codename=codename,
    ))
    client.force_login(operator)

    response = client.get(f'/api/eventos/{attempt.event_id}/tentativa/')

    assert response.status_code == 403


@pytest.mark.parametrize('missing', ['event', 'attempt'])
def test_returns_not_found_without_creating_attempt(operator_client, event, missing):
    event_id = uuid.uuid4() if missing == 'event' else event.pk

    response = operator_client.get(f'/api/eventos/{event_id}/tentativa/')

    assert response.status_code == 404
    assert not DeliveryAttempt.objects.exists()
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING


def test_malformed_event_id_is_rejected_by_routing(operator_client):
    response = operator_client.get('/api/eventos/not-a-uuid/tentativa/')

    assert response.status_code == 404


@pytest.mark.parametrize('method', ['head', 'options'])
def test_operator_can_use_read_only_metadata_methods(operator_client, attempt, method):
    url = reverse('webhooks:delivery-attempt-detail', kwargs={'pk': attempt.event_id})

    response = getattr(operator_client, method)(url)

    assert response.status_code == 200
    assert set(response['Allow'].replace(' ', '').split(',')) == {'GET', 'HEAD', 'OPTIONS'}
    if method == 'head':
        assert response.content == b''


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_attempt_route_rejects_writes(operator_client, attempt, method):
    response = operator_client.generic(
        method, f'/api/eventos/{attempt.event_id}/tentativa/',
        data='{}', content_type='application/json',
    )

    assert response.status_code == 405
    attempt.refresh_from_db()
    assert attempt.finished_at is None
    assert attempt.event.status == Event.Status.SENDING


@pytest.mark.parametrize('policy', ['removed_destination', 'disabled_delivery'])
def test_history_remains_readable_after_delivery_policy_changes(
    operator_client, attempt, settings, policy,
):
    if policy == 'removed_destination':
        settings.WEBHOOK_ALLOWED_URLS = []
    else:
        settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 0

    response = operator_client.get(f'/api/eventos/{attempt.event_id}/tentativa/')

    assert response.status_code == 200
    assert response.json()['event_id'] == str(attempt.event_id)


def test_authorized_operator_can_read_attempt_started_by_another_operator(
    operator_client, attempt, django_user_model,
):
    other = django_user_model.objects.create_user(username='sender', is_staff=True)
    attempt.initiated_by = other
    attempt.save(update_fields=['initiated_by'])

    response = operator_client.get(f'/api/eventos/{attempt.event_id}/tentativa/')

    assert response.status_code == 200
    assert 'initiated_by' not in response.json()
