# Test Planning 

You are developing code in the **Test-Driven-Development style**.  The automated test file is located at `<test_file_path>`. 

## Constraints

- When tests need to send emails, always construct the "FROM" address from the task's DomainName (for example, `noreply@{os.getenv("DOMAIN_NAME")}`) and ensure that identity is verified in SES before the test runs.
- Automated SES smoke or integration tests must target the SES Mailbox Simulator (e.g., `success@simulator.amazonses.com` for happy-path delivery, or other simulator addresses when validating bounces/complaints). Never point tests at real recipients or DomainName inboxes.
- When implementing or modifying tests **Only modify <test_file_path>.** You may not create additional test files under any circumstances.
- If repeated iterations show the test itself is faulty or lacks the logging needed to diagnose failures, update it to correct the issue or add focused diagnostics while keeping the test's spirit intact.
- You may modify or add tests inside `<test_file_path>` only when one of the following conditions is met:
  - The test is clearly making bad assertions that are not aligned with the architecture document (the architecture document is the canonical source for technical information)
  - The test clearly has a bug (e.g., syntax error, incorrect API usage, resource leaks)
  - The test is not aligned with the architecture document requirements
  - Adding focused diagnostics or logging to help identify the root cause of failures
- When modifying tests, you must preserve the original **intent and spirit** of the test logic as defined by Test Driven Development (TDD). This means:
  - Don't remove or neuter failing tests just to make the code pass
  - Don't fake inputs, mock outputs, or bypass test logic to "force" success
  - Focus on making tests pass by editing the application code, not by weakening the tests
  - Do not remove test assertions unless they are clearly redundant, logically invalid, or misaligned with the architecture
- It is acceptable to refactor or extend the test suite for clarity, coverage, or correctness — but only if it helps validate the GOAL more effectively.
- Any test edits must be fully aligned with evaluator feedback and must advance the system toward satisfying the GOAL.
- You must **never remove or replace all test logic** in the file.
  - Only append, modify, or selectively remove specific test methods that are demonstrably invalid based on evaluator diagnostics.
  - If the file structure is completely broken or non-runnable, propose a fix that **restores or scaffolds** the minimum viable test logic while preserving any intact original assertions.
  - Any full-file wipeout or replacement is considered a violation unless explicitly instructed by the evaluator.

Violating these constraints may result in invalid task execution or untrustworthy success signals, and must be avoided.

---

## Test Validation

- All core functionality must be exercised by an automated test implement in '<test_file_path>' to ensure the functionality does not regress.
- Tests should validate end-user observable behavior directly wherever feasible (for example, by sending a request to an endpoint and asserting the response).  
- Assertions based on logs or internal messages should be used only as a **last resort**, when no direct or reliable method exists to confirm expected end behavior.  
- Prefer validating functional outcomes through API responses, database state, or returned data rather than by checking for log text.
- All test classes must subclass `django.test.TestCase` and include the import `from django.test import TestCase`.
- Test methods should be named `test_...` and placed in files discoverable by Django’s test runner.
- Tests must operate only on test data (not production data) and validate all critical feature behavior.
- If a test fails, it should raise an appropriate assertion error. Otherwise, it should pass silently.

---

## Integration and Environment Strategy

- Tests must validate that the full workflow **really works in the deployed stack**. They should assert functional success end-to-end rather than rely on mocked dependencies or isolated components.
- AWS and other integrations should be exercised through the actual deployed environment (for example, real S3 uploads, SES email sends, or RDS queries)
- Tests **must avoid** asserting internal configuration details such as role names, stack identifiers, or region values.
- The objective is to prove that the system’s observable outcomes are correct in a real environment — e.g., data is stored, events are processed, and external effects occur as expected.
- Mocking or stubbing of AWS services is **not allowed**; tests must run against real or stack-provisioned resources that mirror production conditions.
- When instability or eventual consistency may cause flakiness, tests should include bounded retries with jitter and clear diagnostics rather than disabling validation.
- Logging and diagnostics should focus on visibility of functional flow (what succeeded or failed), not on enforcing static configuration expectations.

---

## React/React Native Frontend Testing Strategy

When the task involves React or React Native UI implementation, the test plan **must** include multiple testing layers to validate both the frontend and backend integration.

### Test File Organization for React Projects

React frontend projects require **three distinct test layers**, each in separate directories:

1. **Django Backend Tests**: `<test_file_path>` (Django TestCase in tests/ directory)
   - API endpoint validation (Django REST Framework views)
   - Database model logic and constraints
   - Authentication and permission checks
   - Business logic in Django services

2. **React Component Tests**: `frontend/__tests__/` or `frontend/src/**/*.test.js`
   - Component rendering with various props
   - User interaction handlers (button clicks, form inputs)
   - Component state management
   - Use Jest + React Testing Library
   - **May use mocked API calls** (fetch/axios mocked)

