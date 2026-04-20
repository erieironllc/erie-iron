import os
import sys

from playwright.sync_api import expect


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from create_test_users import generate_test_email, login_user_via_ui


def test_local_login_and_root_chat_smoke(page, base_url):
    email = generate_test_email()
    login_user_via_ui(page, base_url, email)

    page.goto(f"{base_url}/")
    expect(page.locator("#root-chat-root")).to_be_visible()
    expect(page.get_by_role("button", name="New chat")).to_be_visible()
    expect(page.locator("[data-role='message-input']")).to_be_visible()

    conversation_links = page.locator("[data-role='conversation-link']")
    existing_count = conversation_links.count()

    page.get_by_role("button", name="New chat").click()

    expect(conversation_links).to_have_count(existing_count + 1)
    expect(page.locator("[data-role='current-conversation-title']")).to_have_text("Erie Iron Operations")
    expect(page.get_by_role("button", name="Delete chat")).to_be_enabled()

    message_input = page.locator("[data-role='message-input']")
    user_messages = page.locator(".conversation-message.message-user")
    assistant_messages = page.locator(".conversation-message.message-assistant")
    prompt = "Please confirm the root chat is working."

    initial_user_count = user_messages.count()
    initial_assistant_count = assistant_messages.count()

    message_input.fill(prompt)
    message_input.press("Enter")

    expect(user_messages).to_have_count(initial_user_count + 1)
    expect(user_messages.last.locator(".message-content")).to_contain_text(prompt)
    expect(assistant_messages).to_have_count(initial_assistant_count + 1, timeout=30_000)
    expect(page.locator("[data-role='loading-indicator']")).to_have_count(0, timeout=30_000)
    assert assistant_messages.last.locator(".message-content").inner_text().strip()
