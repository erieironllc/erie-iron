import pprint
from collections import defaultdict

from erieiron_common import common


class CodeCompilationError(Exception):
    def __init__(self, code_str, *args):
        super().__init__(*args)
        self.code_str = code_str


class CodeReviewException(Exception):
    def __init__(self, review_data):
        self.bad_plan = review_data.get("plan_quality", []) != "VALID"
        self.review_data = review_data
        super().__init__("Code Review Failed")
    
    def get_issue_dicts(self) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
        return self.get_file_blockers_dict(), self.get_file_warnings_dict()
    
    def get_file_blockers_dict(self) -> dict[str, list[dict]]:
        d = defaultdict(list)
        
        for i in common.ensure_list(self.review_data.get("blocking_issues", [])):
            d[i['file']].append(i)
        
        return d
    
    def get_file_warnings_dict(self) -> dict[str, list[dict]]:
        d = defaultdict(list)
        
        for i in common.ensure_list(self.review_data.get("non_blocking_warnings", [])):
            d[i['file']].append(i)
        
        return d


class GoalAchieved(Exception):
    def __init__(self, planning_data):
        pprint.pprint(planning_data)
        self.planning_data = planning_data


class DatabaseMigrationException(Exception):
    ...


class AgentBlocked(Exception):
    def __init__(self, blocked_data):
        self.blocked_data = blocked_data


class NeedPlan(Exception):
    def __init__(self, msg: str):
        super().__init__(msg)


class FailingTestException(Exception):
    def __init__(self, extracted_exception: str):
        super().__init__(extracted_exception)


class ExecutionException(Exception):
    def __init__(self, extracted_exception: str):
        super().__init__(extracted_exception)


class BadPlan(Exception):
    def __init__(self, msg: str, plan_data: dict = None):
        if not plan_data:
            plan_data = {}
        
        self.plan_data = plan_data
        super().__init__(msg)


class RetryableException(Exception):
    ...
