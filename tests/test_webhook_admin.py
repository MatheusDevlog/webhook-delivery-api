import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from webhooks.models import WebhookEndpoint


pytestmark = pytest.mark.django_db
ALLOWED_URL = 'https://receiver.example/webhook'


@pytest.fixture
def allowed_destinations(settings):
    settings.WEBHOOK_ALLOWED_URLS = [ALLOWED_URL]


@pytest.fixture
def operator(django_user_model):
    user = django_user_model.objects.create_user(username='operator', is_staff=True)
    user.user_permissions.set(
        Permission.objects.filter(
            content_type__app_label='webhooks',
            codename__in=['add_webhookendpoint', 'view_webhookendpoint'],
        )
    )
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def endpoint(allowed_destinations):
    return WebhookEndpoint.objects.create(url=ALLOWED_URL, event_type='pedido.pago')


def test_anonymous_visitor_cannot_register_endpoint(client, allowed_destinations):
    response = client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': ALLOWED_URL, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 302
    assert response.headers['Location'].startswith(reverse('admin:login'))
    assert not WebhookEndpoint.objects.exists()


def test_login_without_staff_access_cannot_register_endpoint(
    client, django_user_model, allowed_destinations
):
    user = django_user_model.objects.create_user(username='visitor')
    client.force_login(user)

    response = client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': ALLOWED_URL, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 302
    assert not WebhookEndpoint.objects.exists()


def test_staff_without_model_permission_cannot_register_endpoint(
    client, django_user_model, allowed_destinations
):
    user = django_user_model.objects.create_user(username='staff', is_staff=True)
    client.force_login(user)

    response = client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': ALLOWED_URL, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 403
    assert not WebhookEndpoint.objects.exists()


def test_view_permission_does_not_grant_registration(
    client, django_user_model, allowed_destinations
):
    user = django_user_model.objects.create_user(username='reader', is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(
            content_type__app_label='webhooks', codename='view_webhookendpoint'
        )
    )
    client.force_login(user)

    response = client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': ALLOWED_URL, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 403
    assert not WebhookEndpoint.objects.exists()


@pytest.mark.parametrize('submitted_url', [ALLOWED_URL, 'receiver.example/webhook'])
def test_operator_can_register_allowed_endpoint(
    operator_client, allowed_destinations, submitted_url
):
    response = operator_client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': submitted_url, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 302
    saved = WebhookEndpoint.objects.get()
    assert saved.url == ALLOWED_URL
    assert saved.event_type == 'pedido.pago'


@pytest.mark.parametrize(
    'values, error_field',
    [
        ({'url': 'https://other.example/webhook', 'event_type': 'pedido.pago'}, 'url'),
        ({'url': 'http://receiver.example/webhook', 'event_type': 'pedido.pago'}, 'url'),
        ({'url': 'https://receiver.example:99999/webhook', 'event_type': 'pedido.pago'}, 'url'),
        ({'url': ALLOWED_URL, 'event_type': 'Pedido.pago'}, 'event_type'),
    ],
)
def test_admin_rejects_invalid_form_without_saving(
    operator_client, allowed_destinations, values, error_field
):
    response = operator_client.post(reverse('admin:webhooks_webhookendpoint_add'), values)

    assert response.status_code == 200
    assert error_field in response.context['adminform'].form.errors
    assert not WebhookEndpoint.objects.exists()


def test_admin_rejects_duplicate_without_creating_another(operator_client, endpoint):
    response = operator_client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': endpoint.url, 'event_type': endpoint.event_type},
    )

    assert response.status_code == 200
    assert '__all__' in response.context['adminform'].form.errors
    assert WebhookEndpoint.objects.count() == 1


def test_operator_can_list_and_inspect_endpoint(operator_client, endpoint):
    listing = operator_client.get(reverse('admin:webhooks_webhookendpoint_changelist'))
    detail = operator_client.get(
        reverse('admin:webhooks_webhookendpoint_change', args=[endpoint.pk])
    )

    assert listing.status_code == 200
    assert str(endpoint.pk) in listing.content.decode()
    assert detail.status_code == 200
    assert endpoint.url in detail.content.decode()
    assert detail.context['has_change_permission'] is False
    assert detail.context['has_delete_permission'] is False


@pytest.mark.parametrize('superuser', [False, True])
def test_admin_blocks_editing_even_for_superuser(
    operator_client, operator, endpoint, superuser
):
    operator.is_superuser = superuser
    operator.save(update_fields=['is_superuser'])

    response = operator_client.post(
        reverse('admin:webhooks_webhookendpoint_change', args=[endpoint.pk]),
        {'url': endpoint.url, 'event_type': 'pedido.cancelado'},
    )

    assert response.status_code == 403
    endpoint.refresh_from_db()
    assert endpoint.event_type == 'pedido.pago'


@pytest.mark.parametrize('superuser', [False, True])
def test_admin_blocks_deleting_even_for_superuser(
    operator_client, operator, endpoint, superuser
):
    operator.is_superuser = superuser
    operator.save(update_fields=['is_superuser'])

    response = operator_client.post(
        reverse('admin:webhooks_webhookendpoint_delete', args=[endpoint.pk]),
        {'post': 'yes'},
    )

    assert response.status_code == 403
    assert WebhookEndpoint.objects.filter(pk=endpoint.pk).exists()


def test_admin_rejects_missing_csrf_token(operator, allowed_destinations):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)

    response = client.post(
        reverse('admin:webhooks_webhookendpoint_add'),
        {'url': ALLOWED_URL, 'event_type': 'pedido.pago'},
    )

    assert response.status_code == 403
    assert not WebhookEndpoint.objects.exists()
