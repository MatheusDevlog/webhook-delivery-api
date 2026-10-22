from django.contrib import admin
from django.db import models

from .models import WebhookEndpoint


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ('id', 'url', 'event_type', 'created_at')
    list_filter = ('event_type',)
    search_fields = ('url', 'event_type')
    readonly_fields = ('id', 'created_at')
    fields = ('id', 'url', 'event_type', 'created_at')
    formfield_overrides = {models.URLField: {'assume_scheme': 'https'}}
    actions = None

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
