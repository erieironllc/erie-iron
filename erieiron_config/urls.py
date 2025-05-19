from django.urls import path

from erieiron_ui import views

urlpatterns = [
    path("", views.hello)
]
