from erieiron_autonomous_agent.models import WorkflowDefinition
from erieiron_autonomous_agent.application_repo_config import (
    sync_erieiron_application_repo_if_changed,
)
from erieiron_autonomous_agent.workflow_defaults import sync_internal_workflows
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager


@pubsub_workflow
def initialize_database_workflows(pubsub_manager: PubSubManager):
    sync_internal_workflows()
    sync_erieiron_application_repo_if_changed()
    WorkflowDefinition.register_active_workflows(pubsub_manager)
