import uuid

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError

from webhooks.models import Event, WebhookEndpoint


ALLOWED_URL = 'https://receiver.example/webhook'


@pytest.fixture
def endpoint(settings):
    settings.WEBHOOK_ALLOWED_URLS = [ALLOWED_URL]
    return WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')


@pytest.mark.django_db
@pytest.mark.parametrize('payload', [{}, {'pedido_id': 'PEDIDO-001', 'mensagem': 'Pagamento ✅'}])
def test_persists_pending_event_with_destination_and_payload(endpoint, payload):
    event = Event(endpoint=endpoint, event_type='pedido.pago', payload=payload)
    event.full_clean()
    event.save()

    saved = Event.objects.get(pk=event.pk)
    assert isinstance(saved.pk, uuid.UUID)
    assert saved.endpoint_id == endpoint.pk
    assert saved.event_type == 'pedido.pago'
    assert saved.payload == payload
    assert saved.status == 'pending'
    assert saved.created_at is not None
    assert endpoint.events.get().pk == saved.pk


@pytest.mark.django_db
@pytest.mark.parametrize('field', ['endpoint', 'event_type', 'payload'])
def test_rejects_missing_event_data(endpoint, field):
    values = {'endpoint': endpoint, 'event_type': 'pedido.pago', 'payload': {'pedido_id': 'PEDIDO-001'}}
    values.pop(field)

    with pytest.raises(ValidationError) as error:
        Event(**values).full_clean()

    assert field in error.value.message_dict
    assert not Event.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('event_type', ['pedido.cancelado', 'Pedido.pago', 'pedido'])
def test_rejects_incompatible_or_invalid_event_type(endpoint, event_type):
    event = Event(endpoint=endpoint, event_type=event_type, payload={'pedido_id': 'PEDIDO-001'})

    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert 'event_type' in error.value.message_dict
    assert not Event.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('payload', [None, [], '', {'value': '\x00'}])
def test_model_rejects_invalid_payload_including_empty_values(endpoint, payload):
    event = Event(endpoint=endpoint, event_type='pedido.pago', payload=payload)

    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert 'payload' in error.value.message_dict
    assert not Event.objects.exists()


@pytest.mark.django_db
def test_revalidates_destination_after_operator_removes_url(endpoint, settings):
    settings.WEBHOOK_ALLOWED_URLS = []
    event = Event(endpoint=endpoint, event_type='pedido.pago', payload={})

    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert error.value.error_dict['endpoint'][0].code == 'unapproved_destination'
    assert not Event.objects.exists()


@pytest.mark.django_db
def test_rejects_unsafe_destination_even_if_previously_stored(settings):
    url = 'https://127.0.0.1/webhook'
    settings.WEBHOOK_ALLOWED_URLS = [url]
    endpoint = WebhookEndpoint.objects.create(url=url, event_type='pedido.pago')
    event = Event(endpoint=endpoint, event_type='pedido.pago', payload={})

    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert 'endpoint' in error.value.message_dict
    assert not Event.objects.exists()


@pytest.mark.django_db
def test_rejects_nonexistent_destination():
    event = Event(endpoint_id=uuid.uuid4(), event_type='pedido.pago', payload={})

    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert 'endpoint' in error.value.message_dict
    assert not Event.objects.exists()


@pytest.mark.django_db
def test_database_enforces_destination_reference():
    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.create(endpoint_id=uuid.uuid4(), event_type='pedido.pago', payload={})
        connection.check_constraints()

    assert not Event.objects.exists()


@pytest.mark.django_db
def test_destination_with_event_cannot_be_deleted(endpoint):
    event = Event.objects.create(endpoint=endpoint, event_type='pedido.pago', payload={})

    with pytest.raises(ProtectedError):
        endpoint.delete()

    assert WebhookEndpoint.objects.filter(pk=endpoint.pk).exists()
    assert Event.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_rejects_unsupported_event_status_in_validation_and_database(endpoint):
    event = Event(
        endpoint=endpoint, event_type='pedido.pago', payload={}, status='unknown',
    )
    with pytest.raises(ValidationError) as error:
        event.full_clean()

    assert error.value.error_dict[NON_FIELD_ERRORS][0].code == 'invalid_event_status'

    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.create(
            endpoint=endpoint, event_type='pedido.pago', payload={}, status='unknown',
        )

    assert not Event.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('status', ['sending', 'delivered', 'failed'])
def test_persists_supported_delivery_statuses(endpoint, status):
    event = Event(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 'PEDIDO-001'},
        status=status,
    )
    event.full_clean()
    event.save()
    event.refresh_from_db()

    assert event.status == status
    assert event.endpoint_id == endpoint.pk
    assert event.payload == {'pedido_id': 'PEDIDO-001'}


@pytest.mark.django_db
def test_repeated_registration_creates_distinct_events(endpoint):
    first = Event(endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 'PEDIDO-001'})
    first.full_clean()
    first.save()
    second = Event(endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 'PEDIDO-001'})
    second.full_clean()
    second.save()

    assert first.pk != second.pk
    assert Event.objects.count() == 2


@pytest.mark.django_db
def test_event_permissions_include_sending_without_editing_or_deleting():
    permissions = Permission.objects.filter(
        content_type__app_label='webhooks', content_type__model='event',
    ).values_list('codename', flat=True)

    assert set(permissions) == {'add_event', 'view_event', 'send_event'}


@pytest.mark.django_db
@pytest.mark.parametrize('permissions', [
    [], ['add_event'], ['view_event'], ['add_event', 'view_event'],
])
def test_staff_does_not_gain_sending_permission_from_existing_permissions(
    django_user_model, permissions,
):
    user = django_user_model.objects.create_user(username='operator', is_staff=True)
    user.user_permissions.set(Permission.objects.filter(
        content_type__app_label='webhooks', content_type__model='event',
        codename__in=permissions,
    ))

    assert not user.has_perm('webhooks.send_event')


@pytest.mark.django_db
@pytest.mark.parametrize('active', [True, False])
def test_explicit_sending_permission_requires_active_user(django_user_model, active):
    user = django_user_model.objects.create_user(
        username='sender', is_staff=True, is_active=active,
    )
    user.user_permissions.add(Permission.objects.get(
        content_type__app_label='webhooks', content_type__model='event',
        codename='send_event',
    ))

    assert user.has_perm('webhooks.send_event') == active
