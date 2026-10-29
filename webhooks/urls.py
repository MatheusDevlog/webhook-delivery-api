from django.urls import path

from .views import DeliveryAttemptDetailView, EventCreateView, EventDetailView, EventSendView


app_name = 'webhooks'

urlpatterns = [
    path('eventos/', EventCreateView.as_view(), name='event-create'),
    path('eventos/<uuid:pk>/', EventDetailView.as_view(), name='event-detail'),
    path(
        'eventos/<uuid:pk>/tentativa/',
        DeliveryAttemptDetailView.as_view(),
        name='delivery-attempt-detail',
    ),
    path('eventos/<uuid:pk>/enviar/', EventSendView.as_view(), name='event-send'),
]
