from enums import PubSubMessageType
from message_queue.pubsub_manager import pubsub_workflow, PubSubManager
from models import Business


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
    pubsub_manager.on(
        PubSubMessageType.EVERY_MINUTE,
        on_every_minute
    )

def on_every_minute():
    pass


def create_business(name, goals, shutdown_plan):
    business = Business.objects.create(name=name, shutdown_plan=shutdown_plan)
    business.goals.set(goals)
    return business

def shutdown_business(business: Business):
    # Execute shutdown plan
    pass