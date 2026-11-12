from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "Workshop Pal"
admin.site.site_title = "Workshop Pal"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="floor:home"), name="root"),
    path("floor/", include("floor.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    # Development only. Production serves private files through signed URLs.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
