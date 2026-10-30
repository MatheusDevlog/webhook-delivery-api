import json
import uuid

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from webhooks.models import Event, WebhookEndpoint


pytestmark = pytest.mark.django_db
CREATE_URL = '/api/eventos/'
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


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user(username='operator', is_staff=True)
    user.user_permissions.set(Permission.objects.filter(
        content_type__app_label='webhooks',
        content_type__model='event',
        codename__in=['add_event', 'view_event'],
    ))
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def event(endpoint):
    return Event.objects.create(
        endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 'PEDIDO-001'},
    )


def test_operator_registers_pending_event(operator_client, event_data):
    response = operator_client.post(CREATE_URL, event_data, content_type='application/json')

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {'id', 'endpoint_id', 'event_type', 'payload', 'status', 'created_at'}
    saved = Event.objects.get(pk=body['id'])
    assert isinstance(saved.pk, uuid.UUID)
    assert body['endpoint_id'] == event_data['endpoint_id']
    assert body['event_type'] == saved.event_type == 'pedido.pago'
    assert body['payload'] == saved.payload == event_data['payload']
    assert body['status'] == saved.status == 'pending'
    assert body['created_at']
    assert saved.created_at is not None
    assert Event.objects.count() == 1


def test_operator_can_read_registered_event(operator_client, event):
    response = operator_client.get(f'/api/eventos/{event.pk}/')

    assert response.status_code == 200
    body = response.json()
    assert body['id'] == str(event.pk)
    assert body['endpoint_id'] == str(event.endpoint_id)
    assert body['event_type'] == event.event_type
    assert body['payload'] == event.payload
    assert body['status'] == 'pending'


@pytest.mark.parametrize('role', ['anonymous', 'ordinary', 'staff', 'inactive'])
def test_registration_requires_active_staff_and_model_permission(
    client, operator, event_data, role
):
    if role != 'anonymous':
        if role == 'ordinary':
            operator.is_staff = False
            operator.save(update_fields=['is_staff'])
        elif role == 'staff':
            operator.user_permissions.clear()
        elif role == 'inactive':
            operator.is_active = False
            operator.save(update_fields=['is_active'])
        client.force_login(operator)

    response = client.post(CREATE_URL, event_data, content_type='application/json')

    assert response.status_code == 403
    assert not Event.objects.exists()


@pytest.mark.parametrize('role', ['anonymous', 'ordinary', 'staff', 'inactive'])
def test_reading_requires_active_staff_and_model_permission(client, operator, event, role):
    if role != 'anonymous':
        if role == 'ordinary':
            operator.is_staff = False
            operator.save(update_fields=['is_staff'])
        elif role == 'staff':
            operator.user_permissions.clear()
        elif role == 'inactive':
            operator.is_active = False
            operator.save(update_fields=['is_active'])
        client.force_login(operator)

    response = client.get(f'/api/eventos/{event.pk}/')

    assert response.status_code == 403
    assert Event.objects.count() == 1


def test_add_permission_does_not_grant_reading(operator_client, operator, event_data):
    operator.user_permissions.set(Permission.objects.filter(
        content_type__app_label='webhooks', codename='add_event',
    ))
    created = operator_client.post(CREATE_URL, event_data, content_type='application/json')

    assert created.status_code == 201
    response = operator_client.get(f'/api/eventos/{created.json()["id"]}/')
    assert response.status_code == 403


def test_view_permission_does_not_grant_registration(operator_client, operator, event):
    operator.user_permissions.set(Permission.objects.filter(
        content_type__app_label='webhooks', codename='view_event',
    ))
    detail = operator_client.get(f'/api/eventos/{event.pk}/')
    response = operator_client.post(CREATE_URL, {
        'endpoint_id': str(event.endpoint_id),
        'event_type': event.event_type,
        'payload': {},
    }, content_type='application/json')

    assert detail.status_code == 200
    assert response.status_code == 403
    assert Event.objects.count() == 1


def test_existing_event_remains_readable_after_destination_is_removed_from_allowlist(
    operator_client, event, settings
):
    settings.WEBHOOK_ALLOWED_URLS = []

    response = operator_client.get(f'/api/eventos/{event.pk}/')

    assert response.status_code == 200
    assert response.json()['payload'] == event.payload


