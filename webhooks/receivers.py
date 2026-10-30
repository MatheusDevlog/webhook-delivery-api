from collections.abc import Mapping
from io import BytesIO

from django.conf import settings
from django.http import Http404
from rest_framework import serializers
from rest_framework.exceptions import APIException, ParseError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import APIView

from .validators import MAX_EVENT_PAYLOAD_BYTES


MAX_DEMO_BODY_BYTES = MAX_EVENT_PAYLOAD_BYTES + 1024


class DemoBodyTooLarge(APIException):
    status_code = 413
    default_detail = 'O corpo não pode exceder 17 KiB.'
    default_code = 'body_too_large'


class DemoJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        body = stream.read(MAX_DEMO_BODY_BYTES + 1)
        if len(body) > MAX_DEMO_BODY_BYTES:
            raise DemoBodyTooLarge()
        context = {**(parser_context or {}), 'encoding': 'utf-8'}
        try:
            return super().parse(BytesIO(body), media_type, context)
        except (ParseError, RecursionError):
            raise ParseError('Envie um objeto JSON válido em UTF-8.') from None


class DemoEnvelopeSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    event_type = serializers.ChoiceField(
        choices=['pedido.pago'],
        error_messages={'invalid_choice': 'Use somente o tipo pedido.pago.'},
    )
    created_at = serializers.DateTimeField()
    payload = serializers.JSONField()

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError({
                api_settings.NON_FIELD_ERRORS_KEY: ['O envelope contém campos não permitidos.'],
            })
        return super().to_internal_value(data)

    def validate_payload(self, value):
        if (
            not isinstance(value, dict)
            or set(value) != {'pedido_id'}
            or type(value['pedido_id']) is not str
            or value['pedido_id'] != 'PEDIDO-001'
        ):
            raise serializers.ValidationError('Use somente o exemplo fictício: pedido_id igual a PEDIDO-001.')
        return value


class DemoReceiver(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [DemoJSONParser]
    renderer_classes = [JSONRenderer]
    http_method_names = ['post']
    response_status = 200
    result = 'received'

    def initial(self, request, *args, **kwargs):
        if not settings.WEBHOOK_DEMO_RECEIVER_ENABLED:
            raise Http404('Receptor indisponível.')
        super().initial(request, *args, **kwargs)

    def post(self, request):
        serializer = DemoEnvelopeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({
            'status': self.result, 'event_id': str(serializer.validated_data['id']),
        }, status=self.response_status, headers={'Cache-Control': 'no-store'})
