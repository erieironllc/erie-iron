import shutil

from django.core.management.base import BaseCommand

import settings
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.eng_lead import bootstrap_buiness
from erieiron_autonomous_agent.coding_agents import coding_agent
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTask, SelfDrivingTaskIteration, SelfDrivingTaskBestIteration, Task, Business
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import PubSubMessage


class Command(BaseCommand):
    def handle(self, *args, **options):
        bootstrap_buiness("7e482354-23eb-4398-b7f4-dd158c3797be")
        # self.init_ee()
        # business = Business.objects.get(id="dac393bb-9999-4009-a8e0-3b9c451a27dd")
        # self.reset_initiatives(business)
        # self.do_selfdriver(business, 'task_build_dev_runtime_container')

    def init_ee(self):
        Business.get_erie_iron_business()
        pass

    def reset_initiatives(self, business: Business):
        PubSubMessage.objects.all().delete()
        CodeFile.objects.all().delete()
        SelfDrivingTask.objects.all().delete()
        SelfDrivingTaskIteration.objects.all().delete()
        SelfDrivingTaskBestIteration.objects.all().delete()

        business = Business.objects.get(id="dac393bb-9999-4009-a8e0-3b9c451a27dd")

        # business.initiative_set.all().delete()
        # product_lead.define_initiatives(business.id)

        # for i in business.initiative_set.all():
        #     print(i.id, i.description)

        Task.objects.all().delete()
        initiative = business.initiative_set.get(id="articlesummarizertxt_article_submission_mvp_token")
        eng_lead.define_tasks_for_initiative(initiative.id, None)

        for t in Task.objects.all():
            PubSubManager.publish_id(
                PubSubMessageType.TASK_UPDATED,
                t.id
            )

    def do_selfdriver(self, business: Business, task_id):
        PubSubMessage.objects.all().delete()

        Task.objects.get(id=task_id).depends_on.update(
            status=TaskStatus.COMPLETE
        )

        CodeFile.objects.filter(codeversion__task_iteration__task__task__id=task_id).delete()
        SelfDrivingTask.objects.filter(selfdrivingtaskbestiteration__iteration__task__task__id=task_id).delete()
        SelfDrivingTaskIteration.objects.filter(task__task_id=task_id).delete()
        SelfDrivingTaskBestIteration.objects.filter(task__task_id=task_id).delete()

        coding_agent.execute(task_id=task_id)
