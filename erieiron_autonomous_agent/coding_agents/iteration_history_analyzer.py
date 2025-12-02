import logging
from typing import List, Dict, Optional, Set
from collections import Counter

from erieiron_autonomous_agent.models import SelfDrivingTaskIteration
from erieiron_common import common


# Hardcoded limit - make configurable later only if needed
MAX_HISTORY_ITERATIONS = 10
MAX_HISTORY_TOKENS_ESTIMATE = 2000  # ~200 tokens per iteration


class IterationHistoryAnalyzer:
    """Analyzes iteration history to prevent repeated mistakes."""

    def __init__(self, current_iteration: SelfDrivingTaskIteration):
        self.current_iteration = current_iteration
        self.iteration_chain = self._build_iteration_chain()

    def _build_iteration_chain(self) -> List[SelfDrivingTaskIteration]:
        """
        Build iteration chain respecting start_iteration boundaries.

        Logic:
        1. Walk backwards from current via get_previous_iteration_with_eval()
        2. Stop if we hit MAX_HISTORY_ITERATIONS
        3. If current has start_iteration set, only include iterations from that point forward
           (iterations before a restart are from different problem context)
        4. Return chronologically ordered list (oldest first)
        """
        chain = []
        current = self.current_iteration

        # Determine restart boundary if any
        restart_iteration_id = None
        if current.start_iteration_id:
            restart_iteration_id = current.start_iteration_id

        # Walk backwards collecting iterations
        while current and len(chain) < MAX_HISTORY_ITERATIONS:
            prev = current.get_previous_iteration_with_eval()
            if not prev:
                break

            # Stop at restart boundary
            if restart_iteration_id and prev.id == restart_iteration_id:
                chain.append(prev)
                break

            chain.append(prev)
            current = prev

        # Return chronological order (oldest first)
        chain.reverse()
        return chain

    def extract_failure_signature(self, iteration: SelfDrivingTaskIteration) -> Dict:
        """
        Extract structured failure data from an iteration.

        Returns dict with:
        - iteration_version: int
        - error_type: str (test_failure, build_error, deployment_error, runtime_error)
        - error_summary: str (high level description)
        - test_failures: list[str] (test names that failed)
        - files_changed: list[str] (files targeted in this iteration)
        - error_messages: list[str] (key error messages, truncated)
        """
        evaluation = iteration.evaluation_json or {}
        planning = iteration.planning_json or {}

        signature = {
            "iteration_version": iteration.version_number,
            "error_type": None,
            "error_summary": None,
            "test_failures": [],
            "files_changed": [],
            "error_messages": []
        }

        # Extract error summary
        if "error" in evaluation:
            error_info = evaluation["error"]
            if isinstance(error_info, dict):
                signature["error_summary"] = error_info.get("summary", "Unknown error")
            else:
                signature["error_summary"] = str(error_info)[:200]
        else:
            # Check evaluation list
            eval_items = evaluation.get("evaluation", [])
            if eval_items:
                first_eval = eval_items[0]
                signature["error_summary"] = first_eval.get("summary", "Unknown error")

        # Extract test failures
        test_errors = evaluation.get("test_errors", [])
        if test_errors:
            signature["error_type"] = "test_failure"
            for test_error in test_errors[:5]:  # Limit to 5 tests
                if isinstance(test_error, dict):
                    test_name = test_error.get("test", test_error.get("name", "unknown"))
                    signature["test_failures"].append(test_name)
                    error_msg = test_error.get("error", test_error.get("message", ""))
                    if error_msg:
                        signature["error_messages"].append(str(error_msg)[:100])
        elif "deployment" in str(signature.get("error_summary", "")).lower():
            signature["error_type"] = "deployment_error"
        elif "build" in str(signature.get("error_summary", "")).lower():
            signature["error_type"] = "build_error"
        else:
            signature["error_type"] = "runtime_error"

        # Extract files changed
        code_files = planning.get("code_files", [])
        for entry in code_files[:10]:  # Limit to 10 files
            if isinstance(entry, dict):
                filepath = entry.get("code_file_path")
                if filepath:
                    signature["files_changed"].append(filepath)

        return signature

    def find_recurring_errors(self) -> List[Dict]:
        """
        Find errors that appear in 2+ iterations.

        Returns list of dicts with:
        - error_signature: str (the recurring error pattern)
        - iterations: list[int] (version numbers where it appeared)
        """
        # Extract all error messages and test failures
        error_counter = Counter()
        error_to_iterations = {}

        for iteration in self.iteration_chain:
            sig = self.extract_failure_signature(iteration)

            # Track test failures
            for test_name in sig["test_failures"]:
                error_counter[test_name] += 1
                if test_name not in error_to_iterations:
                    error_to_iterations[test_name] = []
                error_to_iterations[test_name].append(sig["iteration_version"])

            # Track error summaries (first 50 chars as signature)
            if sig["error_summary"]:
                error_key = sig["error_summary"][:50]
                error_counter[error_key] += 1
                if error_key not in error_to_iterations:
                    error_to_iterations[error_key] = []
                error_to_iterations[error_key].append(sig["iteration_version"])

        # Return errors that occurred 2+ times
        recurring = []
        for error_sig, count in error_counter.items():
            if count >= 2:
                recurring.append({
                    "error_signature": error_sig,
                    "iterations": sorted(error_to_iterations[error_sig]),
                    "count": count
                })

        # Sort by count descending
        recurring.sort(key=lambda x: x["count"], reverse=True)
        return recurring

    def generate_history_summary(self) -> str:
        """
        Generate markdown summary for LLM consumption.

        Format:
        1. Overview with iteration count
        2. Chronological timeline of iterations
        3. Recurring errors section (if any)
        4. Files with repeated issues
        5. Guidance on avoiding repeated mistakes
        """
        if not self.iteration_chain:
            return "# Iteration History\n\nThis is the first iteration - no previous history."

        lines = [
            "# Iteration History",
            "",
            f"**Total previous iterations:** {len(self.iteration_chain)}",
            "",
            "## Timeline",
            ""
        ]

        # Build timeline
        files_with_issues = Counter()

        for iteration in self.iteration_chain:
            sig = self.extract_failure_signature(iteration)

            lines.append(f"### Iteration v{sig['iteration_version']}")
            lines.append(f"**Error type:** {sig['error_type'] or 'unknown'}")

            if sig['error_summary']:
                lines.append(f"**Summary:** {sig['error_summary']}")

            if sig['test_failures']:
                lines.append(f"**Failed tests:** {', '.join(sig['test_failures'][:3])}")
                if len(sig['test_failures']) > 3:
                    lines.append(f"  ...and {len(sig['test_failures']) - 3} more")

            if sig['files_changed']:
                lines.append(f"**Files modified:** {', '.join(sig['files_changed'][:5])}")
                if len(sig['files_changed']) > 5:
                    lines.append(f"  ...and {len(sig['files_changed']) - 5} more")

                # Track files with issues
                for filepath in sig['files_changed']:
                    files_with_issues[filepath] += 1

            lines.append("")

        # Add recurring errors section
        recurring = self.find_recurring_errors()
        if recurring:
            lines.append("## ⚠️ RECURRING ERRORS - DO NOT REPEAT")
            lines.append("")
            lines.append("The following errors have occurred multiple times. **You must not repeat these mistakes:**")
            lines.append("")

            for item in recurring[:5]:  # Top 5 recurring errors
                versions = ", ".join(f"v{v}" for v in item['iterations'])
                lines.append(f"- **{item['error_signature']}**")
                lines.append(f"  - Occurred in: {versions} ({item['count']} times)")
                lines.append("")

        # Add files with repeated issues
        problematic_files = [(file, count) for file, count in files_with_issues.items() if count >= 2]
        if problematic_files:
            lines.append("## Files With Repeated Issues")
            lines.append("")
            problematic_files.sort(key=lambda x: x[1], reverse=True)
            for filepath, count in problematic_files[:5]:
                lines.append(f"- `{filepath}` - modified in {count} iterations")
            lines.append("")

        # Add guidance
        lines.append("## Guidance")
        lines.append("")
        lines.append("**Before making changes:**")
        lines.append("1. Review the recurring errors section above")
        lines.append("2. If an error recurred after being fixed, understand why the fix broke")
        lines.append("3. Do not repeat approaches that failed in multiple iterations")
        lines.append("4. When modifying problematic files, preserve working fixes")
        lines.append("")

        return "\n".join(lines)
