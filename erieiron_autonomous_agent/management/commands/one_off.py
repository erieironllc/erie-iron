import pprint
import textwrap

from django.core.management.base import BaseCommand

from erieiron_autonomous_agent.board_level_agents import board_chair, corporate_development_agent
from erieiron_autonomous_agent.models import Business, BusinessSecondOpinionEvaluation
from erieiron_common.enums import PubSubMessageType, BusinessNiche
from erieiron_common.message_queue.pubsub_manager import PubSubManager


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        ...
        
        # for business in Business.objects.exclude(id=Business.get_erie_iron_business().id):
        #     PubSubManager.publish(
        #         PubSubMessageType.BUSINESS_SECOND_OPINION_EVALUATION_REQUESTED,
        #         payload=business.id
        #     )
        
        resp = corporate_development_agent.on_portfolio_pick_new_business({
            'guidance': textwrap.dedent("""
            Prioritize for 
            1) simplicty to build 
            2) least amount of human effort to operate 
            3) most confident in having near term profit'
            4) lowest legal risk
            """)
        })
        pprint.pprint(resp)
        
        # for b in Business.objects.filter(niche_category__isnull=False):
        #     b.delete()
        
        # PubSubManager.publish(
        #     PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
        #     payload={
        #         "requested_count": 5,
        #         "user_input": "Prioritize Short Term Profit",
        #         "niche": None
        #     }
        # )
        # for niche in BusinessNiche:
        #     PubSubManager.publish(
        #         PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
        #         payload={
        #             "requested_count": 5,
        #             "user_input": "Prioritize Short Term Profit",
        #             "niche": niche
        #         }
        #     )
