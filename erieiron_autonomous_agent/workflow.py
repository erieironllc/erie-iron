from erieiron_autonomous_agent.board_level_agents import corporate_development_agent, board_analyst, portfolio_resource_planner, board_chair
from erieiron_autonomous_agent.business_level_agents import eng_lead, product_lead, ceo, capability_identifier, capability_builder
from erieiron_common.enums import PubSubMessageType
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager


@pubsub_workflow
def initialize_workflow(pubsub_manager: PubSubManager):
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

    # Business CEO
    pubsub_manager.on(
        PubSubMessageType.BOARD_GUIDANCE_UPDATED,
        ceo.on_business_guidance_updated,
        PubSubMessageType.CEO_DIRECTIVES_ISSUED
    )

    # Business Product Lead
    pubsub_manager.on(
        PubSubMessageType.CEO_DIRECTIVES_ISSUED,
        product_lead.define_product_initiatives,
        PubSubMessageType.PRODUCT_INITIATIVE_DEFINED # one fired for each initiative
    )

    # Business Eng Lead
    pubsub_manager.on(
        PubSubMessageType.PRODUCT_INITIATIVE_DEFINED,
        eng_lead.define_tasks_for_initiative,
        # PubSubMessageType.ENGINEERING_TASKS_DEFINED
    )

    # Capability Identifier
    # pubsub_manager.on(
    #     PubSubMessageType.ENGINEERING_TASKS_DEFINED,
    #     capability_identifier.on_engineering_tasks_defined,
    #     PubSubMessageType.CAPABILITIES_IDENTIFIED
    # )

    # Capability Builder
    # pubsub_manager.on(
    #     PubSubMessageType.CAPABILITIES_IDENTIFIED,
    #     capability_builder.on_capabilities_identified,
    #     PubSubMessageType.CAPABILITY_SPEC_READY
    # )
