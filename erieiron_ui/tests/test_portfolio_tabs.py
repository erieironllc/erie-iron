from types import SimpleNamespace
from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory

from erieiron_ui import views


def test_view_portfolio_with_sub_tab_builds_tabs_for_erieiron_business():
    request = RequestFactory().get("/portfolio/llmrequests/llmrequests/")
    erieiron_business = SimpleNamespace(
        llmrequest_set=SimpleNamespace(
            exists=lambda: False,
            all=lambda: SimpleNamespace(order_by=lambda *args, **kwargs: []),
        )
    )

    with patch("erieiron_ui.views._build_portfolio_tabs") as mock_build_tabs, \
            patch("erieiron_ui.views.send_response", return_value=HttpResponse("ok")), \
            patch("erieiron_autonomous_agent.models.Business.get_erie_iron_business", return_value=erieiron_business):
        mock_build_tabs.return_value = [
            {"slug": "llmrequests", "available": True}
        ]

        response = views.view_portfolio_with_sub_tab(request, "llmrequests", "llmrequests")

    assert response.status_code == 200
    mock_build_tabs.assert_called_once_with(erieiron_business)
