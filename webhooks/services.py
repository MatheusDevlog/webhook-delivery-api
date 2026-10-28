from django.db import transaction

from .models import DeliveryAttempt, Event


class DeliveryAlreadyStarted(Exception):
    def __init__(self):
        super().__init__('Este evento já possui uma tentativa ou não está pendente.')


def _start_delivery(event_id):
    with transaction.atomic(durable=True):
        event = Event.objects.get(pk=event_id)
        if event.status != Event.Status.PENDING or DeliveryAttempt.objects.filter(
            event=event,
        ).exists():
            raise DeliveryAlreadyStarted

        event.full_clean()
        updated = Event.objects.filter(
            pk=event.pk, status=Event.Status.PENDING,
        ).update(status=Event.Status.SENDING)
        if not updated:
            raise DeliveryAlreadyStarted

        event.status = Event.Status.SENDING
        return DeliveryAttempt.objects.create(event=event)
