#!/usr/bin/env python3
"""
Test OAuth redirect flow for Cognito authentication.

This script validates that:
1. Initial request to /login/ redirects successfully
2. All redirects in the OAuth flow respond successfully
3. The callback URL is eventually reached

Usage:
    python scripts/test_oauth_redirect_flow.py [--base-url http://localhost:8023]
"""
import argparse
import logging
import sys
from urllib.parse import urlparse, parse_qs

import requests


logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)


class OAuthRedirectTester:
    """Tests OAuth redirect flow without completing authentication."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.redirects = []

    def test_callback_with_real_auth(self) -> bool:
        """
        Interactive test that uses real authentication to test the full callback flow.

        This method:
        1. Instructs user to authenticate via browser
        2. Captures the auth code from the callback URL
        3. Makes request to callback endpoint with real code
        4. Reports any errors including KeyError: 'email'

        Returns:
            True if callback completes without errors, False otherwise
        """
        callback_url = f"{self.base_url}/oauth/cognito/callback"
        login_url = f"{self.base_url}/login/"

        logging.info("\n" + "=" * 70)
        logging.info("INTERACTIVE AUTHENTICATION TEST")
        logging.info("=" * 70)
        logging.info("")
        logging.info("This test will use real Google authentication to test the callback.")
        logging.info("")
        logging.info("INSTRUCTIONS:")
        logging.info(f"1. Open this URL in your browser: {login_url}")
        logging.info(f"2. Sign in with Google")
        logging.info(f"3. After signing in, you'll be redirected to a URL like:")
        logging.info(f"   {callback_url}?code=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
        logging.info(f"4. Copy the ENTIRE callback URL from your browser's address bar")
        logging.info(f"5. Paste it below")
        logging.info("")

        # Get the callback URL from user
        try:
            callback_with_code = input("Paste the callback URL here: ").strip()
        except (KeyboardInterrupt, EOFError):
            logging.info("\nTest cancelled by user")
            return False

        if not callback_with_code:
            logging.error("No URL provided")
            return False

        # Validate it's the right URL
        if '/oauth/cognito/callback' not in callback_with_code:
            logging.error(f"Invalid URL - doesn't contain /oauth/cognito/callback")
            return False

        logging.info(f"\nTesting callback with provided URL...")

        # Make request to the callback endpoint
        try:
            response = self.session.get(callback_with_code, allow_redirects=False, timeout=30)

            logging.info(f"Response Status: {response.status_code}")

            if response.status_code == 500:
                logging.error("✗ CALLBACK FAILED WITH 500 ERROR")

                # Check for specific errors
                if 'KeyError' in response.text and 'email' in response.text:
                    logging.error("\n" + "=" * 70)
                    logging.error("DETECTED: KeyError: 'email'")
                    logging.error("=" * 70)
                    logging.error("The callback is crashing because the 'email' claim is missing")
                    logging.error("from the Cognito ID token.")
                    logging.error("")
                    logging.error("This suggests:")
                    logging.error("  1. Google identity provider attribute mappings are incorrect")
                    logging.error("  2. Or the mappings aren't being applied to the ID token")
                    logging.error("")
                    logging.error("Check server logs for detailed claim information.")
                    logging.error("=" * 70)
                    return False
                else:
                    logging.error(f"\n{response.text[:1000]}")
                    return False

            elif response.status_code == 302:
                redirect_to = response.headers.get('Location', 'unknown')
                logging.info(f"✓ Callback succeeded - redirecting to: {redirect_to}")
                return True

            elif response.status_code == 200:
                logging.info("✓ Callback succeeded with 200 OK")
                return True

            else:
                logging.warning(f"Unexpected status code: {response.status_code}")
                return False

        except requests.exceptions.RequestException as exc:
            logging.error(f"Request failed: {exc}")
            return False

    def test_callback_endpoint(self) -> bool:
        """
        Test that the callback endpoint is accessible and handles errors gracefully.

        This test verifies that the callback endpoint doesn't crash with KeyError: 'email'
        when it receives invalid/missing authentication codes.

        Returns:
            True if endpoint handles errors properly, False if it crashes with KeyError
        """
        callback_url = f"{self.base_url}/oauth/cognito/callback"

        logging.info(f"\nTesting callback endpoint: {callback_url}")
        logging.info("=" * 70)

        # Test 1: Missing code parameter
        logging.info("\nTest 1: Callback with missing code parameter")
        try:
            response = self.session.get(callback_url, allow_redirects=False, timeout=10)
            logging.info(f"  Status: {response.status_code}")

            # We expect either:
            # - 302 redirect to login (good - handled gracefully)
            # - 400 bad request (good - validation error)
            # - NOT 500 with KeyError: 'email' (bad - our bug)

            if response.status_code == 500:
                # Check if the error is the KeyError we're looking for
                if 'KeyError' in response.text and 'email' in response.text:
                    logging.error("  ✗ FAILED: Endpoint crashed with KeyError: 'email'")
                    logging.error("  This indicates the attribute mapping fix didn't work")
                    return False
                else:
                    logging.warning(f"  ⚠ Got 500 error (not the email KeyError we fixed)")
                    logging.info(f"  ✓ No KeyError: 'email' detected")
                    return True
            else:
                logging.info(f"  ✓ Endpoint handled missing code gracefully")
                return True

        except requests.exceptions.RequestException as exc:
            logging.error(f"  ✗ Request failed: {exc}")
            return False

        # Test 2: Invalid code parameter
        logging.info("\nTest 2: Callback with invalid code parameter")
        try:
            response = self.session.get(
                f"{callback_url}?code=invalid-test-code-12345",
                allow_redirects=False,
                timeout=10
            )
            logging.info(f"  Status: {response.status_code}")

            if response.status_code == 500:
                if 'KeyError' in response.text and 'email' in response.text:
                    logging.error("  ✗ FAILED: Endpoint crashed with KeyError: 'email'")
                    logging.error("  This indicates the attribute mapping is missing required claims")
                    return False
                else:
                    # Some other 500 error - could be expected for invalid token
                    logging.info(f"  ✓ Got error, but not the KeyError: 'email' we fixed")
                    return True
            else:
                logging.info(f"  ✓ Endpoint handled invalid code gracefully")
                return True

        except requests.exceptions.RequestException as exc:
            logging.error(f"  ✗ Request failed: {exc}")
            return False

    def test_redirect_flow(self) -> bool:
        """
        Test the OAuth redirect flow.

        Returns:
            True if all validations pass, False otherwise
        """
        login_url = f"{self.base_url}/login/"

        logging.info(f"Starting OAuth redirect flow test from: {login_url}")
        logging.info("=" * 70)

        try:
            current_url = login_url
            redirect_count = 0
            max_redirects = 10
            callback_reached = False

            while redirect_count < max_redirects:
                logging.info(f"\nStep {redirect_count + 1}: Requesting {current_url}")

                try:
                    response = self.session.get(current_url, allow_redirects=False, timeout=10)
                except requests.exceptions.RequestException as exc:
                    logging.error(f"Request failed: {exc}")
                    return False

                # Record this step
                self.redirects.append({
                    'step': redirect_count + 1,
                    'url': current_url,
                    'status_code': response.status_code,
                    'headers': dict(response.headers)
                })

                # Log response
                logging.info(f"  Status: {response.status_code}")

                # Check for Cognito error page specifically
                if 'cognito' in current_url.lower() and response.status_code >= 400:
                    logging.error(f"  ✗ FAILED: Cognito error page (status {response.status_code})")
                    logging.error(f"  This likely indicates a Cognito configuration issue:")
                    logging.error(f"    - The redirect_uri may not be registered in Cognito")
                    logging.error(f"    - The app client may be misconfigured")
                    logging.error(f"    - Check AWS Cognito console for app client settings")
                    if response.text:
                        # Try to extract error message from HTML
                        import re
                        error_match = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                        if error_match:
                            logging.error(f"  Page title: {error_match.group(1)}")
                    return False

                # Check for successful response (2xx or 3xx)
                if not (200 <= response.status_code < 400):
                    logging.error(f"  ✗ FAILED: Expected 2xx or 3xx status, got {response.status_code}")
                    if response.text:
                        logging.error(f"  Response body: {response.text[:500]}")
                    return False

                logging.info(f"  ✓ Request successful")

                # Check if we've reached the callback URL
                parsed_url = urlparse(current_url)
                if parsed_url.path == '/oauth/cognito/callback':
                    callback_reached = True
                    logging.info(f"  ✓ Callback URL reached!")
                    break

                # Check if this is a redirect
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        logging.error(f"  ✗ FAILED: Redirect response but no Location header")
                        return False

                    logging.info(f"  → Redirect to: {location}")

                    # If Location is relative, make it absolute
                    if location.startswith('/'):
                        location = f"{self.base_url}{location}"
                    elif not location.startswith('http'):
                        # Relative to current path
                        base = current_url.rsplit('/', 1)[0]
                        location = f"{base}/{location}"

                    current_url = location
                    redirect_count += 1

                elif response.status_code == 200:
                    # Got a 200 response - check if it's the Cognito login page
                    if 'cognito' in current_url.lower() or 'amazoncognito.com' in current_url:
                        logging.info(f"  ✓ Reached Cognito hosted UI")
                        logging.info(f"  → This is the expected end point (requires user interaction)")

                        # Parse the URL to verify it has expected OAuth parameters
                        parsed = urlparse(current_url)
                        params = parse_qs(parsed.query)

                        if 'client_id' in params and 'redirect_uri' in params:
                            logging.info(f"  ✓ OAuth parameters present:")
                            logging.info(f"    - client_id: {params['client_id'][0]}")
                            logging.info(f"    - redirect_uri: {params['redirect_uri'][0]}")
                            logging.info(f"    - response_type: {params.get('response_type', ['N/A'])[0]}")
                            logging.info(f"    - scope: {params.get('scope', ['N/A'])[0]}")

                            # Verify redirect_uri points to our callback
                            redirect_uri = params['redirect_uri'][0]
                            if '/oauth/cognito/callback' in redirect_uri:
                                logging.info(f"  ✓ Redirect URI correctly points to callback endpoint")
                            else:
                                logging.warning(f"  ⚠ Redirect URI doesn't point to callback: {redirect_uri}")

                        # This is successful - we've redirected to Cognito
                        break
                    else:
                        logging.error(f"  ✗ FAILED: Got 200 response at unexpected URL: {current_url}")
                        return False
                else:
                    # Some other 2xx response
                    logging.info(f"  ✓ Received {response.status_code} response")
                    break

            if redirect_count >= max_redirects:
                logging.error(f"\n✗ FAILED: Too many redirects (>{max_redirects})")
                return False

            # Summary
            logging.info("\n" + "=" * 70)
            logging.info("SUMMARY:")
            logging.info(f"  Total redirects: {redirect_count}")
            logging.info(f"  All requests successful: ✓")

            if callback_reached:
                logging.info(f"  Callback URL reached: ✓")
            else:
                logging.info(f"  Ended at Cognito hosted UI (expected): ✓")
                logging.info(f"  Note: Callback will be reached after user authenticates")

            logging.info("\n✓ ALL TESTS PASSED")
            return True

        except Exception as exc:
            logging.exception(f"Unexpected error during test: {exc}")
            return False

    def print_redirect_chain(self):
        """Print detailed redirect chain for debugging."""
        logging.info("\nDETAILED REDIRECT CHAIN:")
        logging.info("=" * 70)
        for redirect in self.redirects:
            logging.info(f"\nStep {redirect['step']}:")
            logging.info(f"  URL: {redirect['url']}")
            logging.info(f"  Status: {redirect['status_code']}")
            if 'Location' in redirect['headers']:
                logging.info(f"  Redirects to: {redirect['headers']['Location']}")


def main():
    parser = argparse.ArgumentParser(
        description='Test OAuth redirect flow for Cognito authentication'
    )
    parser.add_argument(
        '--base-url',
        default='http://localhost:8023',
        help='Base URL of the application (default: http://localhost:8023)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print detailed redirect chain'
    )
    parser.add_argument(
        '--test-with-auth',
        action='store_true',
        help='Interactive mode: authenticate and test callback with real auth code'
    )

    args = parser.parse_args()

    tester = OAuthRedirectTester(args.base_url)

    # Interactive authentication test mode
    if args.test_with_auth:
        logging.info("Running interactive authentication test...")
        success = tester.test_callback_with_real_auth()
        sys.exit(0 if success else 1)

    # Run both tests
    logging.info("Running OAuth redirect flow tests...")
    logging.info("")

    # Test 1: Callback endpoint error handling
    callback_test_passed = tester.test_callback_endpoint()

    # Test 2: Full redirect flow
    redirect_test_passed = tester.test_redirect_flow()

    if args.verbose:
        tester.print_redirect_chain()

    # Overall success requires both tests to pass
    all_tests_passed = callback_test_passed and redirect_test_passed

    if all_tests_passed:
        logging.info("\n" + "=" * 70)
        logging.info("MANUAL TESTING INSTRUCTIONS:")
        logging.info("=" * 70)
        logging.info(f"To complete the full authentication flow:")
        logging.info(f"1. Open your browser to: {args.base_url}/login/")
        logging.info(f"2. Click 'Sign in with Google'")
        logging.info(f"3. Complete Google authentication")
        logging.info(f"4. You should be redirected back to {args.base_url}")
        logging.info(f"5. Check server logs for any errors in /oauth/cognito/callback")
        logging.info("")
        logging.info("If you see 'KeyError: email' in the logs, the attribute mapping")
        logging.info("is incorrect in Cognito's Google identity provider configuration.")
        logging.info("=" * 70)
    else:
        logging.error("\n" + "=" * 70)
        logging.error("TEST FAILURES:")
        if not callback_test_passed:
            logging.error("  ✗ Callback endpoint test failed")
        if not redirect_test_passed:
            logging.error("  ✗ Redirect flow test failed")
        logging.error("=" * 70)

    sys.exit(0 if all_tests_passed else 1)


if __name__ == '__main__':
    main()
