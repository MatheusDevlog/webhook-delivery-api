from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, connections, transaction
from django.utils import timezone

from config.settings import _read_nonnegative_integer
from webhooks.delivery import DeliveryResult
from webhooks.limits import DELIVERY_BUDGET_LOCK, DeliveryLimitError, check_delivery_limits
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import _start_delivery, deliver_event


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def create_event(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 20
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )

    def create():
        return Event.objects.create(endpoint=endpoint, event_type='pedido.pago', payload={})

    return create


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user(username='sender', is_staff=True)


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    sender = Mock(return_value=DeliveryResult(204, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    return sender


@pytest.mark.parametrize('name', [
    'WEBHOOK_MAX_DELIVERIES_PER_MINUTE', 'WEBHOOK_MAX_DELIVERIES_PER_DAY',
])
def test_zero_limit_disables_sending_without_persisting_start(
    create_event, operator, settings, sender, name,
):
    event = create_event()
    setattr(settings, name, 0)

    with pytest.raises(DeliveryLimitError) as error:
        deliver_event(event.pk, initiated_by=operator)

    assert error.value.code == 'disabled'
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_operator_limit_preserves_pending_event_and_allows_other_operator(
    create_event, operator, django_user_model, sender,
):
    for _ in range(3):
        _start_delivery(create_event().pk, initiated_by=operator)
    blocked = create_event()

    with pytest.raises(DeliveryLimitError) as error:
        deliver_event(blocked.pk, initiated_by=operator)

    assert error.value.code == 'operator_limit'
    assert 1 <= error.value.retry_after <= 60
    blocked.refresh_from_db()
    assert blocked.status == Event.Status.PENDING
    other = django_user_model.objects.create_user(username='other', is_staff=True)
    attempt = _start_delivery(create_event().pk, initiated_by=other)
    assert attempt.initiated_by_id == other.pk
    assert DeliveryAttempt.objects.count() == 4
    sender.assert_not_called()


def test_global_budget_includes_attempts_without_operator(
    create_event, operator, settings, sender,
):
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 1
    _start_delivery(create_event().pk)
    blocked = create_event()

    with pytest.raises(DeliveryLimitError) as error:
        deliver_event(blocked.pk, initiated_by=operator)

    assert error.value.code == 'global_limit'
    assert 1 <= error.value.retry_after <= 86400
    blocked.refresh_from_db()
    assert blocked.status == Event.Status.PENDING
    assert DeliveryAttempt.objects.count() == 1
    sender.assert_not_called()


@pytest.mark.parametrize('result', [
    DeliveryResult(500, 'http_error'), DeliveryResult(None, 'timeout'),
])
def test_failed_attempt_still_consumes_global_budget(create_event, operator, settings, sender, result):
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 1
    sender.return_value = result
    first = deliver_event(create_event().pk, initiated_by=operator)

    with pytest.raises(DeliveryLimitError):
        deliver_event(create_event().pk, initiated_by=operator)

    assert first.error_code == result.error_code
    assert first.initiated_by_id == operator.pk
    assert DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()


@pytest.mark.parametrize(('name', 'age', 'blocked'), [
    ('WEBHOOK_MAX_DELIVERIES_PER_MINUTE', 59, True),
    ('WEBHOOK_MAX_DELIVERIES_PER_MINUTE', 60, False),
    ('WEBHOOK_MAX_DELIVERIES_PER_DAY', 86399, True),
    ('WEBHOOK_MAX_DELIVERIES_PER_DAY', 86400, False),
])
def test_window_boundary_releases_capacity(
    create_event, operator, settings, monkeypatch, name, age, blocked,
):
    setattr(settings, name, 1)
    now = timezone.now()
    monkeypatch.setattr('webhooks.limits.timezone.now', lambda: now)
    DeliveryAttempt.objects.create(
        event=create_event(), initiated_by=operator,
        started_at=now - timedelta(seconds=age),
    )
    event = create_event()

    if blocked:
        with pytest.raises(DeliveryLimitError) as error:
            _start_delivery(event.pk, initiated_by=operator)
        assert error.value.retry_after == 1
        assert DeliveryAttempt.objects.count() == 1
    else:
        attempt = _start_delivery(event.pk, initiated_by=operator)
        assert attempt.event_id == event.pk
        assert DeliveryAttempt.objects.count() == 2


@pytest.mark.parametrize('value', ['', '-1', '3.5', 'invalid'])
def test_rejects_invalid_limit_configuration(monkeypatch, value):
    monkeypatch.setenv('TEST_WEBHOOK_LIMIT', value)

    with pytest.raises(ImproperlyConfigured, match='TEST_WEBHOOK_LIMIT'):
        _read_nonnegative_integer('TEST_WEBHOOK_LIMIT', 0)


@pytest.mark.parametrize('default', [0, 3])
def test_uses_explicit_default_when_limit_variable_is_missing(monkeypatch, default):
    monkeypatch.delenv('TEST_WEBHOOK_LIMIT', raising=False)

    assert _read_nonnegative_integer('TEST_WEBHOOK_LIMIT', default) == default


@pytest.mark.parametrize('value', [-1, True, '20', 2.5])
def test_invalid_runtime_limit_does_not_allow_sending(create_event, settings, sender, value):
    event = create_event()
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = value

    with pytest.raises(ImproperlyConfigured):
        deliver_event(event.pk)

    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


def test_budget_cannot_be_checked_outside_start_transaction(create_event):
    with pytest.raises(RuntimeError):
        check_delivery_limits()


def test_busy_budget_has_bounded_wait_and_rolls_back_start(
    create_event, operator, monkeypatch, sender,
):
    event = create_event()
    monkeypatch.setattr('webhooks.limits.LOCK_TIMEOUT_MS', 100)

    def start():
        try:
            return _start_delivery(event.pk, initiated_by=operator)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', DELIVERY_BUDGET_LOCK)
            future = pool.submit(start)
            with pytest.raises(DeliveryLimitError) as error:
                future.result(timeout=5)

    assert error.value.code == 'busy'
    assert error.value.retry_after == 1
    event.refresh_from_db()
    assert event.status == Event.Status.PENDING
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()
