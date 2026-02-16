# 2026-02-15 19:11:19
$IMPL: Restored the missing top navigation by allowing top-nav rendering for Cognito and Django-authenticated sessions, not only simple-auth sessions.

Suggested next step: code review to confirm top-nav visibility and breadcrumb behavior across business, initiative, and task pages.


# 2026-02-15 19:01:36
$IMPL: Fixed a login-page UI break caused by invalid JavaScript output by defaulting `allowed_back_dests` to an empty array literal when absent.

Suggested next step: code review to verify anonymous and authenticated pages both render base scripting correctly.


