from unittest.mock import patch

from erieiron_common import view_utils


def test_get_cognito_config_accepts_camel_case_keys():
    with patch(
        "erieiron_common.view_utils.agent_tools.get_cognito_config",
        return_value={
            "domain": "https://example.auth.us-west-2.amazoncognito.com",
            "clientId": "client-id",
            "userPoolId": "user-pool-id",
        },
    ):
        config = view_utils.get_cognito_config()

    assert config["domain"] == "https://example.auth.us-west-2.amazoncognito.com"
    assert config["client_id"] == "client-id"
    assert config["user_pool_id"] == "user-pool-id"
