from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.view_portfolio, name="view_home"),
    path("login/", views.view_login, name="view_login"),
    path("logout/", views.action_logout, name="action_logout"),
    path("oauth/cognito/callback", views.oauth_cognito_callback, name="oauth_cognito_callback"),
    path("health/", views.healthcheck, name="health"),
    
    path("portfolio/", views.view_portfolio, name="view_portfolio"),
    path("portfolio/<slug:tab>/", views.view_portfolio, name="view_portfolio_tab"),
    path("portfolio/<slug:tab>/<slug:sub_tab>/", views.view_portfolio_with_sub_tab, name="view_portfolio_tab_sub"),
    
    path("_business/add", views.action_add_business, name="action_add_business"),
    path("_business/find", views.action_find_business, name="action_find_business"),
    path("_business/green_light/<uuid:business_id>", views.action_business_green_light, name="action_business_green_light"),
    path("_business/update/<uuid:business_id>", views.action_update_business, name="action_update_business"),
    path("_business/bootstrap/<uuid:business_id>", views.action_bootstrap_business, name="action_bootstrap_business"),
    path("_business/newdomain/<uuid:business_id>", views.action_business_new_domain, name="action_business_new_domain"),
    path("_business/define_architecture/<uuid:business_id>", views.action_business_define_architecture, name="action_business_define_architecture"),
    path("_business/regenerate_architecture/<uuid:business_id>", views.action_business_regenerate_architecture, name="action_business_regenerate_architecture"),
    path("_business/define_ui_design_spec/<uuid:business_id>", views.action_business_define_ui_design_spec, name="action_business_define_ui_design_spec"),
    path("_business/product-initiatives/add/<uuid:business_id>", views.action_add_initiative_from_brief, name="action_add_initiative_from_brief"),
    path("_business/production_push/<uuid:business_id>", views.action_business_production_push, name="action_business_production_push"),
    path("_business/delete/<uuid:business_id>", views.action_delete_business, name="action_delete_business"),
    path("_business/export_pitch_deck/<uuid:business_id>", views.action_export_pitch_deck, name="action_export_pitch_deck"),
    path("_business/submit_bug_report/<uuid:business_id>", views.action_submit_bug_report, name="action_submit_bug_report"),
    path("_initiative/submit_bug_report/<str:initiative_id>", views.action_submit_bug_report_initiative, name="action_submit_bug_report_initiative"),
    path("_initiative/submit_bug_report_completion", views.action_submit_bug_report_initiative_completion, name="action_submit_bug_report_initiative_completion"),
    path("_initiative/submit_task/<str:initiative_id>", views.action_submit_initiative_task, name="action_submit_initiative_task"),
    path("business/<uuid:business_id>", views.view_business, name="view_business"),
    path("business/<slug:tab>/<uuid:business_id>", views.view_business, name="view_business_tab"),
    path("business/<slug:tab>/<slug:sub_tab>/<uuid:business_id>", views.view_business_with_sub_tab, name="view_business_tab_sub"),
    
    path("initiative/<str:initiative_id>", views.view_initiative, name="view_initiative"),
    path("initiative/<slug:tab>/<str:initiative_id>", views.view_initiative, name="view_initiative_tab"),
    path("initiative/<slug:tab>/<slug:sub_tab>/<str:initiative_id>", views.view_initiative_with_sub_tab, name="view_initiative_tab_sub"),
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
    path("task/<slug:tab>/<slug:sub_tab>/<str:task_id>", views.view_task_with_sub_tab, name="view_task_tab_sub"),
    
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
    path("message-processor/<str:instance_id>", views.view_message_processor_details, name="view_message_processor_details"),
    
    path("_pubsub/fetch_messages", views.fetch_pubsub_messages, name="fetch_pubsub_messages"),
    path("_message_processors/fetch", views.fetch_message_processors, name="fetch_message_processors"),
    path("_llmrequests/fetch/<str:scope>/<str:entity_id>", views.fetch_llm_requests, name="fetch_llm_requests"),
    path("_pubsub/delete/<uuid:message_id>", views.action_delete_pubsub_message, name="action_delete_pubsub_message"),
    path("_pubsub/retry/<uuid:message_id>", views.action_retry_pubsub_message, name="action_retry_pubsub_message"),
    
    path("codefile/<uuid:codefile_id>", views.view_codefile, name="view_codefile"),
    path("stack/<uuid:stack_id>/destroy", views.action_destroy_stack, name="action_destroy_stack"),
    path("stack/<uuid:stack_id>", views.view_stack, name="view_stack"),
    path("api/codefile/<uuid:codefile_id>/content", views.api_codefile_content, name="api_codefile_content"),
    path("api/pubsub/publish/", views.api_pubsub_publish, name="api_pubsub_publish"),
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
    
    # Business Conversations
    path('api/business/<uuid:business_id>/conversations/', views.business_conversations_list, name='business_conversations_list'),
    path('api/business/<uuid:business_id>/conversations/create/', views.business_conversation_create, name='business_conversation_create'),
    path('api/conversation/<uuid:conversation_id>/', views.business_conversation_detail, name='business_conversation_detail'),
    path('api/conversation/<uuid:conversation_id>/delete/', views.business_conversation_delete, name='business_conversation_delete'),
    path('api/conversation/<uuid:conversation_id>/message/', views.business_conversation_message, name='business_conversation_message'),
    path('api/conversation/<uuid:conversation_id>/changes/', views.conversation_changes_list, name='conversation_changes_list'),
    path('api/conversation/change/<uuid:change_id>/approve/', views.conversation_change_approve, name='conversation_change_approve'),
    path('api/conversation/change/<uuid:change_id>/decline/', views.conversation_change_decline, name='conversation_change_decline'),
    
    # Business credential management
    path('business/<uuid:business_id>/credentials/', views.business_credentials_list, name='business_credentials_list'),
    path('business/<uuid:business_id>/credentials/<str:credential_service_name>/secret/', views.business_credential_secret_get, name='business_credential_secret_get'),
    path('business/<uuid:business_id>/credentials/<str:credential_service_name>/secret/update/', views.business_credential_secret_update, name='business_credential_secret_update'),
    path('business/<uuid:business_id>/credentials/update/', views.business_credentials_update, name='business_credentials_update'),
    path('business/<uuid:business_id>/credentials/delete/', views.business_credentials_delete, name='business_credentials_delete'),
    
    # Stack credential management
    path('stack/<uuid:stack_id>/credentials/', views.stack_credentials_list, name='stack_credentials_list'),
    path('stack/<uuid:stack_id>/credentials/<str:credential_service_name>/secret/', views.stack_credential_secret_get, name='stack_credential_secret_get'),
    path('stack/<uuid:stack_id>/credentials/<str:credential_service_name>/secret/update/', views.stack_credential_secret_update, name='stack_credential_secret_update'),
    path('stack/<uuid:stack_id>/credentials/update/', views.stack_credentials_update, name='stack_credentials_update'),
    path('stack/<uuid:stack_id>/credentials/delete/', views.stack_credentials_delete, name='stack_credentials_delete'),
]
