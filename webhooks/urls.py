from django.urls import path

from .views import EventCreateView, EventDetailView


app_name = 'webhooks'

urlpatterns = [
    path('eventos/', EventCreateView.as_view(), name='event-create'),
    path('eventos/<uuid:pk>/', EventDetailView.as_view(), name='event-detail'),
]
