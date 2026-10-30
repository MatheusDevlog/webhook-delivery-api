import logging
from io import BytesIO
from uuid import uuid4

from django.conf import settings
from django.http import Http404, JsonResponse
from django.middleware.csrf import get_token
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_safe
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

from .demo import DemoConfigurationError, deliver_demo
from .demo_access import get_visitor_attempt, get_visitor_id, prepare_visitor_response
from .limits import DeliveryLimitError
from .serializers import DeliveryAttemptSerializer, DemoRequestSerializer
from .services import InvalidEventForDelivery


logger = logging.getLogger(__name__)
MAX_DEMO_REQUEST_BYTES = 1024


def _json_response(data, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    patch_vary_headers(response, ['Cookie'])
    return response


@require_safe
def demo_home(request):
    limits = {
        'per_minute': settings.WEBHOOK_MAX_DELIVERIES_PER_MINUTE,
        'per_day': settings.WEBHOOK_DEMO_MAX_DELIVERIES_PER_DAY,
        'global_per_day': settings.WEBHOOK_MAX_DELIVERIES_PER_DAY,
    }
    enabled = (
        settings.WEBHOOK_DEMO_ENABLED and settings.WEBHOOK_DEMO_RECEIVER_ENABLED
        and all(value > 0 for value in limits.values())
        and settings.WEBHOOK_DEMO_SUCCESS_ENDPOINT_ID
        and settings.WEBHOOK_DEMO_FAILURE_ENDPOINT_ID
    )
    response = _json_response({
        'enabled': bool(enabled), 'event_type': 'pedido.pago', 'payload': {'pedido_id': 123},
        'scenarios': {'success': 'Sucesso', 'failure': 'Falha controlada'},
        'limits': limits, 'csrf_token': get_token(request),
    })
    return prepare_visitor_response(request, response)


@require_safe
def demo_detail(request, event_id):
    try:
        attempt = get_visitor_attempt(request, event_id)
    except Http404:
        return _json_response({
            'detail': 'Tentativa não encontrada.', 'code': 'not_found',
        }, status=404)
    return _json_response(DeliveryAttemptSerializer(attempt).data)


@require_POST
@csrf_protect
def demo_send(request):
    visitor_id = get_visitor_id(request)
    if visitor_id is None:
        return _json_response({
            'detail': 'Abra a página da demonstração antes de solicitar o envio.',
            'code': 'invalid_visitor',
        }, status=403)
    if request.content_type != 'application/json':
        return _json_response({
            'detail': 'Envie o corpo como application/json.', 'code': 'unsupported_media_type',
        }, status=415)
    body = request.read(MAX_DEMO_REQUEST_BYTES + 1)
    if len(body) > MAX_DEMO_REQUEST_BYTES:
        return _json_response({
            'detail': 'O corpo não pode exceder 1 KiB.', 'code': 'body_too_large',
        }, status=413)
    try:
        data = JSONParser().parse(BytesIO(body), parser_context={'encoding': 'utf-8'})
    except (ParseError, RecursionError):
        return _json_response({
            'detail': 'Envie um objeto JSON válido em UTF-8.', 'code': 'invalid_json',
        }, status=400)
    serializer = DemoRequestSerializer(data=data)
    if not serializer.is_valid():
        return _json_response(serializer.errors, status=400)

    event_id = uuid4()
    try:
        attempt = deliver_demo(
            serializer.validated_data['scenario'], visitor_id, event_id=event_id,
        )
        return _json_response(DeliveryAttemptSerializer(attempt).data)
    except (DemoConfigurationError, InvalidEventForDelivery):
        return _json_response({
            'detail': 'A demonstração não está disponível no momento.', 'code': 'demo_unavailable',
        }, status=503)
    except DeliveryLimitError as error:
        status = 503 if error.code in {'disabled', 'busy'} else 429
        response = _json_response({'detail': str(error), 'code': error.code}, status=status)
        if error.retry_after:
            response['Retry-After'] = str(error.retry_after)
        return response
    except Exception as error:
        logger.error('Falha interna na demonstração: %s', type(error).__name__)
        return _json_response({
            'detail': 'Não foi possível concluir a solicitação. Consulte o resultado.',
            'code': 'delivery_error', 'event_id': str(event_id),
        }, status=500)
