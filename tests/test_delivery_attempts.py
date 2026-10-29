import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


pytestmark = pytest.mark.django_db
STARTED_AT = datetime(2026, 10, 26, 12, tzinfo=UTC)


@pytest.fixture
def event(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    event = Event(endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123})
    event.full_clean()
    event.save()
    return event


@pytest.mark.parametrize(('http_status', 'error_code'), [
    (200, ''), (204, ''), (299, ''),
    (302, 'http_error'), (400, 'http_error'), (500, 'http_error'), (599, 'http_error'),
    (None, 'network_error'), (None, 'timeout'),
    (None, 'destination_rejected'),
])
def test_persists_completed_attempt_results(event, http_status, error_code):
    attempt = DeliveryAttempt(
        event=event, started_at=STARTED_AT,
        finished_at=STARTED_AT + timedelta(seconds=1),
        http_status=http_status, error_code=error_code,
    )
    attempt.full_clean()
    attempt.save()

    saved = DeliveryAttempt.objects.get(pk=attempt.pk)
    assert isinstance(saved.pk, uuid.UUID)
    assert saved.event_id == event.pk
    assert saved.started_at == STARTED_AT
    assert saved.finished_at == STARTED_AT + timedelta(seconds=1)
    assert saved.http_status == http_status
    assert saved.error_code == error_code
    assert event.delivery_attempt.pk == saved.pk
    event.refresh_from_db()
    assert event.status == 'pending'


def test_starts_attempt_without_inventing_a_result(event):
    before = timezone.now()
    attempt = DeliveryAttempt(event=event)
    attempt.full_clean()
    attempt.save()
    attempt.refresh_from_db()

    assert before <= attempt.started_at <= timezone.now()
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert attempt.error_code == ''


def test_completes_the_existing_attempt_without_creating_another(event):
    attempt = DeliveryAttempt.objects.create(event=event, started_at=STARTED_AT)
    attempt.finished_at = STARTED_AT
    attempt.http_status = 200
    attempt.full_clean()
    attempt.save(update_fields=['finished_at', 'http_status'])
    attempt.refresh_from_db()

    assert attempt.finished_at == STARTED_AT
    assert attempt.http_status == 200
    assert DeliveryAttempt.objects.filter(event=event).count() == 1


@pytest.mark.parametrize(('finished', 'http_status', 'error_code'), [
    (False, 200, ''), (False, None, 'timeout'), (False, 500, 'http_error'),
    (True, None, ''), (True, None, 'http_error'),
    (True, 200, 'http_error'), (True, 500, ''), (True, 302, ''),
    (True, 500, 'network_error'), (True, 200, 'timeout'),
    (True, None, 'unexpected_error'), (True, 199, ''), (True, 600, 'http_error'),
    (False, None, 'destination_rejected'),
    (True, 200, 'destination_rejected'), (True, 500, 'destination_rejected'),
])
def test_rejects_inconsistent_results_in_validation_and_database(
    event, finished, http_status, error_code,
):
    values = {
        'event': event,
        'started_at': STARTED_AT,
        'finished_at': STARTED_AT + timedelta(seconds=1) if finished else None,
        'http_status': http_status,
        'error_code': error_code,
    }
    with pytest.raises(ValidationError):
        DeliveryAttempt(**values).full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAttempt.objects.create(**values)

    assert not DeliveryAttempt.objects.exists()


def test_rejects_finish_before_start_in_validation_and_database(event):
    values = {
        'event': event, 'started_at': STARTED_AT,
        'finished_at': STARTED_AT - timedelta(seconds=1), 'http_status': 200,
    }
    with pytest.raises(ValidationError):
        DeliveryAttempt(**values).full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAttempt.objects.create(**values)

    assert not DeliveryAttempt.objects.exists()


def test_rejects_second_attempt_for_the_same_event(event):
    first = DeliveryAttempt.objects.create(event=event)

    with pytest.raises(ValidationError):
        DeliveryAttempt(event=event).full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAttempt.objects.create(event=event)

    assert DeliveryAttempt.objects.get(event=event).pk == first.pk


def test_attempt_requires_an_existing_event():
    event_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        DeliveryAttempt(event_id=event_id).full_clean()

    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAttempt.objects.create(event_id=event_id)
        connection.check_constraints()

    assert not DeliveryAttempt.objects.exists()


def test_event_with_attempt_is_protected_from_deletion(event):
    attempt = DeliveryAttempt.objects.create(event=event)

    with pytest.raises(ProtectedError):
        event.delete()

    assert Event.objects.filter(pk=event.pk).exists()
    assert DeliveryAttempt.objects.filter(pk=attempt.pk).exists()


def test_attempt_can_remain_without_operator_for_existing_records(event):
    attempt = DeliveryAttempt(event=event)
    attempt.full_clean()
    attempt.save()
    attempt.refresh_from_db()

    assert attempt.initiated_by_id is None
    assert attempt.event_id == event.pk


def test_persists_operator_who_initiated_attempt(event, django_user_model):
    user = django_user_model.objects.create_user(username='sender', is_staff=True)
    attempt = DeliveryAttempt(event=event, initiated_by=user)
    attempt.full_clean()
    attempt.save()
    attempt.refresh_from_db()

    assert attempt.initiated_by_id == user.pk
    assert user.webhook_delivery_attempts.get().pk == attempt.pk


def test_deleting_operator_preserves_attempt_and_event(event, django_user_model):
    user = django_user_model.objects.create_user(username='sender', is_staff=True)
    attempt = DeliveryAttempt.objects.create(event=event, initiated_by=user)
    started_at = attempt.started_at

    user.delete()
    attempt.refresh_from_db()

    assert attempt.initiated_by_id is None
    assert attempt.event_id == event.pk
    assert attempt.started_at == started_at
    assert Event.objects.filter(pk=event.pk).exists()


def test_attempt_operator_must_reference_existing_user(event, django_user_model):
    user = django_user_model.objects.create_user(username='removed')
    user_id = user.pk
    user.delete()
    values = {'event': event, 'initiated_by_id': user_id}

    with pytest.raises(ValidationError) as error:
        DeliveryAttempt(**values).full_clean()

    assert 'initiated_by' in error.value.message_dict
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryAttempt.objects.create(**values)
        connection.check_constraints()

    assert not DeliveryAttempt.objects.exists()
