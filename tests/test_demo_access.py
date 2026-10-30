from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.http import Http404, HttpResponse

from webhooks.demo_access import (
    VISITOR_COOKIE_MAX_AGE,
    VISITOR_COOKIE_NAME,
    VISITOR_COOKIE_SALT,
    get_visitor_attempt,
    get_visitor_id,
    prepare_visitor_response,
)
from webhooks.models import DeliveryAttempt, Event, WebhookEndpoint


def signed_request(rf, visitor_id, salt=VISITOR_COOKIE_SALT):
    response = HttpResponse()
    response.set_signed_cookie(VISITOR_COOKIE_NAME, str(visitor_id), salt=salt)
    request = rf.get('/')
    request.COOKIES[VISITOR_COOKIE_NAME] = response.cookies[VISITOR_COOKIE_NAME].value
    return request


@pytest.mark.parametrize('secure', [False, True])
def test_prepares_signed_cookie_without_database_or_session(rf, settings, secure):
    settings.SESSION_COOKIE_SECURE = secure
    response = prepare_visitor_response(rf.get('/', secure=secure), HttpResponse())
    cookie = response.cookies[VISITOR_COOKIE_NAME]
    request = rf.get('/')
    request.COOKIES[VISITOR_COOKIE_NAME] = cookie.value

    assert isinstance(get_visitor_id(request), UUID)
    assert cookie['httponly']
    assert bool(cookie['secure']) is secure
    assert cookie['samesite'] == 'Lax'
    assert cookie['max-age'] == VISITOR_COOKIE_MAX_AGE == 86400
    assert cookie['path'] == '/'
    assert not cookie['domain']
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Vary'] == 'Cookie'
    assert set(response.cookies) == {VISITOR_COOKIE_NAME}


def test_preparing_response_preserves_existing_visitor_identifier(rf):
    visitor_id = uuid4()
    request = signed_request(rf, visitor_id)

    response = prepare_visitor_response(request, HttpResponse())
    request.COOKIES[VISITOR_COOKIE_NAME] = response.cookies[VISITOR_COOKIE_NAME].value

    assert get_visitor_id(request) == visitor_id


@pytest.mark.parametrize('kind', ['missing', 'unsigned', 'tampered', 'wrong_salt', 'invalid_uuid'])
def test_rejects_cookie_without_valid_signature_and_uuid(rf, kind):
    visitor_id = uuid4()
    request = signed_request(rf, visitor_id)
    if kind == 'missing':
        request.COOKIES.clear()
    elif kind == 'unsigned':
        request.COOKIES[VISITOR_COOKIE_NAME] = str(visitor_id)
    elif kind == 'tampered':
        request.COOKIES[VISITOR_COOKIE_NAME] += 'changed'
    elif kind == 'wrong_salt':
        request = signed_request(rf, visitor_id, salt='another-purpose')
    else:
        request = signed_request(rf, 'not-a-uuid')

    assert get_visitor_id(request) is None


@pytest.mark.parametrize(('age', 'accepted'), [(86400, True), (86401, False)])
def test_signed_cookie_has_server_side_age_limit(rf, monkeypatch, age, accepted):
    start = 1800000000
    monkeypatch.setattr('django.core.signing.time.time', lambda: start)
    visitor_id = uuid4()
    request = signed_request(rf, visitor_id)
    monkeypatch.setattr('django.core.signing.time.time', lambda: start + age)

    assert get_visitor_id(request) == (visitor_id if accepted else None)


def test_invalid_cookie_is_replaced_when_preparing_page_response(rf):
    visitor_id = uuid4()
    request = signed_request(rf, visitor_id)
    request.COOKIES[VISITOR_COOKIE_NAME] += 'changed'

    response = prepare_visitor_response(request, HttpResponse())
    request.COOKIES[VISITOR_COOKIE_NAME] = response.cookies[VISITOR_COOKIE_NAME].value

    assert isinstance(get_visitor_id(request), UUID)
    assert get_visitor_id(request) != visitor_id


@pytest.fixture
def create_attempt(db):
    endpoint = WebhookEndpoint.objects.create(
        url='https://receiver.example/demonstracao/receptor/sucesso/', event_type='pedido.pago',
    )

    def create(visitor_id, initiated_by=None):
        event = Event.objects.create(
            endpoint=endpoint, event_type='pedido.pago', payload={'pedido_id': 123},
        )
        return DeliveryAttempt.objects.create(
            event=event, visitor_id=visitor_id, initiated_by=initiated_by,
        )

    return create


def test_visitor_can_find_only_their_own_attempt(rf, create_attempt):
    visitor_id = uuid4()
    own = create_attempt(visitor_id)
    create_attempt(uuid4())
    request = signed_request(rf, visitor_id)

    found = get_visitor_attempt(request, own.event_id)

    assert found.pk == own.pk
    assert found.event.pk == own.event_id
    assert found.visitor_id == visitor_id


@pytest.mark.parametrize('owner', ['another_visitor', 'operator'])
def test_visitor_cannot_find_attempt_from_another_owner(rf, create_attempt, django_user_model, owner):
    operator = None
    if owner == 'operator':
        operator = django_user_model.objects.create_user(username='operator', is_staff=True)
    attempt = create_attempt(
        uuid4() if owner == 'another_visitor' else None, initiated_by=operator,
    )
    request = signed_request(rf, uuid4())

    with pytest.raises(Http404, match='Tentativa não encontrada.'):
        get_visitor_attempt(request, attempt.event_id)


@pytest.mark.parametrize('kind', ['missing', 'unsigned', 'tampered'])
def test_known_event_id_does_not_grant_access_without_valid_cookie(rf, create_attempt, kind):
    visitor_id = uuid4()
    attempt = create_attempt(visitor_id)
    request = signed_request(rf, visitor_id)
    if kind == 'missing':
        request.COOKIES.clear()
    elif kind == 'unsigned':
        request.COOKIES[VISITOR_COOKIE_NAME] = str(visitor_id)
    else:
        request.COOKIES[VISITOR_COOKIE_NAME] += 'changed'

    with pytest.raises(Http404):
        get_visitor_attempt(request, attempt.event_id)


def test_unknown_event_id_returns_not_found(rf, create_attempt):
    visitor_id = uuid4()
    create_attempt(visitor_id)

    with pytest.raises(Http404):
        get_visitor_attempt(signed_request(rf, visitor_id), uuid4())


def test_invalid_event_identifier_is_rejected_before_database_access(rf):
    request = signed_request(rf, uuid4())

    with pytest.raises(Http404):
        get_visitor_attempt(request, 'not-a-uuid')


def test_saved_attempt_remains_readable_when_sending_is_disabled(rf, create_attempt, settings):
    visitor_id = uuid4()
    attempt = create_attempt(visitor_id)
    settings.WEBHOOK_DEMO_ENABLED = False
    settings.WEBHOOK_MAX_DELIVERIES_PER_DAY = 0

    assert get_visitor_attempt(signed_request(rf, visitor_id), attempt.event_id).pk == attempt.pk


def test_staff_privileges_do_not_bypass_visitor_lookup(rf, create_attempt):
    attempt = create_attempt(uuid4())
    request = signed_request(rf, uuid4())
    request.user = SimpleNamespace(is_staff=True, is_superuser=True)

    with pytest.raises(Http404):
        get_visitor_attempt(request, attempt.event_id)
