import json
import uuid
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from webhooks.delivery import MAX_REQUEST_BYTES, DeliveryResult
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import (
    DeliveryAlreadyStarted,
    _reserve_delivery,
    _send_and_finish_delivery,
    _start_delivery,
    deliver_event,
)
from webhooks.validators import MAX_EVENT_PAYLOAD_BYTES


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


def test_starts_one_unfinished_attempt_and_persists_sending_status(event):
    attempt = _start_delivery(event.pk)
    attempt.refresh_from_db()
    event.refresh_from_db()

    assert attempt.event_id == event.pk
    assert attempt.started_at is not None
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert attempt.error_code == ''
    assert event.status == Event.Status.SENDING
    assert DeliveryAttempt.objects.filter(event=event).count() == 1


def test_repeated_start_keeps_the_original_attempt(event):
    first = _start_delivery(event.pk)

    with pytest.raises(DeliveryAlreadyStarted):
        _start_delivery(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.SENDING
    assert DeliveryAttempt.objects.get(event=event).pk == first.pk


@pytest.mark.parametrize('status', ['sending', 'delivered', 'failed'])
def test_rejects_non_pending_event_without_creating_attempt(event, status):
    Event.objects.filter(pk=event.pk).update(status=status)

    with pytest.raises(DeliveryAlreadyStarted):
        _start_delivery(event.pk)

    event.refresh_from_db()
    assert event.status == status
    assert not DeliveryAttempt.objects.exists()


def test_rejects_existing_attempt_even_if_event_is_pending(event):
    original = DeliveryAttempt.objects.create(event=event)

    with pytest.raises(DeliveryAlreadyStarted):
        _start_delivery(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert DeliveryAttempt.objects.get(event=event).pk == original.pk


def test_rejects_unknown_event_without_creating_attempt():
    with pytest.raises(Event.DoesNotExist):
        _start_delivery(uuid.uuid4())

    assert not DeliveryAttempt.objects.exists()


def test_revalidates_destination_before_changing_status(event, settings):
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(ValidationError) as error:
        _start_delivery(event.pk)

    assert error.value.error_dict['endpoint'][0].code == 'unapproved_destination'
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()


@pytest.mark.parametrize('field, value', [
    ('payload', []), ('event_type', 'pedido.cancelado'),
])
def test_revalidates_stored_event_before_starting(event, field, value):
    Event.objects.filter(pk=event.pk).update(**{field: value})

    with pytest.raises(ValidationError) as error:
        _start_delivery(event.pk)

    assert field in error.value.message_dict
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()


def test_failed_attempt_insert_rolls_back_status_change(event, monkeypatch):
    create = Mock(side_effect=IntegrityError('Falha de persistência simulada.'))
    monkeypatch.setattr(DeliveryAttempt.objects, 'create', create)

    with pytest.raises(IntegrityError):
        _start_delivery(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()


def test_refuses_to_start_inside_an_existing_transaction(event):
    with transaction.atomic():
        with pytest.raises(RuntimeError):
            _start_delivery(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()


@pytest.fixture
def sender(monkeypatch):
    sender = Mock(return_value=DeliveryResult(204, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    return sender


@pytest.mark.parametrize(('http_status', 'error_code', 'status'), [
    (200, '', 'delivered'), (204, '', 'delivered'), (299, '', 'delivered'),
    (302, 'http_error', 'failed'), (400, 'http_error', 'failed'),
    (500, 'http_error', 'failed'), (599, 'http_error', 'failed'),
    (None, 'network_error', 'failed'), (None, 'timeout', 'failed'),
])
def test_records_transport_result_and_final_event_status(
    event, sender, http_status, error_code, status,
):
    sender.return_value = DeliveryResult(http_status, error_code)

    attempt = deliver_event(event.pk)
    attempt.refresh_from_db()
    event.refresh_from_db()

    assert attempt.event_id == event.pk
    assert attempt.finished_at >= attempt.started_at
    assert attempt.http_status == http_status
    assert attempt.error_code == error_code
    assert event.status == status
    assert DeliveryAttempt.objects.filter(event=event).count() == 1
    sender.assert_called_once()


def test_sends_event_envelope_after_persisting_start_outside_transaction(event, sender):
    expected = {
        'id': str(event.pk),
        'event_type': 'pedido.pago',
        'created_at': event.created_at.isoformat(),
        'payload': {'pedido_id': 'PEDIDO-001'},
    }

    def send(url, data):
        assert not connection.in_atomic_block
        assert connection.get_autocommit()
        event.refresh_from_db()
        assert event.status == Event.Status.SENDING
        assert DeliveryAttempt.objects.get(event=event).finished_at is None
        assert data == expected
        return DeliveryResult(204, '')

    sender.side_effect = send
    deliver_event(event.pk)

    sender.assert_called_once_with(event.endpoint.url, expected)


def test_envelope_with_maximum_payload_fits_transport_limit(event, sender):
    event_type = 'a.' + 'b' * 98
    overhead = len(json.dumps({'data': ''}, separators=(',', ':')).encode('utf-8'))
    payload = {'data': 'x' * (MAX_EVENT_PAYLOAD_BYTES - overhead)}
    WebhookEndpoint.objects.filter(pk=event.endpoint_id).update(event_type=event_type)
    Event.objects.filter(pk=event.pk).update(event_type=event_type, payload=payload)

    deliver_event(event.pk)

    data = sender.call_args.args[1]
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    assert data['event_type'] == event_type
    assert data['payload'] == payload
    assert MAX_EVENT_PAYLOAD_BYTES < len(encoded) <= MAX_REQUEST_BYTES


@pytest.mark.parametrize('code', [
    'non_public_destination', 'unapproved_destination', 'invalid_destination_hostname',
])
def test_records_destination_rejection_without_inventing_http_status(event, sender, code):
    sender.side_effect = ValidationError('Destino bloqueado.', code=code)

    attempt = deliver_event(event.pk)
    attempt.refresh_from_db()
    event.refresh_from_db()

    assert attempt.finished_at >= attempt.started_at
    assert attempt.http_status is None
    assert attempt.error_code == 'destination_rejected'
    assert event.status == Event.Status.FAILED
    sender.assert_called_once()


@pytest.mark.parametrize('code', ['invalid_delivery_body', 'delivery_body_too_large'])
def test_does_not_mislabel_invalid_envelope_as_destination_rejection(event, sender, code):
    sender.side_effect = ValidationError('Corpo de envio inválido.', code=code)

    with pytest.raises(ValidationError) as error:
        deliver_event(event.pk)

    assert error.value.code == code
    attempt = DeliveryAttempt.objects.get(event=event)
    event.refresh_from_db()
    assert attempt.finished_at is None
    assert attempt.error_code == ''
    assert event.status == Event.Status.SENDING


@pytest.mark.parametrize('result', [
    DeliveryResult(204, ''), DeliveryResult(500, 'http_error'), DeliveryResult(None, 'timeout'),
])
def test_does_not_resend_an_event_after_completed_attempt(event, sender, result):
    sender.return_value = result
    first = deliver_event(event.pk)

    with pytest.raises(DeliveryAlreadyStarted):
        deliver_event(event.pk)

    saved = DeliveryAttempt.objects.get(event=event)
    assert saved.pk == first.pk
    assert saved.finished_at == first.finished_at
    sender.assert_called_once()


def test_removed_destination_prevents_transport_and_attempt(event, sender, settings):
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(ValidationError):
        deliver_event(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize('exception', [RuntimeError('Falha simulada.'), KeyboardInterrupt()])
def test_interruption_keeps_attempt_unfinished_and_prevents_resend(event, sender, exception):
    sender.side_effect = exception

    with pytest.raises(type(exception)):
        deliver_event(event.pk)

    event.refresh_from_db()
    attempt = DeliveryAttempt.objects.get(event=event)
    assert event.status == Event.Status.SENDING
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert attempt.error_code == ''
    with pytest.raises(DeliveryAlreadyStarted):
        deliver_event(event.pk)
    sender.assert_called_once()


def test_failure_to_save_final_status_rolls_back_result_and_prevents_resend(
    event, sender, monkeypatch,
):
    save = Mock(side_effect=IntegrityError('Falha de gravação simulada.'))
    monkeypatch.setattr(Event, 'save', save)

    with pytest.raises(IntegrityError):
        deliver_event(event.pk)

    event.refresh_from_db()
    attempt = DeliveryAttempt.objects.get(event=event)
    assert event.status == Event.Status.SENDING
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert attempt.error_code == ''
    with pytest.raises(DeliveryAlreadyStarted):
        deliver_event(event.pk)
    sender.assert_called_once()


def test_policy_change_after_http_does_not_erase_observed_result(event, sender, settings):
    def send(url, data):
        settings.WEBHOOK_ALLOWED_URLS = []
        return DeliveryResult(204, '')

    sender.side_effect = send
    attempt = deliver_event(event.pk)
    attempt.refresh_from_db()
    event.refresh_from_db()

    assert attempt.http_status == 204
    assert attempt.finished_at is not None
    assert event.status == Event.Status.DELIVERED


def test_reservation_requires_transaction_before_changing_event(event, sender):
    with pytest.raises(RuntimeError):
        _reserve_delivery(event)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_creation_and_reservation_can_roll_back_together(event, sender):
    with pytest.raises(IntegrityError), transaction.atomic(durable=True):
        created = Event.objects.create(
            endpoint=event.endpoint, event_type=event.event_type, payload=event.payload,
        )
        attempt = _reserve_delivery(created)
        assert attempt.event_id == created.pk
        assert created.status == Event.Status.SENDING
        raise IntegrityError('Falha simulada após reservar a entrega.')

    assert Event.objects.filter(pk=event.pk).exists()
    assert not Event.objects.filter(pk=created.pk).exists()
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_transport_phase_refuses_open_transaction(event, sender):
    attempt = _start_delivery(event.pk)
    with transaction.atomic():
        with pytest.raises(RuntimeError):
            _send_and_finish_delivery(attempt)

    attempt.refresh_from_db()
    event.refresh_from_db()
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert event.status == Event.Status.SENDING
    sender.assert_not_called()
