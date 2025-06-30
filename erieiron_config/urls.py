from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.view_businesses, name="view_home"),
    path("businesses/", views.view_businesses, name="view_businesses"),
    path("business/<uuid:business_id>", views.view_business, name="view_business"),
    path("business/add", views.action_add_business, name="action_add_business"),
    path("business/find", views.action_find_business, name="action_find_business"),

    path("initiative/<str:initiative_id>", views.view_initiative, name="view_initiative"),

    path("task/resolve/<str:task_id>", views.action_resolve_task, name="action_resolve_task"),
    path("task/retry/<str:task_id>", views.action_retry_task, name="action_retry_task"),
    path("task/<str:task_id>", views.view_task, name="view_task"),

    path("self_driver_iteration/<uuid:iteration_id>", views.view_self_driver_iteration, name="view_self_driver_iteration"),
]
