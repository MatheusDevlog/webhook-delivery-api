from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAdminUser

from .models import Event
from .permissions import EventModelPermissions
from .serializers import EventSerializer


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
