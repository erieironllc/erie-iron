from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.view_portfolio, name="view_home"),
    path("login/", views.view_login, name="view_login"),
    path("logout/", views.action_logout, name="action_logout"),
    path("health/", views.healthcheck, name="health"),
    
    path("portfolio/", views.view_portfolio, name="view_portfolio"),
    path("portfolio/<slug:tab>/", views.view_portfolio, name="view_portfolio_tab"),
    
    path("_business/add", views.action_add_business, name="action_add_business"),
    path("_business/find", views.action_find_business, name="action_find_business"),
    path("_business/green_light/<uuid:business_id>", views.action_business_green_light, name="action_business_green_light"),
    path("_business/update/<uuid:business_id>", views.action_update_business, name="action_update_business"),
    path("_business/bootstrap/<uuid:business_id>", views.action_bootstrap_business, name="action_bootstrap_business"),
    path("_business/newdomain/<uuid:business_id>", views.action_business_new_domain, name="action_business_new_domain"),
    path("_business/regenerate_architecture/<uuid:business_id>", views.action_business_regenerate_architecture, name="action_business_regenerate_architecture"),
    path("_business/product-initiatives/add/<uuid:business_id>", views.action_add_initiative_from_brief, name="action_add_initiative_from_brief"),
    path("_business/production_push/<uuid:business_id>", views.action_business_production_push, name="action_business_production_push"),
    path("_business/delete/<uuid:business_id>", views.action_delete_business, name="action_delete_business"),
    path("_business/submit_bug_report/<uuid:business_id>", views.action_submit_bug_report, name="action_submit_bug_report"),
    path("_initiative/submit_bug_report/<str:initiative_id>", views.action_submit_bug_report_initiative, name="action_submit_bug_report_initiative"),
    path("_initiative/submit_task/<str:initiative_id>", views.action_submit_initiative_task, name="action_submit_initiative_task"),
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

    path("_task/resolve/<str:task_id>", views.action_resolve_task, name="action_resolve_task"),
    path("_task/retry/<str:task_id>", views.action_retry_task, name="action_retry_task"),
    path("_task/regen_test/<str:task_id>", views.action_task_regenerate_test, name="action_task_regenerate_test"),
    path("_task/restart/<str:task_id>", views.action_restart_task, name="action_restart_task"),
    path("_task/delete/<str:task_id>", views.action_delete_task, name="action_delete_task"),
    path("_task/update/<str:task_id>", views.action_update_task, name="action_update_task"),
    path("_task/updateguidance/<str:task_id>", views.action_update_task_guidance, name="action_update_task_guidance"),
    path("_task/debug-assistance/<str:task_id>", views.action_debug_assistance, name="action_debug_assistance"),
    path("task/latest_iteration/<str:task_id>", views.view_self_driver_latest_iteration, name="view_self_driver_latest_iteration"),
    path("task/latest-logs/<str:task_id>", views.view_task_latest_iteration_logs, name="view_task_latest_iteration_logs"),
    path("task/phase-state/<str:task_id>", views.view_task_phase_state, name="view_task_phase_state"),
    path("_process/kill/<uuid:process_id>", views.action_kill_process, name="action_kill_process"),
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
    
    path("pubsub/message/<uuid:message_id>", views.view_pubsub_message_details, name="view_pubsub_message_details"),
    
    path("_pubsub/fetch_messages", views.fetch_pubsub_messages, name="fetch_pubsub_messages"),
    path("_pubsub/delete/<uuid:message_id>", views.action_delete_pubsub_message, name="action_delete_pubsub_message"),
    path("_pubsub/retry/<uuid:message_id>", views.action_retry_pubsub_message, name="action_retry_pubsub_message"),
    
    path("codefile/<uuid:codefile_id>", views.view_codefile, name="view_codefile"),
    path("stack/<uuid:stack_id>/destroy", views.action_destroy_stack, name="action_destroy_stack"),
    path("stack/<uuid:stack_id>", views.view_stack, name="view_stack"),
    path("api/codefile/<uuid:codefile_id>/content", views.api_codefile_content, name="api_codefile_content"),
    path(
        "api/business/<uuid:business_id>/cloud-accounts",
        views.api_business_cloud_accounts,
        name="api_business_cloud_accounts",
    ),
    path(
        "api/business/<uuid:business_id>/cloud-accounts/create",
        views.api_business_cloud_account_create,
        name="api_business_cloud_account_create",
    ),
    path(
        "api/business/<uuid:business_id>/cloud-accounts/<uuid:account_id>",
        views.api_business_cloud_account_update,
        name="api_business_cloud_account_update",
    ),
    path(
        "api/business/<uuid:business_id>/cloud-accounts/<uuid:account_id>/delete",
        views.api_business_cloud_account_delete,
        name="api_business_cloud_account_delete",
    ),
]
