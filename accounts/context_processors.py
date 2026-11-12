from .permissions import can_manage


def roles(request):
    return {"can_manage": can_manage(request.user)}
