import uuid
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import DeliveryAlreadyStarted, _start_delivery


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def event(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    return Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
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