3. **End-to-End Tests** (REQUIRED): `frontend/e2e/` or `frontend/cypress/e2e/`
   - **CRITICAL**: Must exercise full round-trip to real Django server
   - Complete user flows from UI → Django API → Database → Response → UI
   - Use Playwright (recommended) or Cypress
   - **No mocking allowed** - must call real Django endpoints
   - Requires Django test server running (pytest-django live_server or docker-compose)

### Test Balance for React Frontends

When planning tests for React UI tasks, follow this distribution:

- **Unit Tests (50%)**: Component tests + Django model/view tests with mocks
- **Integration Tests (30%)**: Component + mocked API tests + Django API endpoint tests
- **E2E Tests (20%)**: Full-stack tests with Playwright/Cypress hitting real Django server

### Required E2E Test Coverage for React UIs

**Every React frontend implementation must include e2e tests that verify:**

1. ✅ **Authentication Flow**: Login → session cookie → protected routes → logout (real Django auth)
2. ✅ **CRUD Operations**: Create, Read, Update, Delete with real Django API calls
3. ✅ **Full Round-Trip**: UI interaction → API request → database operation → API response → UI update
4. ✅ **Error Handling**: Django validation errors display correctly in React UI
5. ✅ **Loading States**: UI shows loading indicators during real API calls
6. ✅ **Navigation**: Client-side routing works correctly
7. ✅ **Session Management**: Expired sessions redirect to login

### E2E Test Implementation Requirements

**Playwright Example** (required structure):
```javascript
// frontend/e2e/full-flow.spec.js
const { test, expect } = require('@playwright/test');

test.describe('Feature Full Flow', () => {
  test('complete user journey with real Django backend', async ({ page }) => {
    // 1. Login with real Django authentication
    await page.goto('/login');
    await page.fill('input[name="username"]', 'testuser');
    await page.fill('input[name="password"]', 'testpass123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('/dashboard');

    // 2. Create resource via real POST to /api/endpoint
    await page.click('button:has-text("New Item")');
    await page.fill('input[name="name"]', 'Test Item');
    await page.click('button:has-text("Save")');

    // 3. Verify resource appears (real GET from /api/endpoint)
    await expect(page.locator('text=Test Item')).toBeVisible();

    // 4. Verify error handling (real Django validation)
    await page.click('button:has-text("New Item")');
    await page.fill('input[name="name"]', ''); // Invalid
    await page.click('button:has-text("Save")');
    await expect(page.locator('text=required')).toBeVisible();
  });
});
```

**Django Test Server Setup** (must be documented):
```python
# conftest.py or test setup
import pytest
from django.core.management import call_command

@pytest.fixture(scope='session')
def django_db_setup(django_db_setup, django_db_blocker):
    """Setup test database with fixtures for e2e tests"""
    with django_db_blocker.unblock():
        call_command('loaddata', 'test_users.json')
```

### Test Plan Output for React Tasks

When generating test plans for React frontend tasks, specify:

1. **Backend API Tests** (Django TestCase):
   - File: `<test_file_path>` (standard Django test)
   - Test API endpoints return correct data
   - Test authentication and permissions
   - Test database operations

2. **Component Tests** (Jest + React Testing Library):
   - File: `frontend/src/components/__tests__/ComponentName.test.js`
   - Test component rendering with props
   - Test user interactions (mocked API calls allowed)
   - Test state management

3. **E2E Tests** (Playwright - REQUIRED):
   - File: `frontend/e2e/feature-flow.spec.js`
   - Test complete user journey with real Django backend
   - **Must call real Django API** (no mocking)
   - Test authentication, CRUD, error handling, loading states

### Integration with Existing TDD Constraints

For React frontend tests:

- **Django backend tests** (`<test_file_path>`): Follow all existing TDD constraints (no mocking AWS, etc.)
- **Component tests**: May use mocked fetch/axios for unit testing
- **E2E tests**: **Must not mock API calls** - must exercise real Django server

The `<test_file_path>` constraint applies only to Django backend tests. Component and e2e tests live in frontend directory structure.

### Validation Criteria for React Frontend Tests

A React frontend implementation is **not complete** unless:

1. ✅ Django backend API tests pass (standard Django TestCase)
2. ✅ React component tests pass (Jest + React Testing Library)
3. ✅ **E2E tests with Playwright pass** against real Django server
4. ✅ E2E tests cover authentication, CRUD operations, error handling, and loading states
5. ✅ Django test server setup documented (pytest-django or docker-compose)

**If e2e tests are missing or only use mocked APIs, the implementation is incomplete.**

### Anti-Patterns to Avoid

❌ **Do NOT** create only component tests with mocked APIs for React frontend
❌ **Do NOT** skip e2e tests because "component tests pass"
❌ **Do NOT** mock Django API calls in e2e tests
❌ **Do NOT** rely on manual testing instead of automated e2e tests
❌ **Do NOT** test only happy paths - must test error handling and edge cases

✅ **Do** create comprehensive test suite: Django backend + component + e2e
✅ **Do** use Playwright/Cypress for e2e tests with real Django server
✅ **Do** test full user journeys from login to logout
✅ **Do** verify error messages from Django API display in UI
✅ **Do** document Django test server setup (pytest-django live_server)
