from collections.abc import Mapping

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.settings import api_settings

from .models import DeliveryAttempt, Event, WebhookEndpoint


class EventSerializer(serializers.ModelSerializer):
    endpoint_id = serializers.PrimaryKeyRelatedField(
        source='endpoint',
        queryset=WebhookEndpoint.objects.all(),
        pk_field=serializers.UUIDField(),
        label='Destino',
    )
    payload = serializers.JSONField(required=True, label='Dados do evento')

    class Meta:
        model = Event
        fields = ('id', 'endpoint_id', 'event_type', 'payload', 'status', 'created_at')
        read_only_fields = ('id', 'status', 'created_at')

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            writable_fields = {
                name for name, field in self.fields.items() if not field.read_only
            }
            errors = {
                name: ['Este campo não é permitido no cadastro.']
                for name in data if name not in writable_fields
            }
            if errors:
                raise serializers.ValidationError(errors)
        return super().to_internal_value(data)

    def validate(self, attrs):
        event = Event(**attrs)
        try:
            event.full_clean()
        except DjangoValidationError as error:
            field_names = {
                'endpoint': 'endpoint_id',
                NON_FIELD_ERRORS: api_settings.NON_FIELD_ERRORS_KEY,
            }
            raise serializers.ValidationError({
                field_names.get(name, name): messages
                for name, messages in error.message_dict.items()
            }) from error
        return attrs


class DeliveryAttemptSerializer(serializers.ModelSerializer):
    event_id = serializers.UUIDField(read_only=True, label='Evento')
    event_status = serializers.CharField(
        source='event.status', read_only=True, label='Estado do evento',
    )

    class Meta:
        model = DeliveryAttempt
        fields = (
            'id', 'event_id', 'event_status', 'started_at', 'finished_at',
            'http_status', 'error_code',
        )
        read_only_fields = fields
