import logging

from django.http import Http404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.generics import CreateAPIView, GenericAPIView, RetrieveAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .limits import DeliveryLimitError
from .models import DeliveryAttempt, Event
from .permissions import CanSendEvent, CanViewDeliveryAttempt, EventModelPermissions
from .serializers import DeliveryAttemptSerializer, DeliveryRequestSerializer, EventSerializer
from .services import DeliveryAlreadyStarted, InvalidEventForDelivery, deliver_event


logger = logging.getLogger(__name__)


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


class EventSendView(GenericAPIView):
    queryset = Event.objects.all()
    serializer_class = DeliveryRequestSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAdminUser, CanSendEvent]
    parser_classes = [JSONParser]

    def post(self, request, pk):
        self.get_serializer(data=request.data).is_valid(raise_exception=True)
        try:
            attempt = deliver_event(pk, initiated_by=request.user)
            return Response(DeliveryAttemptSerializer(attempt).data)
        except Event.DoesNotExist:
            raise Http404('Evento não encontrado.') from None
        except InvalidEventForDelivery:
            return Response({
                'detail': 'Os dados do evento ou a autorização do destino impedem o envio.',
                'code': 'invalid_event',
            }, status=status.HTTP_400_BAD_REQUEST)
        except DeliveryAlreadyStarted as error:
            return Response({
                'detail': str(error), 'code': 'delivery_already_started',
            }, status=status.HTTP_409_CONFLICT)
        except DeliveryLimitError as error:
            response_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if error.code in {'disabled', 'busy'} else status.HTTP_429_TOO_MANY_REQUESTS
            )
            headers = {'Retry-After': str(error.retry_after)} if error.retry_after else {}
            return Response({
                'detail': str(error), 'code': error.code,
            }, status=response_status, headers=headers)
        except Exception as error:
            logger.error('Falha interna ao solicitar entrega: %s', type(error).__name__)
            return Response({
                'detail': 'Não foi possível concluir a solicitação. Consulte a tentativa.',
                'code': 'delivery_error',
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
