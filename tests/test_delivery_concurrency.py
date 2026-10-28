from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection, connections

from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import DeliveryAlreadyStarted, _start_delivery


pytestmark = pytest.mark.django_db(transaction=True)


def test_concurrent_starts_create_only_one_attempt(settings, monkeypatch):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    endpoint = WebhookEndpoint.objects.create(
        url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago',
    )
    event = Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
    )
    ready = Barrier(2, timeout=10)
    full_clean = Event.full_clean

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
                attempt = _start_delivery(event.pk)
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
    assert attempt.finished_at is None
    event.refresh_from_db()
    assert event.status == Event.Status.SENDING
