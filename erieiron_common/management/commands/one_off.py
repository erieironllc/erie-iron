import shutil

from django.core.management.base import BaseCommand

import settings
from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.business_level_agents.self_driving_coder import self_driving_coder_agent
from erieiron_common.enums import TaskAssigneeType
from erieiron_common.models import CodeFile, SelfDrivingTask, PubSubMessage, SelfDrivingTaskIteration, SelfDrivingTaskBestIteration, Task, Business


class Command(BaseCommand):
    def handle(self, *args, **options):
        business = Business.objects.get(id="dac393bb-9999-4009-a8e0-3b9c451a27dd")
        # self.reset_initiatives(business)
        self.do_selfdriver(business, 'task_implement_summarizer_service_v1')

    def reset_initiatives(self, business: Business):
        PubSubMessage.objects.all().delete()
        CodeFile.objects.all().delete()
        SelfDrivingTask.objects.all().delete()
        SelfDrivingTaskIteration.objects.all().delete()
        SelfDrivingTaskBestIteration.objects.all().delete()

        business = Business.objects.get(id="dac393bb-9999-4009-a8e0-3b9c451a27dd")

        # business.productinitiative_set.all().delete()
        # product_lead.define_product_initiatives(business.id)

        # for i in business.productinitiative_set.all():
        #     print(i.id, i.description)

        Task.objects.all().delete()
        product_initiative = business.productinitiative_set.get(id="articlesummarizertxt_article_submission_mvp_token")
        eng_lead.define_tasks_for_initiative(product_initiative.id)

        for t in Task.objects.filter(product_initiative__business=business).order_by("created_timestamp"):
            print(t.id, TaskAssigneeType(t.role_assignee).label(), t.description, t.completion_criteria)

    def do_selfdriver(self, business: Business, task_id):
        PubSubMessage.objects.all().delete()
        CodeFile.objects.all().delete()
        SelfDrivingTask.objects.all().delete()
        SelfDrivingTaskIteration.objects.all().delete()
        SelfDrivingTaskBestIteration.objects.all().delete()
        shutil.rmtree(settings.BUSINESS_SANDBOX_ROOTDIR / business.sandbox_dir_name, ignore_errors=True)

        self_driving_coder_agent.execute(task_id=task_id)
