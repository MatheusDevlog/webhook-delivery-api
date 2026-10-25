import json
from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator, URLValidator


MAX_EVENT_PAYLOAD_BYTES = 16 * 1024


validate_event_type = RegexValidator(
    regex=r'\A[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\Z',
    message=(
        'Use partes em minúsculas separadas por pontos, como pedido.pago. '
        'Cada parte deve começar com uma letra e pode conter números e _.'
    ),
    code='invalid_event_type',
)


def validate_webhook_url(value):
    URLValidator(schemes=['https'])(value)
    if len(value.encode('utf-8')) > 2048:
        raise ValidationError(
            'A URL não pode exceder 2048 bytes em UTF-8.',
            code='url_too_long',
        )

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValidationError(
            'Informe uma URL HTTPS com porta válida.',
            code='invalid_webhook_url',
        ) from error

    if (
        parsed.username is not None
        or parsed.password is not None
        or '?' in value
        or '#' in value
        or port not in (None, 443)
    ):
        raise ValidationError(
            'Use HTTPS na porta padrão, sem credenciais, parâmetros ou fragmentos.',
            code='invalid_webhook_url',
        )

    host = parsed.hostname.rstrip('.').lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValidationError(
            'Endereços locais ou internos não são permitidos.',
            code='non_public_destination',
        )

    try:
        address = ip_address(host)
    except ValueError:
        address = None

    if address is not None and not address.is_global:
        raise ValidationError(
            'O endereço IP do destino deve ser público.',
            code='non_public_destination',
        )

    if value not in settings.WEBHOOK_ALLOWED_URLS:
        raise ValidationError(
            'Esta URL não está na lista de destinos permitidos pelo operador.',
            code='unapproved_destination',
        )


def validate_event_payload(value):
    if not isinstance(value, dict):
        raise ValidationError(
            'Os dados do evento devem ser um objeto JSON.',
            code='invalid_payload_type',
        )

    try:
        encoded = json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(',', ':'),
        ).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as error:
        raise ValidationError(
            'Os dados do evento devem conter valores JSON válidos em UTF-8.',
            code='invalid_payload',
        ) from error

    if len(encoded) > MAX_EVENT_PAYLOAD_BYTES:
        raise ValidationError(
            'Os dados do evento não podem exceder 16 KiB em JSON UTF-8 compacto.',
            code='payload_too_large',
        )

    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValidationError(
                    'As chaves dos objetos JSON devem ser textos.',
                    code='invalid_payload_key',
                )
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str) and '\x00' in item:
            raise ValidationError(
                'Os dados do evento não podem conter o caractere nulo U+0000.',
                code='invalid_payload_character',
            )
