from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from .limits import DeliveryLimitError
from .models import Event, WebhookEndpoint
from .services import _reserve_delivery, _send_and_finish_delivery
from .validators import validate_webhook_url


SCENARIOS = {'success': 'sucesso', 'failure': 'falha'}


class DemoConfigurationError(Exception):
    pass


def _get_demo_endpoint(scenario):
    try:
        endpoint_ids = {
            'success': UUID(str(settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID)),
            'failure': UUID(str(settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID)),
        }
        if endpoint_ids['success'] == endpoint_ids['failure']:
            raise ValueError
        endpoint = WebhookEndpoint.objects.get(
            pk=endpoint_ids[scenario], event_type='pedido.pago',
        )
        validate_webhook_url(endpoint.url)
        if urlsplit(endpoint.url).path != f'/demonstracao/receptor/{SCENARIOS[scenario]}/':
            raise ValueError
    except (ValueError, ValidationError, WebhookEndpoint.DoesNotExist):
        raise DemoConfigurationError('Os destinos de demonstração não estão configurados corretamente.') from None
    return endpoint


def deliver_demo(scenario, visitor_id, *, event_id=None):
    if not settings.WEBHOOK_DEMO_ENABLED or not settings.WEBHOOK_DEMO_RECEIVER_ENABLED:
        raise DeliveryLimitError('disabled', 'A demonstração de envios está desabilitada.')
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise ValidationError({'scenario': 'Escolha somente o cenário de sucesso ou de falha.'})
    if not isinstance(visitor_id, UUID) or (
        event_id is not None and not isinstance(event_id, UUID)
    ):
        raise ValueError('Use identificadores UUID gerados pela aplicação.')

    with transaction.atomic(durable=True):
        endpoint = _get_demo_endpoint(scenario)
        event = Event(
            endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
        )
        if event_id is not None:
            event.id = event_id
        event.save(force_insert=True)
        attempt = _reserve_delivery(event, visitor_id=visitor_id)

    return _send_and_finish_delivery(attempt)
