from uuid import UUID, uuid4

from django.conf import settings
from django.http import Http404
from django.utils.cache import patch_vary_headers

from .models import DeliveryAttempt


VISITOR_COOKIE_NAME = 'webhook_demo_visitor'
VISITOR_COOKIE_SALT = 'webhooks.demo.visitor'
VISITOR_COOKIE_MAX_AGE = 24 * 60 * 60


def get_visitor_id(request):
    value = request.get_signed_cookie(
        VISITOR_COOKIE_NAME, default=None,
        salt=VISITOR_COOKIE_SALT, max_age=VISITOR_COOKIE_MAX_AGE,
    )
    if value is None:
        return None
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def prepare_visitor_response(request, response):
    visitor_id = get_visitor_id(request)
    if visitor_id is None:
        visitor_id = uuid4()
    response.set_signed_cookie(
        VISITOR_COOKIE_NAME, str(visitor_id), salt=VISITOR_COOKIE_SALT,
        max_age=VISITOR_COOKIE_MAX_AGE, httponly=True, samesite='Lax',
        secure=settings.SESSION_COOKIE_SECURE,
    )
    response['Cache-Control'] = 'no-store'
    patch_vary_headers(response, ['Cookie'])
    return response


def get_visitor_attempt(request, event_id):
    visitor_id = get_visitor_id(request)
    if visitor_id is None or not isinstance(event_id, UUID):
        raise Http404('Tentativa não encontrada.')
    attempt = DeliveryAttempt.objects.select_related('event').filter(
        event_id=event_id, visitor_id=visitor_id,
    ).first()
    if attempt is None:
        raise Http404('Tentativa não encontrada.')
    return attempt
