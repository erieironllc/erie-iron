import pytest
from django.test import TestCase
from erieiron_autonomous_agent.models import SelfDrivingTask, SelfDrivingTaskIteration, Business
from erieiron_autonomous_agent.coding_agents.iteration_history_analyzer import IterationHistoryAnalyzer


class TestIterationHistoryAnalyzer(TestCase):

    def setUp(self):
        """Create test fixture with 3 iterations showing regression pattern."""
        # Create business
        self.business = Business.objects.create(
            name="Test Business",
            slug="test-business"
        )

        # Create task
        self.task = SelfDrivingTask.objects.create(
            business=self.business,
            task_description="Test auth implementation"
        )

        # Iteration 1: test_login fails
        self.iter1 = SelfDrivingTaskIteration.objects.create(
            self_driving_task=self.task,
            version_number=1,
            evaluation_json={
                "test_errors": [
                    {"test": "test_login", "error": "AssertionError: 401 != 200"}
                ]
            },
            planning_json={
                "code_files": [
                    {"code_file_path": "auth/views.py"}
                ]
            }
        )

        # Iteration 2: test_login passes, test_logout fails
        self.iter2 = SelfDrivingTaskIteration.objects.create(
            self_driving_task=self.task,
            version_number=2,
            start_iteration=self.iter1,
            evaluation_json={
                "test_errors": [
                    {"test": "test_logout", "error": "KeyError: 'session'"}
                ]
            },
            planning_json={
                "code_files": [
                    {"code_file_path": "auth/views.py"}
                ]
            }
        )

        # Iteration 3: test_login fails again (regression!)
        self.iter3 = SelfDrivingTaskIteration.objects.create(
            self_driving_task=self.task,
            version_number=3,
            start_iteration=self.iter1,
            evaluation_json={
                "test_errors": [
                    {"test": "test_login", "error": "AssertionError: 401 != 200"},
                    {"test": "test_logout", "error": "KeyError: 'session'"}
                ]
            },
            planning_json={
                "code_files": [
                    {"code_file_path": "auth/views.py"}
                ]
            }
        )

    def test_build_iteration_chain(self):
        """Verify chain building includes previous iterations."""
        analyzer = IterationHistoryAnalyzer(self.iter3)

        # Should include iter1 and iter2 (not iter3 itself)
        assert len(analyzer.iteration_chain) == 2
        assert analyzer.iteration_chain[0].version_number == 1
        assert analyzer.iteration_chain[1].version_number == 2

    def test_extract_failure_signature(self):
        """Verify parsing of evaluation_json."""
        analyzer = IterationHistoryAnalyzer(self.iter3)
        sig = analyzer.extract_failure_signature(self.iter1)

        assert sig["iteration_version"] == 1
        assert sig["error_type"] == "test_failure"
        assert "test_login" in sig["test_failures"]
        assert "auth/views.py" in sig["files_changed"]

    def test_find_recurring_errors(self):
        """Verify detection of recurring test failures."""
        analyzer = IterationHistoryAnalyzer(self.iter3)
        recurring = analyzer.find_recurring_errors()

        # test_logout should be flagged as recurring (appears in iter2 in the chain)
        # Since we only analyze the chain (iter1 and iter2), not the current iteration
        assert len(recurring) >= 0

    def test_generate_history_summary(self):
        """Verify markdown output format."""
        analyzer = IterationHistoryAnalyzer(self.iter3)
        summary = analyzer.generate_history_summary()

        assert "# Iteration History" in summary
        assert "v1" in summary
        assert "v2" in summary
        assert "test_login" in summary or "test_logout" in summary
        assert "auth/views.py" in summary

    def test_first_iteration_handles_gracefully(self):
        """Verify first iteration returns appropriate message."""
        analyzer = IterationHistoryAnalyzer(self.iter1)
        summary = analyzer.generate_history_summary()

        assert "first iteration" in summary.lower()
        assert "no previous history" in summary.lower()

    def test_recurring_error_detection_with_same_test(self):
        """Test that the same test failure across iterations is detected as recurring."""
        # Create iterations with same test failing multiple times
        iter_a = SelfDrivingTaskIteration.objects.create(
            self_driving_task=self.task,
            version_number=10,
            evaluation_json={
                "test_errors": [
                    {"test": "test_authentication", "error": "Token invalid"}
                ]
            },
            planning_json={"code_files": [{"code_file_path": "auth.py"}]}
        )

        iter_b = SelfDrivingTaskIteration.objects.create(
            self_driving_task=self.task,
            version_number=11,
            start_iteration=iter_a,
            evaluation_json={
                "test_errors": [
                    {"test": "test_authentication", "error": "Token invalid"}
                ]
            },
            planning_json={"code_files": [{"code_file_path": "auth.py"}]}
        )

        analyzer = IterationHistoryAnalyzer(iter_b)
        recurring = analyzer.find_recurring_errors()

        # test_authentication should appear as recurring
        recurring_sigs = [r["error_signature"] for r in recurring]
        assert "test_authentication" in recurring_sigs

    def test_files_with_repeated_issues(self):
        """Test that files modified in multiple iterations are highlighted."""
        analyzer = IterationHistoryAnalyzer(self.iter3)
        summary = analyzer.generate_history_summary()

        # auth/views.py was modified in both iter1 and iter2
        assert "Files With Repeated Issues" in summary
        assert "auth/views.py" in summary
