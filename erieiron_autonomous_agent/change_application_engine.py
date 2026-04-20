import logging
from typing import Optional
from django.utils import timezone
from django.db import transaction

from erieiron_autonomous_agent.models import (
    ConversationChange,
    Business,
    Initiative,
    Task,
    LlmRequest,
    InfrastructureStack,
    ConversationMessage,
)
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_common.enums import InitiativeType, LlmModel, LlmVerbosity, TaskType
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_ui import mutation_helpers

logger = logging.getLogger(__name__)


class ChangeApplicationEngine:
    """Applies approved conversation changes to business entities"""

    def __init__(self, change: ConversationChange):
        self.change = change
        self.business = change.conversation.business

    @classmethod
    def apply_approved_change(cls, change: ConversationChange):
        """Main entry point for applying a change"""
        if not change.approved:
            raise ValueError(f"Change {change.id} not approved")

        if change.applied:
            logger.warning(f"Change {change.id} already applied")
            return

        engine = cls(change)

        with transaction.atomic():
            if change.change_type == 'business_plan':
                engine.apply_business_plan_change()
            elif change.change_type == 'architecture':
                engine.apply_architecture_change()
            elif change.change_type == 'initiative':
                engine.apply_initiative_change()
            elif change.change_type == 'task':
                engine.apply_task_change()
            elif change.change_type == 'workflow':
                engine.apply_workflow_change()
            elif change.change_type == 'infrastructure':
                engine.apply_infrastructure_change()
            else:
                raise ValueError(f"Unknown change type: {change.change_type}")

            change.applied = True
            change.applied_at = timezone.now()
            change.save()
            engine.record_application_message()

        logger.info(f"Successfully applied change {change.id}")

    def apply_business_plan_change(self):
        """Update business plan based on change details"""
        details = self.change.change_details
        section = details.get('section')
        modification_type = details.get('modification_type')
        new_content = details.get('new_content')
        reason = details.get('reason', '')

        # Get current business plan
        current_plan = self.business.business_plan or ""

        # Use LLM to intelligently update the plan
        updated_plan = self._regenerate_business_plan(
            current_plan=current_plan,
            section=section,
            modification_type=modification_type,
            new_content=new_content,
            reason=reason
        )

        # Update business
        self.business.business_plan = updated_plan
        self.business.save()

        logger.info(f"Updated business plan for {self.business.name}")

    def _regenerate_business_plan(self, current_plan: str, section: str,
                                  modification_type: str, new_content: str,
                                  reason: str) -> str:
        """Use LLM to regenerate business plan with changes"""
        messages = [
            LlmMessage.sys("""
                You are updating a business plan based on approved changes from a conversation.

                Your task:
                1. Take the current business plan
                2. Apply the specified modification to the specified section
                3. Ensure the updated plan remains coherent and consistent
                4. Maintain the existing structure and format
                5. Return ONLY the updated business plan text (no explanations)
            """),
            LlmMessage.user_from_data("Current Business Plan", {"plan": current_plan}),
            LlmMessage.user_from_data("Change to Apply", {
                "section": section,
                "modification_type": modification_type,
                "new_content": new_content,
                "reason": reason
            }),
            LlmMessage.user("Generate the updated business plan:")
        ]

        response = system_agent_llm_interface.llm_chat(
            description="Regenerate business plan with approved changes",
            messages=messages,
            tag_entity=self.business,
            model=LlmModel.ANTHROPIC_CLAUDE_SONNET_4_5,
            verbosity=LlmVerbosity.MEDIUM
        )

        # LLM request is automatically created by llm_chat

        return response.text.strip()

    def apply_architecture_change(self):
        """Update architecture based on change details"""
        details = self.change.change_details
        component = details.get('component')
        modification_type = details.get('modification_type')
        description = details.get('description')
        integration_points = details.get('integration_points', [])
        required_credentials = details.get('required_credentials', [])

        # Get current architecture
        current_arch = self.business.architecture or ""

        # Use LLM to update architecture
        updated_arch = self._regenerate_architecture(
            current_arch=current_arch,
            component=component,
            modification_type=modification_type,
            description=description,
            integration_points=integration_points
        )

        # Update business
        self.business.architecture = updated_arch

        # Update required credentials if any added
        if required_credentials and modification_type == 'add':
            current_creds = self.business.required_credentials or {}
            for cred in required_credentials:
                if cred not in current_creds:
                    current_creds[cred] = {"added_from_conversation": True}
            self.business.required_credentials = current_creds

        self.business.save()

        # Create initiative to implement this architectural change
        initiative = self._create_implementation_initiative(
            name=f"Implement {component}",
            description=description
        )

        # Link initiative to change
        self.change.resulting_tasks.set([])  # Clear any existing
        logger.info(f"Updated architecture and created initiative {initiative.id}")

    def _regenerate_architecture(self, current_arch: str, component: str,
                                 modification_type: str, description: str,
                                 integration_points: list[str]) -> str:
        """Use LLM to regenerate architecture with changes"""
        messages = [
            LlmMessage.sys("""
                You are updating a technical architecture document based on approved changes.

                Your task:
                1. Take the current architecture
                2. Apply the specified architectural modification
                3. Ensure integration points are documented
                4. Maintain consistency with existing components
                5. Return ONLY the updated architecture document (no explanations)
            """),
            LlmMessage.user_from_data("Current Architecture", {"architecture": current_arch}),
            LlmMessage.user_from_data("Change to Apply", {
                "component": component,
                "modification_type": modification_type,
                "description": description,
                "integration_points": integration_points
            }),
            LlmMessage.user("Generate the updated architecture document:")
        ]

        response = system_agent_llm_interface.llm_chat(
            description="Regenerate architecture with approved changes",
            messages=messages,
            tag_entity=self.business,
            model=LlmModel.ANTHROPIC_CLAUDE_SONNET_4_5,
            verbosity=LlmVerbosity.MEDIUM
        )

        # LLM request is automatically created by llm_chat

        return response.text.strip()

    def apply_initiative_change(self):
        """Create a new initiative based on change details"""
        details = self.change.change_details

        initiative = Initiative.objects.create(
            business=self.business,
            name=details['name'],
            description=details['description'],
            type=details.get('type', InitiativeType.FEATURE.value),
            status='NOT_STARTED',
            priority=details.get('priority', 'medium')
        )

        logger.info(f"Created new initiative {initiative.id}: {initiative.name}")

        # Create placeholder tasks if estimated
        estimated_tasks = details.get('estimated_tasks', 0)
        if estimated_tasks > 0:
            for i in range(estimated_tasks):
                Task.objects.create(
                    business=self.business,
                    initiative=initiative,
                    name=f"Task {i+1} for {initiative.name}",
                    description="To be defined by engineering lead",
                    status=TaskStatus.NOT_STARTED.value,
                    type=TaskType.FEATURE.value
                )

        self.change.resulting_tasks.set([])

    def apply_task_change(self):
        """Create or update a task based on change details"""
        details = self.change.change_details
        operation = details.get("operation", "create")

        if operation == "update":
            task = Task.objects.select_related("initiative", "initiative__business").get(id=details["task_id"])
            mutation_helpers.update_task_from_data(
                task=task,
                raw_data=details.get("fields") or {},
                partial=True,
            )
            self.change.resulting_tasks.set([task])
            return

        initiative = Initiative.objects.select_related("business").get(id=details["initiative_id"])
        task = mutation_helpers.create_task_from_structured_data(
            initiative=initiative,
            task_id_token=details.get("task_id"),
            description=details["description"],
            completion_criteria=details.get("completion_criteria"),
            risk_notes=details.get("risk_notes"),
            task_type=details.get("task_type"),
            requires_test=details.get("requires_test"),
            status=details.get("status"),
        )
        self.change.resulting_tasks.set([task])

    def apply_workflow_change(self):
        details = self.change.change_details
        entity_type = details["entity_type"]
        operation = details["operation"]

        if entity_type == "workflow_definition":
            if operation == "delete":
                mutation_helpers.delete_workflow_definition(details["workflow_id"])
                return
            mutation_helpers.save_workflow_definition(
                workflow_id=details.get("workflow_id"),
                name=details["name"],
                description=details.get("description"),
                is_active=details.get("is_active", True),
            )
            return

        if entity_type == "workflow_step":
            if operation == "delete":
                mutation_helpers.delete_workflow_step(details["step_id"])
                return
            mutation_helpers.save_workflow_step(
                workflow_id=details["workflow_id"],
                step_id=details.get("step_id"),
                name=details["name"],
                handler_path=details["handler_path"],
                emits_message_type=details.get("emits_message_type"),
                sort_order=details.get("sort_order"),
            )
            return

        if entity_type == "workflow_trigger":
            if operation == "delete":
                mutation_helpers.delete_workflow_trigger(details["trigger_id"])
                return
            mutation_helpers.save_workflow_trigger(
                workflow_id=details["workflow_id"],
                trigger_id=details.get("trigger_id"),
                target_step_id=details["target_step_id"],
                message_type=details["message_type"],
                sort_order=details.get("sort_order"),
            )
            return

        if entity_type == "workflow_connection":
            if operation == "delete":
                mutation_helpers.delete_workflow_connection(details["connection_id"])
                return
            mutation_helpers.save_workflow_connection(
                workflow_id=details["workflow_id"],
                connection_id=details.get("connection_id"),
                source_step_id=details["source_step_id"],
                target_step_id=details["target_step_id"],
                message_type=details["message_type"],
                sort_order=details.get("sort_order"),
            )
            return

        raise ValueError(f"Unknown workflow entity_type: {entity_type}")

    def apply_infrastructure_change(self):
        """Handle infrastructure changes (usually creates initiative)"""
        details = self.change.change_details

        initiative = self._create_implementation_initiative(
            name=f"Infrastructure: {details.get('name', 'Update')}",
            description=details.get('description', ''),
            init_type=InitiativeType.INFRASTRUCTURE.value
        )

        logger.info(f"Created infrastructure initiative {initiative.id}")

    def _create_implementation_initiative(self, name: str, description: str,
                                         init_type: str = InitiativeType.FEATURE.value) -> Initiative:
        """Helper to create implementation initiative"""
        initiative = Initiative.objects.create(
            business=self.business,
            name=name,
            description=description,
            type=init_type,
            status='NOT_STARTED'
        )

        # Create initial task for engineering lead to break down
        task = Task.objects.create(
            business=self.business,
            initiative=initiative,
            name=f"Plan and implement {name}",
            description=description,
            status=TaskStatus.NOT_STARTED.value,
            type=TaskType.FEATURE.value
        )

        return initiative

    def record_application_message(self):
        ConversationMessage.objects.create(
            conversation=self.change.conversation,
            role="assistant",
            content=self._build_application_summary(),
        )

    def _build_application_summary(self) -> str:
        if self.change.change_type == "workflow":
            details = self.change.change_details
            return (
                f"Approved and applied workflow change: {details['operation']} "
                f"{details['entity_type']}."
            )

        if self.change.change_type == "task":
            details = self.change.change_details
            operation = details.get("operation", "create")
            if operation == "update":
                return f"Approved and applied task update for `{details['task_id']}`."
            task = self.change.resulting_tasks.first()
            task_id = task.id if task else "new task"
            return f"Approved and applied task creation for `{task_id}`."

        return f"Approved and applied change: {self.change.change_description}"
