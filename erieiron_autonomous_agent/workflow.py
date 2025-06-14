from erieiron_autonomous_agent.board_level_agents import corporate_development_agent, board_analyst, portfolio_resource_planner, board_chair
from erieiron_autonomous_agent.business_level_agents import eng_lead, product_lead, ceo, worker_design, worker_coder
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager


@pubsub_workflow
def board_workflow(pubsub_manager: PubSubManager):
    # Board Chair
    pubsub_manager.on(
        PubSubMessageType.BOARD_CHAIR_EXEC_REQUESTED,
        board_chair.exec_board_chair_tasks
    ).on(
        [PubSubMessageType.ANALYSIS_ADDED, PubSubMessageType.BOARD_GUIDANCE_REQUESTED],
        board_chair.on_board_guidance_requested,
        PubSubMessageType.BOARD_GUIDANCE_UPDATED
    ).on(
        PubSubMessageType.PORTFOLIO_REDUCE_BUSINESSES_REQUESTED,
        board_chair.on_portfolio_reduce_businesses_requested
    )

    # Board Business Development
    pubsub_manager.on(
        PubSubMessageType.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        corporate_development_agent.find_new_business_opportunity,
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED
    ).on(
        PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
        corporate_development_agent.submit_business_opportunity,
        PubSubMessageType.ANALYSIS_REQUESTED
    )

    # Board Analyst
    pubsub_manager.on(
        PubSubMessageType.ANALYSIS_REQUESTED,
        board_analyst.on_analysis_requested,
        PubSubMessageType.ANALYSIS_ADDED
    )

    # Board Resource Planner
    pubsub_manager.on(
        PubSubMessageType.RESOURCE_PLANNING_REQUESTED,
        portfolio_resource_planner.on_resource_planning_requested
    )


@pubsub_workflow
def business_workflow(pubsub_manager: PubSubManager):
    # CEO
    pubsub_manager.on(
        PubSubMessageType.BOARD_GUIDANCE_UPDATED,
        ceo.on_business_guidance_updated,
        PubSubMessageType.CEO_DIRECTIVES_ISSUED
    )

    # Product Lead
    pubsub_manager.on(
        PubSubMessageType.CEO_DIRECTIVES_ISSUED,
        product_lead.define_product_initiatives,
        PubSubMessageType.PRODUCT_INITIATIVE_DEFINED  # one fired for each initiative
    )

    # Eng Lead
    pubsub_manager.on(
        PubSubMessageType.PRODUCT_INITIATIVE_DEFINED,
        eng_lead.define_tasks_for_initiative
        # publishes ENGINEERING_WORK_REQUESTED | DESIGN_WORK_REQUESTED | HUMAN_WORK_REQUESTED
    ).on(
        PubSubMessageType.WORK_COMPLETED,
        eng_lead.on_work_completed
    )

    # Desiger
    pubsub_manager.on(
        PubSubMessageType.DESIGN_WORK_REQUESTED,
        worker_design.do_work,
        PubSubMessageType.WORK_COMPLETED
    )

    # Coder
    pubsub_manager.on(
        PubSubMessageType.CODING_WORK_REQUESTED,
        worker_coder.do_work
    )

    # Human
    # pubsub_manager.on(
    #     PubSubMessageType.HUMAN_WORK_REQUESTED,
    #     worker_human.do_work
    # )
