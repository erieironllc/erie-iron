from typing import Optional

import requests


def get_account_balance_data():
    return MercuryAPI().get_account_balance_data()


class MercuryAPI:
    def __init__(self):
        from erieiron_common import aws_utils
        self.api_token = aws_utils.get_secret("mercury_bank").get("token")
        self.base_url = "https://api.mercury.com/api/v1"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json"
        }

    def get_account_balance_data(self) -> Optional[dict]:
        """Fetch the balance for a specific account ID."""
        url = f"{self.base_url}/accounts"
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching account balance: {e}")
            return None
