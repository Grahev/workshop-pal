from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.job_list, name="list"),
    path("new/", views.job_create, name="create"),
    path("<int:pk>/", views.job_detail, name="detail"),
    path("<int:pk>/edit/", views.job_edit, name="edit"),
    path("<int:pk>/archive/", views.job_archive, name="archive"),
    path("<int:job_pk>/items/new/", views.item_create, name="item_create"),
    path("items/<int:pk>/", views.item_detail, name="item"),
    path("items/<int:pk>/spec/", views.item_spec, name="item_spec"),
    path("items/<int:pk>/route/apply/", views.route_apply, name="route_apply"),
    path("items/<int:pk>/route/add/", views.operation_add, name="operation_add"),
    path("operations/<int:pk>/edit/", views.operation_edit, name="operation_edit"),
    path("operations/<int:pk>/move/<str:direction>/", views.operation_move, name="operation_move"),
    path("operations/<int:pk>/remove/", views.operation_remove, name="operation_remove"),
    path("operations/<int:pk>/skip/", views.operation_skip, name="operation_skip"),
    path("operations/<int:pk>/links/add/", views.link_add, name="link_add"),
    path("operations/<int:pk>/gates/", views.operation_gates, name="operation_gates"),
    path("links/<int:pk>/update/", views.link_update, name="link_update"),
    path("links/<int:pk>/remove/", views.link_remove, name="link_remove"),
    path("items/<int:pk>/route/sequence/", views.route_sequence, name="route_sequence"),
    path("items/<int:pk>/route/enforcement/", views.route_enforcement, name="route_enforcement"),
    path("items/<int:pk>/route/save-template/", views.route_save_template, name="route_save_template"),
    path("gates/<int:pk>/<str:action>/", views.gate_update, name="gate_update"),
    path("issues/<int:pk>/resolve/", views.issue_resolve, name="issue_resolve"),
]
