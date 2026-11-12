from django.urls import path

from . import views

app_name = "attachments"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("<int:pk>/supersede/", views.supersede, name="supersede"),
    path("<int:pk>/archive/", views.archive, name="archive"),
]
