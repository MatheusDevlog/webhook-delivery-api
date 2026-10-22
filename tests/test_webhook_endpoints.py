import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from webhooks.models import WebhookEndpoint
from webhooks.validators import validate_event_type, validate_webhook_url


ALLOWED_URL = 'https://receiver.example/webhook'


@pytest.fixture
def allowed_destinations(settings):
    settings.WEBHOOK_ALLOWED_URLS = [ALLOWED_URL]


@pytest.mark.parametrize('url', [ALLOWED_URL, 'https://receiver.example:443/webhook'])
def test_accepts_exact_approved_https_url(settings, url):
    settings.WEBHOOK_ALLOWED_URLS = [url]

    validate_webhook_url(url)


@pytest.mark.parametrize(
    'url',
    [
        'http://receiver.example/webhook',
        'ftp://receiver.example/webhook',
        'not-a-url',
        'https://receiver.example:99999/webhook',
        'https://receiver.example:invalid/webhook',
    ],
)
def test_rejects_invalid_url_even_if_allowlisted(settings, url):
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    'url',
    [
        'https://user:password@receiver.example/webhook',
        'https://@receiver.example/webhook',
        'https://receiver.example:8443/webhook',
        'https://receiver.example/webhook?secret=value',
        'https://receiver.example/webhook?',
        'https://receiver.example/webhook#section',
        'https://receiver.example/webhook#',
    ],
)
def test_rejects_unsafe_url_components_even_if_allowlisted(settings, url):
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    'host',
    [
        'localhost',
        'app.localhost',
        'service.local',
        'metadata.google.internal',
        '127.0.0.1',
        '10.0.0.1',
        '172.16.0.1',
        '192.168.0.1',
        '169.254.169.254',
        '[::1]',
        '[fc00::1]',
        '[fe80::1]',
        '[::ffff:127.0.0.1]',
    ],
)
def test_rejects_non_public_destination_even_if_allowlisted(settings, host):
    url = f'https://{host}/webhook'
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError):
        validate_webhook_url(url)


@pytest.mark.parametrize(
    'url',
    [
        'https://other.example/webhook',
        f'{ALLOWED_URL}/extra',
        'https://receiver.example/other',
        'https://receiver.example.evil.example/webhook',
    ],
)
def test_allowlist_requires_exact_url(allowed_destinations, url):
    with pytest.raises(ValidationError) as error:
        validate_webhook_url(url)

    assert error.value.code == 'unapproved_destination'


def test_empty_allowlist_denies_registration(settings):
    settings.WEBHOOK_ALLOWED_URLS = []

    with pytest.raises(ValidationError) as error:
        validate_webhook_url(ALLOWED_URL)

    assert error.value.code == 'unapproved_destination'


def test_rejects_url_exceeding_byte_limit_even_if_allowlisted(settings):
    url = 'https://receiver.example/' + 'á' * 1100
    settings.WEBHOOK_ALLOWED_URLS = [url]

    with pytest.raises(ValidationError) as error:
        validate_webhook_url(url)

    assert error.value.code == 'url_too_long'


@pytest.mark.parametrize('event_type', ['pedido.pago', 'pedido_2.item3.criado'])
def test_accepts_namespaced_event_type(event_type):
    validate_event_type(event_type)


@pytest.mark.parametrize(
    'event_type',
    ['pedido', 'Pedido.pago', 'pedido..pago', 'pedido.pago\n', 'pedido.1pago', 'pedido-pago'],
)
def test_rejects_invalid_event_type(event_type):
    with pytest.raises(ValidationError):
        validate_event_type(event_type)


@pytest.mark.django_db
def test_persists_validated_endpoint(allowed_destinations):
    endpoint = WebhookEndpoint(url=ALLOWED_URL, event_type='pedido.pago')
    endpoint.full_clean()
    endpoint.save()

    saved = WebhookEndpoint.objects.get(pk=endpoint.pk)
    assert isinstance(saved.pk, uuid.UUID)
    assert saved.url == ALLOWED_URL
    assert saved.event_type == 'pedido.pago'
    assert saved.created_at is not None


@pytest.mark.django_db
@pytest.mark.parametrize('field', ['url', 'event_type'])
def test_rejects_missing_required_value(allowed_destinations, field):
    values = {'url': ALLOWED_URL, 'event_type': 'pedido.pago'}
    values[field] = ''

    with pytest.raises(ValidationError) as error:
        WebhookEndpoint(**values).full_clean()

    assert field in error.value.message_dict


@pytest.mark.django_db
def test_reports_duplicate_endpoint_before_save(allowed_destinations):
    WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')
    duplicate = WebhookEndpoint(url=ALLOWED_URL, event_type='pedido.pago')

    with pytest.raises(ValidationError) as error:
        duplicate.full_clean()

    assert error.value.error_dict['__all__'][0].code == 'duplicate_endpoint'


@pytest.mark.django_db
def test_database_prevents_duplicate_endpoint(allowed_destinations):
    WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')

    with pytest.raises(IntegrityError), transaction.atomic():
        WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')

    assert WebhookEndpoint.objects.count() == 1


@pytest.mark.django_db
def test_allows_distinct_event_types_for_same_url(allowed_destinations):
    for event_type in ['pedido.pago', 'pedido.cancelado']:
        endpoint = WebhookEndpoint(url=ALLOWED_URL, event_type=event_type)
        endpoint.full_clean()
        endpoint.save()

    assert WebhookEndpoint.objects.count() == 2
