import uuid

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .validators import (
    validate_event_payload,
    validate_event_type,
    validate_webhook_url,
)


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


class Event(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pendente'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(
        WebhookEndpoint,
        verbose_name='destino',
        on_delete=models.PROTECT,
        related_name='events',
    )
    event_type = models.CharField(
        'tipo de evento', max_length=100, validators=[validate_event_type],
    )
    # clean() validates empty values too, accepting {} but rejecting [] and null.
    payload = models.JSONField('dados do evento', blank=True)
    status = models.CharField(
        'estado',
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        editable=False,
    )
    created_at = models.DateTimeField('criado em', auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'evento'
        verbose_name_plural = 'eventos'
        default_permissions = ('add', 'view')
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status='pending'),
                name='event_valid_status',
                violation_error_code='invalid_event_status',
                violation_error_message='O estado do evento deve ser pendente nesta etapa.',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        try:
            validate_event_payload(self.payload)
        except ValidationError as error:
            errors['payload'] = error

        try:
            endpoint = self.endpoint if self.endpoint_id else None
        except (WebhookEndpoint.DoesNotExist, ValidationError):
            endpoint = None

        if endpoint is not None:
            try:
                validate_webhook_url(endpoint.url)
            except ValidationError as error:
                errors['endpoint'] = error
            if self.event_type and self.event_type != endpoint.event_type:
                errors['event_type'] = ValidationError(
                    'O tipo do evento deve corresponder ao tipo cadastrado no destino.',
                    code='event_type_mismatch',
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.event_type} - {self.id}'


class DeliveryAttempt(models.Model):
    class ErrorCode(models.TextChoices):
        HTTP_ERROR = 'http_error', 'Resposta HTTP sem sucesso'
        NETWORK_ERROR = 'network_error', 'Falha de comunicação'
        TIMEOUT = 'timeout', 'Tempo limite excedido'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(
        Event,
        verbose_name='evento',
        on_delete=models.PROTECT,
        related_name='delivery_attempt',
    )
    started_at = models.DateTimeField(
        'iniciada em', default=timezone.now, editable=False,
    )
    finished_at = models.DateTimeField('finalizada em', null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(
        'código HTTP',
        null=True,
        blank=True,
        validators=[MinValueValidator(200), MaxValueValidator(599)],
    )
    error_code = models.CharField(
        'categoria de falha',
        max_length=20,
        choices=ErrorCode.choices,
        default='',
        blank=True,
    )

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'tentativa de entrega'
        verbose_name_plural = 'tentativas de entrega'
        default_permissions = ('view',)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(finished_at__isnull=True)
                    | models.Q(finished_at__gte=models.F('started_at'))
                ),
                name='attempt_valid_timestamps',
                violation_error_message=(
                    'O término da tentativa não pode ser anterior ao início.'
                ),
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        finished_at__isnull=True, http_status__isnull=True,
                        error_code='',
                    )
                    | models.Q(
                        finished_at__isnull=False, http_status__isnull=False,
                        http_status__gte=200, http_status__lte=299, error_code='',
                    )
                    | models.Q(
                        finished_at__isnull=False, http_status__isnull=False,
                        http_status__gte=300, http_status__lte=599,
                        error_code='http_error',
                    )
                    | models.Q(
                        finished_at__isnull=False, http_status__isnull=True,
                        error_code__in=['network_error', 'timeout'],
                    )
                ),
                name='attempt_valid_result',
                violation_error_message=(
                    'Informe um resultado compatível com o término, '
                    'o código HTTP e a categoria de falha.'
                ),
            ),
        ]

    def __str__(self):
        return f'Tentativa - {self.event_id}'
