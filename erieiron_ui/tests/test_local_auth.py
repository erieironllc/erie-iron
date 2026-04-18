import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory

from erieiron_ui import views


def test_view_login_renders_local_auth_context(monkeypatch):
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_EMAIL", "local-admin@erieiron.local")
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_PASSWORD", "local-password")
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_NAME", "Local Admin")

    request = RequestFactory().get("/login/", {"next": "/portfolio/"})
    request.user = AnonymousUser()
    request.session = {}

    with patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")) as mock_send_response:
        response = views.view_login(request)

    assert response.status_code == 200
    mock_send_response.assert_called_once()
    _, _, context = mock_send_response.call_args.args
    assert context["local_auth_enabled"] is True
    assert context["local_auth_email"] == views.settings.LOCAL_AUTH_EMAIL
    assert context["next_param"] == "/portfolio/"


def test_view_login_local_auth_success_redirects_without_db(monkeypatch):
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_ENABLED", True)
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_EMAIL", "local-admin@erieiron.local")
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_PASSWORD", "local-password")
    monkeypatch.setattr(views.settings, "LOCAL_AUTH_NAME", "Local Admin")

    request = RequestFactory().post(
        "/login/",
        {
            "email": views.settings.LOCAL_AUTH_EMAIL,
            "password": views.settings.LOCAL_AUTH_PASSWORD,
            "next": "/portfolio/",
        },
    )
    request.user = AnonymousUser()
    request.session = {}

    dummy_user = SimpleNamespace()
    dummy_person = SimpleNamespace(id=uuid.uuid4())

    with patch(
        "erieiron_common.local_runtime.ensure_local_auth_identity",
        return_value=(dummy_user, dummy_person),
    ), patch("django.contrib.auth.login") as mock_login:
        response = views.view_login(request)

    assert response.status_code == 302
    assert response["Location"] == "/portfolio/"
    assert request.session["person_id"] == str(dummy_person.id)
    mock_login.assert_called_once_with(
        request,
        dummy_user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
