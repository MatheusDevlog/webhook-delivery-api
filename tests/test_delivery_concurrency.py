from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.db import connection, connections

from webhooks.delivery import DeliveryResult
from webhooks.limits import DeliveryLimitError
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import DeliveryAlreadyStarted, _start_delivery, deliver_event


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize('send_event', [False, True])
def test_concurrent_starts_create_only_one_attempt(settings, monkeypatch, send_event):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 20
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    event = Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
    )
    ready = Barrier(2, timeout=10)
    full_clean = Event.full_clean
    sender = Mock(return_value=DeliveryResult(204, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)
    operation = deliver_event if send_event else _start_delivery

    def validate_together(instance, *args, **kwargs):
        full_clean(instance, *args, **kwargs)
        ready.wait()

    monkeypatch.setattr(Event, 'full_clean', validate_together)

    def start():
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 10000')
                cursor.execute('SELECT pg_backend_pid()')
                session_id = cursor.fetchone()[0]
            try:
                attempt = operation(event.pk)
                return 'started', attempt.pk, session_id
            except DeliveryAlreadyStarted:
                return 'already_started', None, session_id
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(result[0] for result in results) == ['already_started', 'started']
    assert len({result[2] for result in results}) == 2
    attempt = DeliveryAttempt.objects.get(event=event)
    assert attempt.pk == next(result[1] for result in results if result[0] == 'started')
    assert (attempt.finished_at is not None) == send_event
    assert sender.call_count == int(send_event)
    event.refresh_from_db()
    assert event.status == (Event.Status.DELIVERED if send_event else Event.Status.SENDING)


@pytest.mark.parametrize('budget', ['global', 'operator'])
def test_concurrent_events_cannot_take_the_same_last_budget_slot(
    settings, monkeypatch, django_user_model, budget,
):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 1 if budget == 'global' else 20
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 1 if budget == 'operator' else 3
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    first = django_user_model.objects.create_user(username='first', is_staff=True)
    second = django_user_model.objects.create_user(username='second', is_staff=True)
    actors = [first, second if budget == 'global' else first]
    events = [
        Event.objects.create(endpoint=endpoint, event_type='pedido.pago', payload={})
        for _ in range(2)
    ]
    ready = Barrier(2, timeout=10)
    full_clean = Event.full_clean
    sender = Mock(return_value=DeliveryResult(204, ''))
    monkeypatch.setattr('webhooks.services.post_json', sender)

    def validate_together(instance, *args, **kwargs):
        full_clean(instance, *args, **kwargs)
        ready.wait()

    monkeypatch.setattr(Event, 'full_clean', validate_together)

    def send(event_id, actor):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 10000')
            try:
                deliver_event(event_id, initiated_by=actor)
                return 'sent'
            except DeliveryLimitError as error:
                return error.code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send, event.pk, actor) for event, actor in zip(events, actors)]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(results) == [f'{budget}_limit', 'sent']
    assert DeliveryAttempt.objects.count() == 1
    assert sorted(Event.objects.values_list('status', flat=True)) == ['delivered', 'pending']
    sender.assert_called_once()
