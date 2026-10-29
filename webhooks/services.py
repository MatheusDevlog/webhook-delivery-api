from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .delivery import DeliveryResult, post_json
from .limits import check_delivery_limits
from .models import DeliveryAttempt, Event


class DeliveryAlreadyStarted(Exception):
    def __init__(self):
        super().__init__('Este evento já possui uma tentativa ou não está pendente.')


class InvalidEventForDelivery(ValidationError):
    pass


def _start_delivery(event_id, initiated_by=None):
    with transaction.atomic(durable=True):
        event = Event.objects.get(pk=event_id)
        if event.status != Event.Status.PENDING or DeliveryAttempt.objects.filter(
            event=event,
        ).exists():
            raise DeliveryAlreadyStarted

        try:
            event.full_clean()
        except ValidationError as error:
            raise InvalidEventForDelivery(error) from None
        updated = Event.objects.filter(
            pk=event.pk, status=Event.Status.PENDING,
        ).update(status=Event.Status.SENDING)
        if not updated:
            raise DeliveryAlreadyStarted

        check_delivery_limits(initiated_by)
        event.status = Event.Status.SENDING
        return DeliveryAttempt.objects.create(event=event, initiated_by=initiated_by)


def deliver_event(event_id, initiated_by=None):
    attempt = _start_delivery(event_id, initiated_by=initiated_by)
    event = attempt.event
    data = {
        'id': str(event.pk),
        'event_type': event.event_type,
        'created_at': event.created_at.isoformat(),
        'payload': event.payload,
    }
    try:
        result = post_json(event.endpoint.url, data)
    except ValidationError as error:
        if getattr(error, 'code', None) in {'invalid_delivery_body', 'delivery_body_too_large'}:
            raise
        result = DeliveryResult(None, DeliveryAttempt.ErrorCode.DESTINATION_REJECTED)

    with transaction.atomic(durable=True):
        attempt.finished_at = timezone.now()
        attempt.http_status = result.http_status
        attempt.error_code = result.error_code
        attempt.full_clean()
        attempt.save(update_fields=['finished_at', 'http_status', 'error_code'])
        successful = result.http_status is not None and 200 <= result.http_status <= 299
        event.status = Event.Status.DELIVERED if successful else Event.Status.FAILED
        event.save(update_fields=['status'])

    return attempt
