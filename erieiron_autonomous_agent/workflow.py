from erieiron_autonomous_agent.models import WorkflowDefinition
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager


@pubsub_workflow
def initialize_database_workflows(pubsub_manager: PubSubManager):
    WorkflowDefinition.register_active_workflows(pubsub_manager)
