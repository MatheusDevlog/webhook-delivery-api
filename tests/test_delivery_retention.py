from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connections, transaction
from django.db.models.query import QuerySet

from webhooks.limits import DeliveryLimitError
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint
from webhooks.services import _start_delivery


pytestmark = pytest.mark.django_db(transaction=True)
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr('webhooks.management.commands.purge_webhook_data.timezone.now', lambda: NOW)


@pytest.fixture
def operator(django_user_model):
    return django_user_model.objects.create_user(username='operator', is_staff=True)


@pytest.fixture
def endpoint(settings):
    settings.WEBHOOK_ALLOWED_URLS = ['https://receiver.example/webhook']
    return WebhookEndpoint.objects.create(url=settings.WEBHOOK_ALLOWED_URLS[0], event_type='pedido.pago')


@pytest.fixture
def make_record(endpoint, operator):
    def create(status='pending', event_age=8, attempt_age=None, finish_age=None):
        event = Event.objects.create(endpoint=endpoint, event_type='pedido.pago', payload={}, status=status)
        Event.objects.filter(pk=event.pk).update(created_at=NOW - timedelta(days=event_age))
        attempt = None
        if attempt_age is not None:
            http_status = None if finish_age is None else (204 if status == 'delivered' else 500)
            attempt = DeliveryAttempt.objects.create(
                event=event, initiated_by=operator,
                started_at=NOW - timedelta(days=attempt_age),
                finished_at=None if finish_age is None else NOW - timedelta(days=finish_age),
                http_status=http_status, error_code='http_error' if http_status == 500 else '',
            )
        return event, attempt

    return create


def test_default_command_only_previews_eligible_records(make_record):
    make_record()
    make_record('delivered', 10, 9, 8)
    make_record('sending', 10, 9)
    event_ids = set(Event.objects.values_list('pk', flat=True))
    attempt_ids = set(DeliveryAttempt.objects.values_list('pk', flat=True))
    output = StringIO()

    call_command('purge_webhook_data', stdout=output)

    assert 'eventos elegíveis = 2; tentativas elegíveis = 1' in output.getvalue()
    assert 'Nenhum registro excluído.' in output.getvalue()
    assert set(Event.objects.values_list('pk', flat=True)) == event_ids
    assert set(DeliveryAttempt.objects.values_list('pk', flat=True)) == attempt_ids


@pytest.mark.parametrize(('status', 'event_age', 'attempt_age', 'finish_age', 'deleted'), [
    ('pending', 8, None, None, True), ('pending', 7, None, None, False),
    ('pending', 1, None, None, False),
    ('delivered', 10, 9, 8, True), ('failed', 10, 9, 8, True),
    ('sending', 10, 9, None, False), ('pending', 10, 9, None, False),
    ('delivered', 10, 9, 1, False), ('failed', 10, 1, 1, False),
    ('delivered', 6, 5, 4, False), ('delivered', 10, 9, 7, False),
    ('sending', 10, 9, 8, False),
])
def test_apply_only_deletes_records_allowed_by_retention_policy(
    make_record, endpoint, operator, django_user_model,
    status, event_age, attempt_age, finish_age, deleted,
):
    event, attempt = make_record(status, event_age, attempt_age, finish_age)

    call_command('purge_webhook_data', '--apply', stdout=StringIO())

    assert Event.objects.filter(pk=event.pk).exists() == (not deleted)
    if attempt is not None:
        assert DeliveryAttempt.objects.filter(pk=attempt.pk).exists() == (not deleted)
    assert WebhookEndpoint.objects.filter(pk=endpoint.pk).exists()
    assert django_user_model.objects.filter(pk=operator.pk).exists()


@pytest.mark.parametrize('apply', [False, True])
def test_empty_database_is_handled_without_deletion_errors(apply):
    output = StringIO()
    call_command('purge_webhook_data', apply=apply, stdout=output)

    expected = 'eventos = 0; tentativas = 0' if apply else 'eventos elegíveis = 0; tentativas elegíveis = 0'
    assert expected in output.getvalue()


