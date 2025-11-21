import pprint
import textwrap

from django.core.management.base import BaseCommand
from tqdm import tqdm

from erieiron_autonomous_agent.board_level_agents import corporate_development_agent, board_analyst
from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business, BusinessAnalysis, BusinessLegalAnalysis, BusinessSecondOpinionEvaluation
from erieiron_common.enums import PubSubMessageType, BusinessNiche
from erieiron_common.message_queue.pubsub_manager import PubSubManager


class Command(BaseCommand):
    def handle(self, env_type=None, *args, **options):
        # self.create_businesses()
        # 
        # for business in tqdm(Business.get_portfolio_business()):
        #     BusinessAnalysis.objects.filter(id=business.id).delete()
        #     BusinessLegalAnalysis.objects.filter(id=business.id).delete()
        #     BusinessSecondOpinionEvaluation.objects.filter(id=business.id).delete()
        #     board_analyst.on_analysis_requested(business.id)
        # 
        resp = corporate_development_agent.on_portfolio_pick_new_business({
            'top_business_count': 5,
            'guidance': textwrap.dedent("""
            Prioritize for 
            1) simplicty to build 
            2) zero to 5 hours per week to operate
            3) most confident in having near term profit'
            4) lowest legal risk
            """)
        })
    
    def create_businesses(self):
        Business.objects.exclude(status=BusinessStatus.ACTIVE).delete()
        PubSubManager.publish(
            PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
            payload={
                "requested_count": 5,
                "max_human_hours_per_week": 3,
                "user_input": "Prioritize Short Term Profit",
                "niche": None
            }
        )
        for niche in BusinessNiche:
            PubSubManager.publish(
                PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
                payload={
                    "requested_count": 5,
                    "max_human_hours_per_week": 3,
                    "user_input": "Prioritize Short Term Profit",
                    "niche": niche
                }
            )
        
        PubSubManager.publish(
            PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
            payload={
                "requested_count": 5,
                "max_human_hours_per_week": 20,
                "user_input": "Prioritize Short Term Profit",
                "niche": None
            }
        )
        for niche in BusinessNiche:
            PubSubManager.publish(
                PubSubMessageType.FIND_NICHE_BUSINESS_IDEAS,
                payload={
                    "requested_count": 5,
                    "max_human_hours_per_week": 20,
                    "user_input": "Prioritize Short Term Profit",
                    "niche": niche
                }
            )
