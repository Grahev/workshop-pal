from django.urls import path

from . import views

app_name = "floor"

urlpatterns = [
    path("", views.home, name="home"),
    path("station/<int:work_centre_id>/", views.station, name="station"),
    path("operation/<int:pk>/", views.operation_detail, name="operation"),
    path("operation/<int:pk>/<str:action>/", views.operation_action, name="action"),
]