def test_cleanup_processes_more_than_one_batch(make_record, monkeypatch):
    monkeypatch.setattr('webhooks.management.commands.purge_webhook_data.BATCH_SIZE', 2)
    for _ in range(5):
        make_record('delivered', 10, 9, 8)
    recent, _ = make_record(event_age=1)
    output = StringIO()

    call_command('purge_webhook_data', '--apply', stdout=output)

    assert list(Event.objects.values_list('pk', flat=True)) == [recent.pk]
    assert not DeliveryAttempt.objects.exists()
    assert 'Exclusão concluída: eventos = 5; tentativas = 5.' in output.getvalue()


def test_failed_event_deletion_restores_attempt_in_same_batch(make_record, monkeypatch):
    event, attempt = make_record('delivered', 10, 9, 8)
    delete = QuerySet.delete

    def fail_event_deletion(queryset):
        if queryset.model is Event:
            raise IntegrityError('INTERNAL_SECRET')
        return delete(queryset)

    monkeypatch.setattr(QuerySet, 'delete', fail_event_deletion)
    with pytest.raises(CommandError) as error:
        call_command('purge_webhook_data', '--apply', stdout=StringIO())

    assert 'lote atual foi desfeito' in str(error.value)
    assert 'INTERNAL_SECRET' not in str(error.value)
    assert Event.objects.filter(pk=event.pk).exists()
    assert DeliveryAttempt.objects.filter(pk=attempt.pk).exists()


def test_later_batch_failure_preserves_previous_commits_and_reports_counts(make_record, monkeypatch):
    first, first_attempt = make_record('delivered', 12, 11, 10)
    second, second_attempt = make_record('delivered', 11, 10, 9)
    monkeypatch.setattr('webhooks.management.commands.purge_webhook_data.BATCH_SIZE', 1)
    delete = QuerySet.delete

    def fail_second_event_deletion(queryset):
        if queryset.model is Event and queryset.filter(pk=second.pk).exists():
            raise IntegrityError('Falha no segundo lote.')
        return delete(queryset)

    monkeypatch.setattr(QuerySet, 'delete', fail_second_event_deletion)
    with pytest.raises(CommandError) as error:
        call_command('purge_webhook_data', '--apply', stdout=StringIO())

    assert 'Já excluídos: eventos = 1; tentativas = 1.' in str(error.value)
    assert not Event.objects.filter(pk=first.pk).exists()
    assert not DeliveryAttempt.objects.filter(pk=first_attempt.pk).exists()
    assert Event.objects.filter(pk=second.pk).exists()
    assert DeliveryAttempt.objects.filter(pk=second_attempt.pk).exists()


def test_recent_attempt_on_old_event_still_consumes_budget_after_cleanup(
    make_record, operator, settings,
):
    make_record('delivered', 10, 0.5, 0.4)
    fresh, _ = make_record(event_age=0)
    settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE = 3
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 1

    call_command('purge_webhook_data', '--apply', stdout=StringIO())

    with pytest.raises(DeliveryLimitError) as error:
        _start_delivery(fresh.pk, initiated_by=operator)
    assert error.value.code == 'global_limit'
    assert DeliveryAttempt.objects.count() == 1
    fresh.refresh_from_db()
    assert fresh.status == Event.Status.PENDING


def test_locked_eligible_record_is_left_for_next_cleanup(make_record):
    locked, _ = make_record()
    free, _ = make_record()

    def clean():
        try:
            output = StringIO()
            call_command('purge_webhook_data', '--apply', stdout=output)
            return output.getvalue()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Event.objects.select_for_update().get(pk=locked.pk)
            output = pool.submit(clean).result(timeout=5)

    assert 'Exclusão concluída: eventos = 1; tentativas = 0.' in output
    assert Event.objects.filter(pk=locked.pk).exists()
    assert not Event.objects.filter(pk=free.pk).exists()
    call_command('purge_webhook_data', '--apply', stdout=StringIO())
    assert not Event.objects.exists()


def test_cleanup_does_not_delete_event_while_attempt_is_starting(make_record, operator):
    event, _ = make_record()

    def clean():
        try:
            call_command('purge_webhook_data', '--apply', stdout=StringIO())
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Event.objects.filter(pk=event.pk).update(status=Event.Status.SENDING)
            attempt = DeliveryAttempt.objects.create(event=event, initiated_by=operator, started_at=NOW)
            pool.submit(clean).result(timeout=5)

    call_command('purge_webhook_data', '--apply', stdout=StringIO())
    event.refresh_from_db()
    assert event.status == Event.Status.SENDING
    assert DeliveryAttempt.objects.get(event=event).pk == attempt.pk