@pytest.mark.parametrize('data', [[], None, 'text', 123])
def test_rejects_non_object_request_body(operator_client, data):
    response = operator_client.post(
        CREATE_URL, json.dumps(data), content_type='application/json',
    )

    assert response.status_code == 400
    assert not Event.objects.exists()


@pytest.mark.parametrize('data', ['{', '{"payload": NaN}', '{"payload": Infinity}'])
def test_rejects_invalid_json(operator_client, data):
    response = operator_client.post(CREATE_URL, data, content_type='application/json')

    assert response.status_code == 400
    assert not Event.objects.exists()


def test_rejects_unsupported_content_type(operator_client, event_data):
    response = operator_client.post(
        CREATE_URL, json.dumps(event_data), content_type='text/plain',
    )

    assert response.status_code == 415
    assert not Event.objects.exists()


@pytest.mark.parametrize('event_id', [str(uuid.uuid4()), 'invalid-uuid'])
def test_reading_missing_or_invalid_id_returns_404(operator_client, event_id):
    response = operator_client.get(f'/api/eventos/{event_id}/')

    assert response.status_code == 404
    assert not Event.objects.exists()


def test_collection_does_not_list_events(operator_client, event):
    response = operator_client.get(CREATE_URL)

    assert response.status_code == 405
    assert Event.objects.count() == 1


@pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete'])
def test_detail_does_not_allow_mutations(operator_client, operator, event, method):
    operator.is_superuser = True
    operator.save(update_fields=['is_superuser'])

    response = getattr(operator_client, method)(
        f'/api/eventos/{event.pk}/', {'payload': {'changed': True}},
        content_type='application/json',
    )

    assert response.status_code == 405
    event.refresh_from_db()
    assert event.payload == {'pedido_id': 'PEDIDO-001'}
    assert event.status == 'pending'


def test_repeated_registration_creates_distinct_ids(operator_client, event_data):
    first = operator_client.post(CREATE_URL, event_data, content_type='application/json')
    second = operator_client.post(CREATE_URL, event_data, content_type='application/json')

    assert first.status_code == second.status_code == 201
    assert first.json()['id'] != second.json()['id']
    assert Event.objects.count() == 2


def test_authenticated_post_requires_csrf_token(operator, event_data):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)

    response = client.post(CREATE_URL, event_data, content_type='application/json')

    assert response.status_code == 403
    assert not Event.objects.exists()


def test_login_session_and_csrf_allow_registration_over_simulated_https(
    operator, event_data, settings
):
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    password = 'test-only-password'
    operator.set_password(password)
    operator.save(update_fields=['password'])
    client = Client(enforce_csrf_checks=True)
    login_url = reverse('admin:login')
    client.get(login_url, secure=True)

    login = client.post(login_url, {
        'username': operator.username,
        'password': password,
        'csrfmiddlewaretoken': client.cookies['csrftoken'].value,
    }, secure=True, HTTP_REFERER='https://testserver/admin/login/')

    assert login.status_code == 302
    assert client.cookies['sessionid']['secure'] is True
    assert client.cookies['csrftoken']['secure'] is True

    page = client.get(CREATE_URL, secure=True, HTTP_ACCEPT='text/html')
    assert page.status_code == 405
    assert page.context['raw_data_post_form'] is not None

    created = client.post(
        CREATE_URL, event_data, content_type='application/json', secure=True,
        HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value,
        HTTP_REFERER='https://testserver/admin/',
    )
    assert created.status_code == 201
    detail = client.get(f'/api/eventos/{created.json()["id"]}/', secure=True)
    assert detail.status_code == 200
    assert Event.objects.get().payload == event_data['payload']


@pytest.mark.parametrize(
    'field, value',
    [
        ('endpoint_id', 'not-a-uuid'),
        ('event_type', 'pedido.cancelado'),
        ('payload', {'value': '\x00'}),
        ('status', 'delivered'),
    ],
)
def test_http_validation_errors_do_not_save_event(operator_client, event_data, field, value):
    event_data[field] = value

    response = operator_client.post(CREATE_URL, event_data, content_type='application/json')

    assert response.status_code == 400
    assert field in response.json()
    assert not Event.objects.exists()
