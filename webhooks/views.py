from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAdminUser

from .models import DeliveryAttempt, Event
from .permissions import CanViewDeliveryAttempt, EventModelPermissions
from .serializers import DeliveryAttemptSerializer, EventSerializer


class EventCreateView(CreateAPIView):
    queryset = Event.objects.all()
    serializer_class = EventSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser, EventModelPermissions]
    parser_classes = [JSONParser]


class EventDetailView(RetrieveAPIView):
    queryset = Event.objects.all()
    serializer_class = EventSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser, EventModelPermissions]


class DeliveryAttemptDetailView(RetrieveAPIView):
    queryset = DeliveryAttempt.objects.select_related('event')
    serializer_class = DeliveryAttemptSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser, CanViewDeliveryAttempt]
    lookup_field = 'event_id'
    lookup_url_kwarg = 'pk'
