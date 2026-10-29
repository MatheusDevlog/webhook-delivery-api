from rest_framework.permissions import BasePermission, DjangoModelPermissions


class EventModelPermissions(DjangoModelPermissions):
    perms_map = {
        **DjangoModelPermissions.perms_map,
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': ['%(app_label)s.view_%(model_name)s'],
    }


class CanViewDeliveryAttempt(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm('webhooks.view_deliveryattempt')


class CanSendEvent(BasePermission):
    def has_permission(self, request, view):
        return request.user.has_perm('webhooks.send_event')
