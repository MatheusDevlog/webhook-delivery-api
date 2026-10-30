import pytest
from django.core.exceptions import ValidationError

from webhooks.validators import validate_event_payload


@pytest.mark.parametrize(
    'payload',
    [
        {},
        {'pedido_id': 'PEDIDO-001'},
        {'itens': [{'sku': 'A', 'quantidade': 2}], 'pago': True, 'observacao': None},
        {'mensagem': 'Pagamento confirmado: ação concluída ✅'},
        {'texto': r'\u0000'},
    ],
)
def test_accepts_json_object_payload(payload):
    validate_event_payload(payload)


@pytest.mark.parametrize('payload', [None, [], [{}], '', 'aviso', True, 123, 1.5])
def test_rejects_non_object_payload(payload):
    with pytest.raises(ValidationError) as error:
        validate_event_payload(payload)

    assert error.value.code == 'invalid_payload_type'


@pytest.mark.parametrize(
    'payload, code',
    [
        pytest.param({'value': float('nan')}, 'invalid_payload', id='nan'),
        pytest.param({'value': float('inf')}, 'invalid_payload', id='infinity'),
        pytest.param({'value': float('-inf')}, 'invalid_payload', id='negative-infinity'),
        pytest.param({'value': {1, 2}}, 'invalid_payload', id='set'),
        pytest.param({'value': b'abc'}, 'invalid_payload', id='bytes'),
        pytest.param({'value': '\ud800'}, 'invalid_payload', id='invalid-unicode'),
        pytest.param({1: 'value'}, 'invalid_payload_key', id='numeric-key'),
        pytest.param({'items': [{1: 'value'}]}, 'invalid_payload_key', id='nested-key'),
        pytest.param({'value': '\x00'}, 'invalid_payload_character', id='null-character'),
        pytest.param({'\x00': 'value'}, 'invalid_payload_character', id='null-in-key'),
        pytest.param({'items': [{'value': '\x00'}]}, 'invalid_payload_character', id='nested-null'),
    ],
)
def test_rejects_values_that_cannot_be_stored_as_json_object(payload, code):
    with pytest.raises(ValidationError) as error:
        validate_event_payload(payload)

    assert error.value.code == code


def test_rejects_circular_payload():
    payload = {}
    payload['self'] = payload

    with pytest.raises(ValidationError) as error:
        validate_event_payload(payload)

    assert error.value.code == 'invalid_payload'


def test_accepts_payload_at_16_kib_boundary():
    payload = {'text': 'a' * (16384 - len(b'{"text":""}'))}

    validate_event_payload(payload)


def test_rejects_payload_one_byte_over_limit():
    payload = {'text': 'a' * (16385 - len(b'{"text":""}'))}

    with pytest.raises(ValidationError) as error:
        validate_event_payload(payload)

    assert error.value.code == 'payload_too_large'


def test_payload_size_counts_utf8_bytes():
    payload = {'text': 'á' * 8192}

    with pytest.raises(ValidationError) as error:
        validate_event_payload(payload)

    assert error.value.code == 'payload_too_large'
