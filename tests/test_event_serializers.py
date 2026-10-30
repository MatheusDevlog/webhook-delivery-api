import uuid

import pytest

from webhooks.models import Event, WebhookEndpoint
from webhooks.serializers import EventSerializer


pytestmark = pytest.mark.django_db
ALLOWED_URL = 'https://receiver.example/webhook'


@pytest.fixture
def endpoint(settings):
    settings.WEBHOOK_ALLOWED_URLS = [ALLOWED_URL]
    return WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')


@pytest.fixture
def event_data(endpoint):
    return {
        'endpoint_id': str(endpoint.pk),
        'event_type': 'pedido.pago',
        'payload': {'pedido_id': 'PEDIDO-001', 'mensagem': 'Pagamento confirmado ✅'},
    }


def test_validated_input_resolves_destination_and_prepares_output(endpoint, event_data):
    serializer = EventSerializer(data=event_data)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data['endpoint'] == endpoint
    event = serializer.save()
    output = serializer.data
    assert output['id'] == str(event.pk)
    assert output['endpoint_id'] == str(endpoint.pk)
    assert output['event_type'] == event.event_type == 'pedido.pago'
    assert output['payload'] == event.payload == event_data['payload']
    assert output['status'] == event.status == 'pending'
    assert output['created_at']
    assert Event.objects.count() == 1


@pytest.mark.parametrize('field', ['endpoint_id', 'event_type', 'payload'])
def test_rejects_missing_required_field(event_data, field):
    event_data.pop(field)

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert field in serializer.errors
    assert not Event.objects.exists()


@pytest.mark.parametrize('field', ['id', 'status', 'created_at', 'endpoint', 'unexpected'])
def test_rejects_read_only_or_unknown_fields(event_data, field):
    event_data[field] = 'not-allowed'

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert field in serializer.errors
    assert not Event.objects.exists()


@pytest.mark.parametrize('endpoint_id', [None, '', 'not-a-uuid', str(uuid.uuid4())])
def test_rejects_invalid_or_missing_destination(event_data, endpoint_id):
    event_data['endpoint_id'] = endpoint_id

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert 'endpoint_id' in serializer.errors
    assert not Event.objects.exists()


@pytest.mark.parametrize('event_type', ['', None, 'pedido', 'Pedido.pago', 'pedido.cancelado'])
def test_rejects_invalid_or_incompatible_type(event_data, event_type):
    event_data['event_type'] = event_type

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert 'event_type' in serializer.errors
    assert not Event.objects.exists()


@pytest.mark.parametrize('payload', [None, [], '', 'aviso', 123, {'value': '\x00'}])
def test_rejects_invalid_payload(event_data, payload):
    event_data['payload'] = payload

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert 'payload' in serializer.errors
    assert not Event.objects.exists()


def test_accepts_empty_payload_object(event_data):
    event_data['payload'] = {}
    serializer = EventSerializer(data=event_data)

    assert serializer.is_valid(), serializer.errors
    event = serializer.save()
    assert event.payload == {}
    assert Event.objects.get(pk=event.pk).payload == {}


def test_rejects_payload_over_utf8_limit(event_data):
    event_data['payload'] = {'text': 'á' * 8192}

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert 'payload' in serializer.errors
    assert not Event.objects.exists()


def test_revalidates_destination_allowlist_before_registration(event_data, settings):
    settings.WEBHOOK_ALLOWED_URLS = []

    serializer = EventSerializer(data=event_data)

    assert not serializer.is_valid()
    assert 'endpoint_id' in serializer.errors
    assert not Event.objects.exists()
