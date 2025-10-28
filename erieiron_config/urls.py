from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.view_businesses, name="view_home"),
    path("businesses/", views.view_businesses, name="view_businesses"),
    path("businesses/<slug:tab>/", views.view_businesses, name="view_businesses_tab"),
    
    path("_business/add", views.action_add_business, name="action_add_business"),
    path("_business/find", views.action_find_business, name="action_find_business"),
    path("_business/update/<uuid:business_id>", views.action_update_business, name="action_update_business"),
    path("_business/bootstrap/<uuid:business_id>", views.action_bootstrap_business, name="action_bootstrap_business"),
    path("_business/newdomain/<uuid:business_id>", views.action_business_new_domain, name="action_business_new_domain"),
    path("_business/regenerate_architecture/<uuid:business_id>", views.action_business_regenerate_architecture, name="action_business_regenerate_architecture"),
    path("_business/delete/<uuid:business_id>", views.action_delete_business, name="action_delete_business"),
    path("business/<uuid:business_id>", views.view_business, name="view_business"),
    path("business/<slug:tab>/<uuid:business_id>", views.view_business, name="view_business_tab"),
    
    path("initiative/<str:initiative_id>", views.view_initiative, name="view_initiative"),
    path("initiative/<slug:tab>/<str:initiative_id>", views.view_initiative, name="view_initiative_tab"),
    path("_initiative/add", views.action_add_initiative, name="action_add_initiative"),
    path("_initiative/update/<str:initiative_id>", views.action_update_initiative, name="action_update_initiative"),
    path("_initiative/delete/<str:initiative_id>", views.action_delete_initiative, name="action_delete_initiative"),
    path("_initiative/dowork/<str:initiative_id>", views.action_dowork_initiative, name="action_dowork_initiative"),
    path("_initiative/regenerate/architecture/<str:initiative_id>", views.action_initiative_regenerate_architecture, name="action_initiative_regenerate_architecture"),
    path("_initiative/regenerate/user_documentation/<str:initiative_id>", views.action_initiative_regenerate_user_documentation, name="action_initiative_regenerate_user_documentation"),
    path("_initiative/regenerate/tasks/<str:initiative_id>", views.action_initiative_regenerate_tasks, name="action_initiative_regenerate_tasks"),

    path("task/resolve/<str:task_id>", views.action_resolve_task, name="action_resolve_task"),
    path("task/retry/<str:task_id>", views.action_retry_task, name="action_retry_task"),
    path("task/regen_test/<str:task_id>", views.action_task_regenerate_test, name="action_task_regenerate_test"),
    path("task/restart/<str:task_id>", views.action_restart_task, name="action_restart_task"),
    path("task/delete/<str:task_id>", views.action_delete_task, name="action_delete_task"),
    path("task/update/<str:task_id>", views.action_update_task, name="action_update_task"),
    path("task/updateguidance/<str:task_id>", views.action_update_task_guidance, name="action_update_task_guidance"),
    path("task/latest_iteration/<str:task_id>", views.view_self_driver_latest_iteration, name="view_self_driver_latest_iteration"),
    path("task/latest-logs/<str:task_id>", views.view_task_latest_iteration_logs, name="view_task_latest_iteration_logs"),
    path("task/phase-state/<str:task_id>", views.view_task_phase_state, name="view_task_phase_state"),
    path("process/kill/<uuid:process_id>", views.action_kill_process, name="action_kill_process"),
    path("task/<str:task_id>", views.view_task, name="view_task"),
    path("task/<slug:tab>/<str:task_id>", views.view_task, name="view_task_tab"),
    
    path("llm/debug/<uuid:llm_request_id>", views.view_llm_request, name="view_llm_request"),
    path("llm/ask/<uuid:llm_request_id>", views.action_llm_debug_ask, name="action_llm_debug_ask"),
    path("llm/compare/<uuid:llm_request_id>", views.action_llm_debug_compare, name="action_llm_debug_compare"),
    
    path("iteration/delete/<uuid:iteration_id>", views.action_delete_iteration, name="action_delete_iteration"),
    path("iteration/rollback/<uuid:iteration_id>", views.action_rollback_iteration, name="action_rollback_iteration"),
    path("iteration/<uuid:iteration_id>/logs", views.view_iteration_logs, name="view_iteration_logs"),
    path("iteration/<slug:tab>/<str:iteration_id>", views.view_self_driver_iteration, name="view_self_driver_iteration_tab"),
    path("iteration/<uuid:iteration_id>", views.view_self_driver_iteration, name="view_self_driver_iteration"),

    path("lesson/toggle/<uuid:lesson_id>", views.action_toggle_lesson_validity, name="action_toggle_lesson_validity"),
    
    path("codefile/<uuid:codefile_id>", views.view_codefile, name="view_codefile"),
    path("api/codefile/<uuid:codefile_id>/content", views.api_codefile_content, name="api_codefile_content"),
]
