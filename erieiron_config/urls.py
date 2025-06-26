from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.hello),
    path("tasks", views.view_tasks),
    path("task/<str:task_id>", views.view_task, name="view_task"),
]
