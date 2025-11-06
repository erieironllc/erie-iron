from erieiron_autonomous_agent.board_level_agents import corporate_development_agent, board_analyst, portfolio_resource_planner, board_chair
from erieiron_autonomous_agent.business_level_agents import eng_lead, product_lead, ceo, worker_design, worker_coder, task_manager, worker_human
from erieiron_autonomous_agent.coding_agents import self_driving_coder_agent_tofu
from erieiron_common.enums import PubSubMessageType as T
from erieiron_common.message_queue.pubsub_manager import pubsub_workflow, PubSubManager


@pubsub_workflow
def board_workflow(pubsub_manager: PubSubManager):
    # Board Chair
    pubsub_manager.on(
        # T.EVERY_WEEK,
        # board_chair.exec_board_chair_tasks
        # ).on(
        #     T.EVERY_DAY,
        #     board_chair.exec_business_analysis
        # ).on(
        [T.ANALYSIS_ADDED, T.BOARD_GUIDANCE_REQUESTED],
        board_chair.on_board_guidance_requested,
        T.BOARD_GUIDANCE_UPDATED
    ).on(
        T.PORTFOLIO_REDUCE_BUSINESSES_REQUESTED,
        board_chair.on_portfolio_reduce_businesses_requested
    )
    
    # Board Business Development
    pubsub_manager.on(
        T.PORTFOLIO_ADD_BUSINESSES_REQUESTED,
        corporate_development_agent.find_new_business_opportunity,
        T.BUSINESS_IDEA_SUBMITTED
    ).on(
        T.BUSINESS_IDEA_SUBMITTED,
        corporate_development_agent.submit_business_opportunity,
        T.ANALYSIS_REQUESTED
    )
    
    # Board Analyst
    pubsub_manager.on(
        T.ANALYSIS_REQUESTED,
        board_analyst.on_analysis_requested,
        T.ANALYSIS_ADDED
    )
    
    # Board Resource Planner
    pubsub_manager.on(
        T.RESOURCE_PLANNING_REQUESTED,
        portfolio_resource_planner.on_resource_planning_requested,
        T.ANALYSIS_ADDED
    )
    
    pubsub_manager.on(
        T.RESET_TASK_TEST,
        self_driving_coder_agent_tofu.on_reset_task_test
    )


@pubsub_workflow
def business_workflow(pubsub_manager: PubSubManager):
    # CEO
    pubsub_manager.on(
        T.BOARD_GUIDANCE_UPDATED,
        ceo.on_business_guidance_updated,
        T.CEO_DIRECTIVES_ISSUED
    ).on(
        T.CEO_DIRECTIVES_ISSUED,
        PubSubManager.noop(),
        T.PRODUCT_INITIATIVES_REQUESTED
    )
    
    # Product Lead
    pubsub_manager.on(
        T.PRODUCT_INITIATIVES_REQUESTED,
        product_lead.define_initiatives,
        T.INITIATIVE_DEFINED  # one fired for each initiative
    ).on(
        T.INITIATIVE_DEFINITION_REQUESTED,
        product_lead.define_single_initiative,
        T.INITIATIVE_DEFINED
    )
    
    # Eng Lead
    pubsub_manager.on(
        T.INITIATIVE_DEFINED,
        eng_lead.define_tasks_for_initiative,
        T.TASK_UPDATED
    ).on(
        T.PRODUCT_INITIATIVES_DEFINED,
        eng_lead.on_product_initiatives_defined
    ).on(
        T.BUSINESS_BOOTSTRAP_REQUESTED,
        eng_lead.bootstrap_buiness
    ).on(
        T.BUSINESS_ARCHITECTURE_GENERATION_REQUESTED,
        eng_lead.on_business_architecture_generation_requested
    ).on(
        T.TASK_BLOCKED,
        eng_lead.on_task_blocked,
        T.TASK_UPDATED
    )
    
    # pubsub_manager.on(
    #     T.INITIATIVE_DEPLOY_REQUESTED,
    #     get_self_driving_coder_agent_module().deploy_to_production
    # )
    
    # Task Manager
    pubsub_manager.on(
        T.TASK_UPDATED,
        task_manager.on_task_updated,
    ).on(
        T.TASK_COMPLETED,
        task_manager.on_task_complete
    ).on(
        T.TASK_FAILED,
        task_manager.on_task_failed
    ).on(
        T.TASK_SPEND,
        task_manager.on_task_spend
    ).on(
        T.INITIATIVE_GREEN_LIT,
        task_manager.on_initiative_green_lit
    )
    
    # Desiger
    pubsub_manager.on(
        T.DESIGN_WORK_REQUESTED,
        worker_design.do_work,
        T.TASK_UPDATED
    )
    
    # Coder
    pubsub_manager.on(
        T.CODING_WORK_REQUESTED,
        worker_coder.do_work,
        T.TASK_UPDATED
    )
    
    # Human
    pubsub_manager.on(
        T.HUMAN_WORK_REQUESTED,
        worker_human.do_work
    )
