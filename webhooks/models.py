import uuid

from django.db import models

from .validators import validate_event_type, validate_webhook_url


class WebhookEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.URLField(
        'URL de destino',
        max_length=2048,
        validators=[validate_webhook_url],
    )
    event_type = models.CharField(
        'tipo de evento',
        max_length=100,
        validators=[validate_event_type],
    )
    created_at = models.DateTimeField('criado em', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'destino de webhook'
        verbose_name_plural = 'destinos de webhook'
        default_permissions = ('add', 'view')
        constraints = [
            models.UniqueConstraint(
                fields=['url', 'event_type'],
                name='unique_webhook_url_event_type',
                violation_error_code='duplicate_endpoint',
                violation_error_message=(
                    'Já existe um destino com essa URL e esse tipo de evento.'
                ),
            ),
        ]

    def __str__(self):
        return f'{self.event_type} - {self.url}'
