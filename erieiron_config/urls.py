from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.view_businesses, name="view_home"),
    path("businesses/", views.view_businesses, name="view_businesses"),
    path("business/<uuid:business_id>", views.view_business, name="view_business"),
    path("business/add", views.action_add_business, name="action_add_business"),
    path("business/find", views.action_find_business, name="action_find_business"),
    path("business/update/<uuid:business_id>", views.action_update_business, name="action_update_business"),
    path("business/bootstrap/<uuid:business_id>", views.action_bootstrap_business, name="action_bootstrap_business"),
    path("business/delete/<uuid:business_id>", views.action_delete_business, name="action_delete_business"),

    path("initiative/<str:initiative_id>", views.view_initiative, name="view_initiative"),
    path("_initiative/add", views.action_add_initiative, name="action_add_initiative"),
    path("_initiative/delete/<str:initiative_id>", views.action_delete_initiative, name="action_delete_initiative"),
    path("_initiative/dowork/<str:initiative_id>", views.action_dowork_initiative, name="action_dowork_initiative"),

    path("task/resolve/<str:task_id>", views.action_resolve_task, name="action_resolve_task"),
    path("task/retry/<str:task_id>", views.action_retry_task, name="action_retry_task"),
    path("task/restart/<str:task_id>", views.action_restart_task, name="action_restart_task"),
    path("task/delete/<str:task_id>", views.action_delete_task, name="action_delete_task"),
    path("task/update/<str:task_id>", views.action_update_task, name="action_update_task"),
    path("task/guidance/<str:task_id>", views.action_update_task_guidance, name="action_update_task_guidance"),
    path("task/latest_iteration/<str:task_id>", views.view_self_driver_latest_iteration, name="view_self_driver_latest_iteration"),
    path("process/kill/<uuid:process_id>", views.action_kill_process, name="action_kill_process"),
    path("task/<str:task_id>", views.view_task, name="view_task"),

    path("self_driver_iteration/<str:iteration_id>", views.view_self_driver_iteration, name="view_self_driver_iteration"),
    path("iteration/delete/<uuid:iteration_id>", views.action_delete_iteration, name="action_delete_iteration"),
    
    path("lesson/toggle/<uuid:lesson_id>", views.action_toggle_lesson_validity, name="action_toggle_lesson_validity"),
]
