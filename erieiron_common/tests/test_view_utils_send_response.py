from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory

from erieiron_common import view_utils
from erieiron_common.enums import LlmModel
from erieiron_common.llm_apis.llm_interface import LlmMessage


def test_get_current_user_uses_local_admin_identity_when_bypass_enabled():
    request = RequestFactory().get("/portfolio/")
    dummy_person = SimpleNamespace(id="person-id")

    with patch(
        "erieiron_common.local_runtime.local_admin_autologin_enabled",
        return_value=True,
    ), patch(
        "erieiron_common.local_runtime.ensure_local_admin_identity",
        return_value=(SimpleNamespace(pk="user-id"), dummy_person),
    ):
        current_user = view_utils.get_current_user(request)

    assert current_user is dummy_person


def test_send_response_normalizes_allowed_back_dest_patterns():
    request = RequestFactory().get("/portfolio/")

    url_patterns = [
        SimpleNamespace(
            lookup_str="webservice.views.view_portfolio",
            pattern=SimpleNamespace(regex=SimpleNamespace(pattern="^portfolio/\\Z")),
        ),
        SimpleNamespace(
            lookup_str="webservice.views.view_business",
            pattern=SimpleNamespace(regex=SimpleNamespace(pattern="^business/<uuid:business_id>\\Z")),
        ),
        SimpleNamespace(
            lookup_str="webservice.views.action_update_business",
            pattern=SimpleNamespace(regex=SimpleNamespace(pattern="^_business/update/\\Z")),
        ),
    ]

    with patch("erieiron_config.urls.urlpatterns", url_patterns), patch(
        "erieiron_common.view_utils.render",
        return_value=HttpResponse("ok"),
    ) as mock_render, patch(
        "erieiron_common.view_utils.csrf.get_token",
        return_value="csrf-token",
    ), patch(
        "erieiron_common.view_utils.get_current_user",
        side_effect=ValueError("anonymous request"),
    ):
        response = view_utils.send_response(request, "portfolio/portfolio_base.html")

    assert response.status_code == 200
    assert response.cookies["csrftoken"].value == "csrf-token"
    _, _, context = mock_render.call_args.args
    assert set(context["allowed_back_dests"]) == {"portfolio", "business"}


def test_request_llm_async_flattens_nested_messages():
    person = SimpleNamespace(id="person-id", email="person@example.com")
    messages = [
        LlmMessage.user("first message"),
        [
            LlmMessage.assistant("second message"),
            None,
        ],
    ]

    with patch(
        "erieiron_common.message_queue.pubsub_manager.PubSubManager.publish",
        return_value="queued-message",
    ) as mock_publish:
        queued_message = view_utils.request_llm_async(
            person=person,
            description="Nested message test",
            messages=messages,
            model=LlmModel.OPENAI_GPT_5_MINI,
        )

    assert queued_message == "queued-message"
    payload = mock_publish.call_args.kwargs["payload"]
    assert payload["messages"] == [
        {"message_type": "user", "text": "first message"},
        {"message_type": "assistant", "text": "second message"},
    ]
