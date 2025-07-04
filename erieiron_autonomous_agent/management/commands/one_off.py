import shutil

from django.core.management.base import BaseCommand

import settings
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.enums import TaskStatus
from erieiron_autonomous_agent.models import CodeFile, SelfDrivingTask, SelfDrivingTaskIteration, SelfDrivingTaskBestIteration, Task, Business
from erieiron_autonomous_agent.self_driving_coder import self_driving_coder_agent
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import PubSubMessage


class Command(BaseCommand):
    def handle(self, *args, **options):
        self.init_ee()
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
        shutil.rmtree(settings.BUSINESS_SANDBOX_ROOTDIR / business.sandbox_dir_name, ignore_errors=True)

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
        shutil.rmtree(settings.BUSINESS_SANDBOX_ROOTDIR / business.sandbox_dir_name, ignore_errors=True)

        Task.objects.get(id=task_id).depends_on.update(
            status=TaskStatus.COMPLETE
        )

        CodeFile.objects.filter(codeversion__task_iteration__task__task__id=task_id).delete()
        SelfDrivingTask.objects.filter(selfdrivingtaskbestiteration__iteration__task__task__id=task_id).delete()
        SelfDrivingTaskIteration.objects.filter(task__task_id=task_id).delete()
        SelfDrivingTaskBestIteration.objects.filter(task__task_id=task_id).delete()

        self_driving_coder_agent.execute(task_id=task_id)
