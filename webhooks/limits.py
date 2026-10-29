from datetime import timedelta
from math import ceil

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, connection
from django.utils import timezone

from .models import DeliveryAttempt


DELIVERY_BUDGET_LOCK = (0x57484453, 1)
LOCK_TIMEOUT_MS = 1000


class DeliveryLimitError(Exception):
    def __init__(self, code, message, retry_after=None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(message)


def _check_window(attempts, now, window, limit, code, message):
    recent = attempts.filter(started_at__gt=now - window)
    count = recent.count()
    if count >= limit:
        next_slot = recent.order_by('started_at').values_list('started_at', flat=True)[
            count - limit
        ]
        wait = max(1, ceil((next_slot + window - now).total_seconds()))
        raise DeliveryLimitError(code, message, retry_after=wait)


def check_delivery_limits(initiated_by=None):
    per_minute = settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE
    per_day = settings.WEBHOOK_MAX_DELIVERIES_PER_DAY
    if any(type(value) is not int or value < 0 for value in (per_minute, per_day)):
        raise ImproperlyConfigured('Os limites de envio devem ser inteiros não negativos.')
    if not per_minute or not per_day:
        raise DeliveryLimitError('disabled', 'O envio de webhooks está desabilitado.')
    if not connection.in_atomic_block:
        raise RuntimeError('O orçamento deve ser verificado dentro da transação de início.')

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('lock_timeout', %s, true)", [f'{LOCK_TIMEOUT_MS}ms'],
            )
            cursor.execute('SELECT pg_advisory_xact_lock(%s, %s)', DELIVERY_BUDGET_LOCK)
    except OperationalError as error:
        if getattr(error.__cause__, 'sqlstate', None) == '55P03':
            raise DeliveryLimitError(
                'busy', 'Não foi possível reservar o envio agora.', retry_after=1,
            ) from None
        raise

    now = timezone.now()
    attempts = DeliveryAttempt.objects.all()
    _check_window(
        attempts, now, timedelta(hours=24), per_day, 'global_limit',
        'O orçamento de envios das últimas 24 horas foi atingido.',
    )
    _check_window(
        attempts.filter(initiated_by=initiated_by), now, timedelta(minutes=1),
        per_minute, 'operator_limit', 'O limite de envios por minuto foi atingido.',
    )
