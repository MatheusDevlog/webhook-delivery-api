from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from webhooks.models import DeliveryAttempt, Event


RETENTION_DAYS = 7
BATCH_SIZE = 100


def _eligible_events(cutoff):
    return Event.objects.filter(created_at__lt=cutoff).filter(
        Q(status=Event.Status.PENDING, delivery_attempt__isnull=True)
        | Q(
            status__in=[Event.Status.DELIVERED, Event.Status.FAILED],
            delivery_attempt__started_at__lt=cutoff,
            delivery_attempt__finished_at__lt=cutoff,
        )
    )


class Command(BaseCommand):
    help = 'Simula a limpeza de dados de webhook com mais de sete dias.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Exclui os registros elegíveis; sem esta opção, apenas simula.',
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        eligible = _eligible_events(cutoff)
        if not options['apply']:
            event_count = eligible.count()
            attempt_count = DeliveryAttempt.objects.filter(event__in=eligible).count()
            self.stdout.write(
                f'Simulação: eventos elegíveis = {event_count}; tentativas elegíveis = {attempt_count}. '
                'Nenhum registro excluído.'
            )
            return

        event_count = attempt_count = 0
        try:
            while True:
                with transaction.atomic(durable=True):
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT set_config('lock_timeout', '1000ms', true)")
                    ids = list(
                        eligible.select_for_update(of=('self',), skip_locked=True)
                        .order_by('created_at', 'pk').values_list('pk', flat=True)[:BATCH_SIZE]
                    )
                    if not ids:
                        break
                    ids = list(_eligible_events(cutoff).filter(pk__in=ids).values_list('pk', flat=True))
                    _, attempts_deleted = DeliveryAttempt.objects.filter(event_id__in=ids).delete()
                    _, events_deleted = Event.objects.filter(pk__in=ids).delete()
                attempt_count += attempts_deleted.get(DeliveryAttempt._meta.label, 0)
                event_count += events_deleted.get(Event._meta.label, 0)
        except DatabaseError:
            raise CommandError(
                'Limpeza interrompida; o lote atual foi desfeito. '
                f'Já excluídos: eventos = {event_count}; tentativas = {attempt_count}.'
            ) from None

        self.stdout.write(
            f'Exclusão concluída: eventos = {event_count}; tentativas = {attempt_count}. '
            'Registros ocupados ficam para uma próxima execução.'
        )
