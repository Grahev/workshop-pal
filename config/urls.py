from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from accounts.permissions import can_manage
from api.api import api

admin.site.site_header = "Workshop Pal"
admin.site.site_title = "Workshop Pal"



@login_required
def root(request):
    """Office roles land on the live board; operators go straight to the workstations."""
    return redirect("board:board" if can_manage(request.user) else "floor:home")


urlpatterns = [
    path("", root, name="root"),
    path("board/", include("board.urls")),
    path("jobs/", include("jobs.urls")),
    path("floor/", include("floor.urls")),
    path("files/", include("attachments.urls")),
    path("api/v1/", api.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    # Development only. Production serves private files through signed URLs.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
