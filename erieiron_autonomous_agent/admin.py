from django.contrib import admin
from erieiron_autonomous_agent.models import (
    BusinessConversation,
    ConversationMessage,
    ConversationChange
)


@admin.register(BusinessConversation)
class BusinessConversationAdmin(admin.ModelAdmin):
    list_display = ['id', 'business', 'title', 'status', 'created_at', 'updated_at']
    list_filter = ['status', 'created_at']
    search_fields = ['business__name', 'title']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['business', 'initiative']


@admin.register(ConversationMessage)
class ConversationMessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'role', 'content_preview', 'created_at']
    list_filter = ['role', 'created_at']
    search_fields = ['content']
    readonly_fields = ['created_at']
    raw_id_fields = ['conversation', 'llm_request']

    def content_preview(self, obj):
        return obj.content[:100] + '...' if len(obj.content) > 100 else obj.content


@admin.register(ConversationChange)
class ConversationChangeAdmin(admin.ModelAdmin):
    list_display = ['id', 'conversation', 'change_type', 'approved', 'applied', 'created_at']
    list_filter = ['change_type', 'approved', 'applied', 'created_at']
    readonly_fields = ['created_at', 'approved_at', 'applied_at']
    raw_id_fields = ['conversation', 'message', 'resulting_tasks']
