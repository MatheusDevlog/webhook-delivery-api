from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction

from webhooks.delivery import DeliveryResult
from webhooks.demo import DemoConfigurationError, deliver_demo
from webhooks.limits import DeliveryLimitError
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def demo(settings):
    settings.WEBHOOK_DEMO_ENABLED = True
    settings.WEBHOOK_DEMO_RECEIVER_ENABLED = True
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 20
    settings.WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY = 4
    endpoints = {
        scenario: WebhookEndpoint.objects.create(
            url=f'https://receiver.example/demonstracao/receptor/{path}/', event_type='pedido.pago',
        )
        for scenario, path in [('success', 'sucesso'), ('failure', 'falha')]
    }
    settings.WEBHOOK_ALLOWED_URLS = [endpoint.url for endpoint in endpoints.values()]
    settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID = str(endpoints['success'].pk)
    settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID = str(endpoints['failure'].pk)
    return endpoints


@pytest.fixture(autouse=True)
def sender(monkeypatch):
    sender = Mock(return_value=DeliveryResult(200, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    return sender


def assert_no_start(sender):
    assert not Event.objects.exists()
    assert not DeliveryAttempt.objects.exists()
    sender.assert_not_called()


@pytest.mark.parametrize(('scenario', 'result', 'state'), [
    ('success', DeliveryResult(200, ''), 'delivered'),
    ('failure', DeliveryResult(500, 'http_error'), 'failed'),
    ('success', DeliveryResult(None, 'timeout'), 'failed'),
    ('success', DeliveryResult(None, 'network_error'), 'failed'),
    ('success', ValidationError('Bloqueado.', code='unapproved_destination'), 'failed'),
])
def test_demo_creates_fixed_event_and_records_transport_result(demo, sender, scenario, result, state):
    visitor_id = uuid4()

    def send(url, data):
        assert not connection.in_atomic_block
        assert connection.get_autocommit()
        event = Event.objects.get(pk=data['id'])
        assert event.status == Event.Status.SENDING
        assert event.delivery_attempt.finished_at is None
        assert url == demo[scenario].url
        assert data == {
            'id': str(event.pk), 'event_type': 'pedido.pago',
            'created_at': event.created_at.isoformat(), 'payload': {'pedido_id': 123},
        }
        if isinstance(result, Exception):
            raise result
        return result

    sender.side_effect = send
    attempt = deliver_demo(scenario, visitor_id)
    attempt.refresh_from_db()
    event = attempt.event

    assert isinstance(event.pk, UUID)
    assert event.status == state
    assert event.endpoint_id == demo[scenario].pk
    assert event.payload == {'pedido_id': 123}
    assert attempt.visitor_id == visitor_id
    assert attempt.initiated_by_id is None
    assert attempt.finished_at >= attempt.started_at
    assert attempt.http_status == (result.http_status if isinstance(result, DeliveryResult) else None)
    assert attempt.error_code == (result.error_code if isinstance(result, DeliveryResult) else 'destination_rejected')
    assert Event.objects.count() == DeliveryAttempt.objects.count() == 1
    sender.assert_called_once()


@pytest.mark.parametrize('flag', ['WEBHOOK_DEMO_ENABLED', 'WEBHOOK_DEMO_RECEIVER_ENABLED'])
def test_disabled_demo_does_not_create_records_or_send(settings, sender, flag):
    setattr(settings, flag, False)

    with pytest.raises(DeliveryLimitError) as error:
        deliver_demo('success', uuid4())

    assert error.value.code == 'disabled'
    assert_no_start(sender)


@pytest.mark.parametrize('scenario', ['unknown', '', None, {'url': 'https://unapproved.example/'}])
def test_demo_rejects_invalid_scenario_before_creating_records(sender, scenario):
    with pytest.raises(ValidationError) as error:
        deliver_demo(scenario, uuid4())

    assert 'scenario' in error.value.message_dict
    assert_no_start(sender)


@pytest.mark.parametrize(('visitor_id', 'event_id'), [
    (None, None), ('not-a-uuid', None), (UUID(int=1), 'not-a-uuid'),
])
def test_demo_requires_typed_internal_identifiers(sender, visitor_id, event_id):
    with pytest.raises(ValueError):
        deliver_demo('success', visitor_id, event_id=event_id)

    assert_no_start(sender)


@pytest.mark.parametrize(('name', 'value'), [
    ('WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID', 'not-a-uuid'),
    ('WEBHOOK_DEMO_FAILURE_ENDPOINT_ID', ''),
])
def test_invalid_endpoint_configuration_is_rejected_without_leaking_value(settings, sender, name, value):
    setattr(settings, name, value)

    with pytest.raises(DemoConfigurationError) as error:
        deliver_demo('success', uuid4())

    assert 'not-a-uuid' not in str(error.value)
    assert_no_start(sender)


def test_demo_requires_distinct_destinations(settings, sender):
    settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID = settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID

    with pytest.raises(DemoConfigurationError):
        deliver_demo('success', uuid4())

    assert_no_start(sender)


@pytest.mark.parametrize('scenario', ['success', 'failure'])
def test_demo_rejects_missing_destination(demo, sender, scenario):
    demo[scenario].delete()

    with pytest.raises(DemoConfigurationError):
        deliver_demo(scenario, uuid4())

    assert_no_start(sender)


@pytest.mark.parametrize(('field', 'value'), [
    ('url', 'https://receiver.example/other/'), ('event_type', 'pedido.cancelado'),
])
def test_demo_only_uses_its_receiver_path_and_fixed_event_type(demo, settings, sender, field, value):
    WebhookEndpoint.objects.filter(pk=demo['success'].pk).update(**{field: value})
    if field == 'url':
        settings.WEBHOOK_ALLOWED_URLS = [value, demo['failure'].url]

    with pytest.raises(DemoConfigurationError):
        deliver_demo('success', uuid4())

    assert_no_start(sender)


def test_demo_revalidates_current_destination_authorization(settings, sender):
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(DemoConfigurationError):
        deliver_demo('success', uuid4())

    assert_no_start(sender)


@pytest.mark.parametrize('name', [
    'WEBHOOK_MAX_DELIVERIES_PER_DAY', 'WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY',
])
def test_disabled_budget_rolls_back_new_demo_event(settings, sender, name):
    setattr(settings, name, 0)

    with pytest.raises(DeliveryLimitError) as error:
        deliver_demo('success', uuid4())

    assert error.value.code == 'disabled'
    assert_no_start(sender)


@pytest.mark.parametrize(('name', 'code'), [
    ('WEBHOOK_MAX_DELIVERIES_PER_MINUTE', 'visitor_minute_limit'),
    ('WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY', 'visitor_day_limit'),
    ('WEBHOOK_MAX_DELIVERIES_PER_DAY', 'global_limit'),
])
def test_exhausted_quota_does_not_accumulate_pending_demo_events(settings, sender, name, code):
    visitor_id = uuid4()
    first = deliver_demo('success', visitor_id)
    setattr(settings, name, 1)
    next_visitor = uuid4() if code == 'global_limit' else visitor_id

    with pytest.raises(DeliveryLimitError) as error:
        deliver_demo('failure', next_visitor)

    assert error.value.code == code
    assert Event.objects.get().pk == first.event_id
    assert DeliveryAttempt.objects.get().pk == first.pk
    sender.assert_called_once()


def test_failed_attempt_insert_rolls_back_new_event(sender, monkeypatch):
    monkeypatch.setattr(DeliveryAttempt.objects, 'create', Mock(side_effect=IntegrityError()))

    with pytest.raises(IntegrityError):
        deliver_demo('success', uuid4())

    assert_no_start(sender)


def test_demo_refuses_nested_transaction(sender):
    with transaction.atomic():
        with pytest.raises(RuntimeError):
            deliver_demo('success', uuid4())

    assert_no_start(sender)


@pytest.mark.parametrize('stage', ['transport', 'finalization'])
def test_unexpected_failure_keeps_reserved_event_and_incomplete_attempt(sender, monkeypatch, stage):
    event_id = uuid4()
    visitor_id = uuid4()
    if stage == 'transport':
        sender.side_effect = RuntimeError('Interrupção simulada.')
        expected_error = RuntimeError
    else:
        def send(url, data):
            monkeypatch.setattr(Event, 'save', Mock(side_effect=IntegrityError()))
            return DeliveryResult(200, '')
        sender.side_effect = send
        expected_error = IntegrityError

    with pytest.raises(expected_error):
        deliver_demo('success', visitor_id, event_id=event_id)

    event = Event.objects.get(pk=event_id)
    attempt = DeliveryAttempt.objects.get(event=event)
    assert event.status == Event.Status.SENDING
    assert attempt.visitor_id == visitor_id
    assert attempt.finished_at is None
    assert attempt.http_status is None
    assert attempt.error_code == ''
    sender.assert_called_once()


def test_concurrent_demo_requests_cannot_leave_orphan_event_after_last_slot(
    settings, sender, monkeypatch,
):
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 1
    visitor_id = uuid4()
    ready = Barrier(2, timeout=10)
    full_clean = Event.full_clean

    def validate_together(instance, *args, **kwargs):
        full_clean(instance, *args, **kwargs)
        ready.wait()

    monkeypatch.setattr(Event, 'full_clean', validate_together)

    def send():
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 10000')
            try:
                deliver_demo('success', visitor_id)
                return 'sent'
            except DeliveryLimitError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(send) for _ in range(2)]]

    assert sorted(results) == ['sent', 'visitor_minute_limit']
    assert Event.objects.count() == DeliveryAttempt.objects.count() == 1
    assert Event.objects.get().status == Event.Status.DELIVERED
    assert DeliveryAttempt.objects.get().visitor_id == visitor_id
    sender.assert_called_once()
