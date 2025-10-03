from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.business_level_agents import eng_lead
from erieiron_autonomous_agent.models import Initiative, Business


class Command(BaseCommand):
    def handle(self, *args, **options):
        Business.objects.filter(id="d447156b-5426-4c63-a500-0fad25fc0c9f").delete()
        Business.objects.filter(id="04c687b6-30fa-46bf-992f-7cde50315b1a").delete()
        return 
        business = Business.objects.get(id="09fc9301-f50f-492a-9d9d-93a3b3bf1fad")
        initiative = Initiative.objects.get(id="articleparser_forwarddigest_launch_token")
        business.codefile_set.all().delete()
        # eng_lead.write_business_architecture(business)
        # eng_lead.write_initiative_architecture(initiative)
        initiative.tasks.all().delete()
        eng_lead.define_tasks_for_initiative(initiative.id)

