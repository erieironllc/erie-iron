from erieiron_ui import views

TAB_DIVIDER = {
    "slug": "divider",
    "is_divider": True
}

INITIATIVE_TAB_DEFINITIONS = [
    {
        "slug": "overview",
        "label": "Overview",
        "template": "initiative/tabs/overview.html",
        "availability_fn": views._initiative_tab_available_overview,
        "context_fn": views._initiative_tab_context_overview,
    },
    TAB_DIVIDER,
    {
        "slug": "requirements",
        "label": "Requirements",
        "template": "initiative/tabs/requirements.html",
        "availability_fn": views._initiative_tab_available_requirements,
        "context_fn": views._initiative_tab_context_requirements,
    },
    {
        "slug": "architecture",
        "label": "Architecture",
        "template": "initiative/tabs/architecture.html",
        "availability_fn": views._initiative_tab_available_architecture,
        "context_fn": views._initiative_tab_context_architecture,
    },
    {
        "slug": "user-documentation",
        "label": "User Documentation",
        "template": "initiative/tabs/user_documentation.html",
        "availability_fn": views._initiative_tab_available_user_documentation,
        "context_fn": views._initiative_tab_context_user_documentation,
    },
    {
        "slug": "tasks",
        "label": "Tasks",
        "template": "initiative/tabs/tasks.html",
        "availability_fn": views._initiative_tab_available_tasks,
        "context_fn": views._initiative_tab_context_tasks,
    },
    {
        "slug": "infrastructure-stacks",
        "label": "Infrastructure Stacks",
        "template": "initiative/tabs/infrastructure_stacks.html",
        "availability_fn": views._initiative_tab_available_infrastructure_stacks,
        "context_fn": views._initiative_tab_context_infrastructure_stacks,
    },
    # {
    #     "slug": "processes",
    #     "label": "Processes",
    #     "template": "initiative/tabs/processes.html",
    #     "availability_fn": views._initiative_tab_available_processes,
    #     "context_fn": views._initiative_tab_context_processes,
    # },
    TAB_DIVIDER,
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "initiative/tabs/llmrequests.html",
        "availability_fn": views._initiative_tab_available_llmrequests,
        "context_fn": views._initiative_tab_context_llmrequests,
    },
    {
        "slug": "llm-spend",
        "label": "LLM Spend",
        "template": "initiative/tabs/llm_spend.html",
        "availability_fn": views._initiative_tab_available_llm_spend,
        "context_fn": views._initiative_tab_context_llm_spend,
    },
    {
        "slug": "bug-report",
        "label": "Bug Report",
        "template": "initiative/tabs/bug_report.html",
        "availability_fn": views._initiative_tab_available_bug_report,
        "context_fn": views._initiative_tab_context_bug_report,
    },
    {
        "slug": "edit",
        "label": "Edit",
        "template": "initiative/tabs/edit.html",
        "availability_fn": views._initiative_tab_available_edit,
        "context_fn": views._initiative_tab_context_edit,
    },
]

INITIATIVE_TAB_MAP = {
    definition["slug"]: definition
    for definition in INITIATIVE_TAB_DEFINITIONS
    if not definition.get("is_divider")
}

BUSINESSES_TAB_DEFINITIONS = [
    {
        "slug": "portfolio",
        "label": "Portfolio",
        "template": "businesses/tabs/portfolio.html",
        "availability_fn": views._businesses_tab_available_portfolio,
        "context_fn": views._businesses_tab_context_portfolio,
    },
    TAB_DIVIDER,
    {
        "slug": "capacity",
        "label": "Capacity",
        "template": "businesses/tabs/capacity.html",
        "availability_fn": views._businesses_tab_available_capacity,
        "context_fn": views._businesses_tab_context_capacity,
    },
    {
        "slug": "initiatives",
        "label": "Initiatives",
        "template": "businesses/tabs/initiatives.html",
        "availability_fn": views._businesses_tab_available_initiatives,
        "context_fn": views._businesses_tab_context_initiatives,
    },
    {
        "slug": "lessons",
        "label": "Lessons",
        "template": "businesses/tabs/lessons.html",
        "availability_fn": views._businesses_tab_available_lessons,
        "context_fn": views._businesses_tab_context_lessons,
    },

    TAB_DIVIDER,
    {
        "slug": "llm-spend",
        "label": "LLM Spend",
        "template": "businesses/tabs/llm_spend.html",
        "availability_fn": views._businesses_tab_available_llm_spend,
        "context_fn": views._businesses_tab_context_llm_spend,
    },
    {
        "slug": "tools",
        "label": "Tools",
        "template": "businesses/tabs/tools.html",
        "availability_fn": views._businesses_tab_available_tools,
        "context_fn": views._businesses_tab_context_tools,
    },
]

BUSINESSES_TAB_MAP = {
    definition["slug"]: definition
    for definition in BUSINESSES_TAB_DEFINITIONS
    if not definition.get("is_divider")
}

TASK_TAB_DEFINITIONS = [
    {
        "slug": "overview",
        "label": "Overview",
        "template": "task/tabs/overview.html",
        "availability_fn": views._task_tab_available_overview,
        "context_fn": views._task_tab_context_overview,
    },
    {
        "slug": "latest_iteration",
        "label": "Latest Iteration",
        "template": "task/tabs/iterations.html",
        "availability_fn": views._task_tab_available_iterations,
        "context_fn": views._task_tab_context_latest_iteration,
    },
    {
        "slug": "iterations",
        "label": "Iterations",
        "template": "task/tabs/iterations.html",
        "availability_fn": views._task_tab_available_iterations,
        "context_fn": views._task_tab_context_iterations,
    },
    TAB_DIVIDER,
    {
        "slug": "testcode",
        "label": "Test Code",
        "template": "task/tabs/testcode.html",
        "availability_fn": views._task_tab_available_testcode,
        "context_fn": views._task_tab_context_testcode,
    },
    {
        "slug": "guidance",
        "label": "Guidance",
        "template": "task/tabs/guidance.html",
        "availability_fn": views._task_tab_available_guidance,
        "context_fn": views._task_tab_context_guidance,
    },
    {
        "slug": "blocked-by",
        "label": "Blocked By",
        "template": "task/tabs/blocked_by.html",
        "availability_fn": views._task_tab_available_blocked_by,
        "context_fn": views._task_tab_context_blocked_by,
    },
    {
        "slug": "blocks",
        "label": "Blocks",
        "template": "task/tabs/blocks.html",
        "availability_fn": views._task_tab_available_blocks,
        "context_fn": views._task_tab_context_blocks,
    },
    {
        "slug": "codefiles",
        "label": "Code Files",
        "template": "task/tabs/codefiles.html",
        "availability_fn": views._task_tab_available_codefiles,
        "context_fn": views._task_tab_context_codefiles,
    },
    {
        "slug": "latest_iteration_logs",
        "label": "Logs",
        "template": "task/tabs/latest_iteration_logs.html",
        "availability_fn": views._task_tab_available_iterations,
        "context_fn": views._task_tab_context_latest_iteration_logs,
    },
    {
        "slug": "resolve",
        "label": "Resolve",
        "template": "task/tabs/resolve.html",
        "availability_fn": views._task_tab_available_resolve,
        "context_fn": views._task_tab_context_resolve,
    },
    # {
    #     "slug": "processes",
    #     "label": "Processes",
    #     "template": "task/tabs/processes.html",
    #     "availability_fn": views._task_tab_available_processes,
    #     "context_fn": views._task_tab_context_processes,
    # },
    TAB_DIVIDER,
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "task/tabs/llmrequests.html",
        "availability_fn": views._task_tab_available_llmrequests,
        "context_fn": views._task_tab_context_llmrequests,
    },
    {
        "slug": "llm-spend",
        "label": "LLM Spend",
        "template": "task/tabs/llm_spend.html",
        "availability_fn": views._task_tab_available_llm_spend,
        "context_fn": views._task_tab_context_llm_spend,
    },
    {
        "slug": "edit",
        "label": "Edit",
        "template": "task/tabs/edit.html",
        "availability_fn": views._task_tab_available_edit,
        "context_fn": views._task_tab_context_edit,
    },
]

ITERATION_TAB_DEFINITIONS = [
    {
        "slug": "routing",
        "label": "Iteration",
        "template": "iteration/tabs/overview.html",
        "availability_fn": views._iteration_tab_available_routing,
        "context_fn": views._iteration_tab_context_routing,
    },
    TAB_DIVIDER,
    {
        "slug": "coding",
        "label": "Coding",
        "template": "iteration/tabs/coding.html",
        "availability_fn": views._iteration_tab_available_planning,
        "context_fn": views._iteration_tab_context_planning,
    },
    {
        "slug": "logs",
        "label": "Agent Logs",
        "template": "iteration/tabs/logs.html",
        "availability_fn": views._iteration_tab_available_codelog,
        "context_fn": views._iteration_tab_context_codelog,
    },
    {
        "slug": "cloudformation",
        "label": "CloudFormation Logs",
        "template": "iteration/tabs/cloudformation_logs.html",
        "availability_fn": views._iteration_tab_available_cloudformation_logs,
        "context_fn": views._iteration_tab_context_cloudformation_logs,
    },
    {
        "slug": "evaluation",
        "label": "Evaluation",
        "template": "iteration/tabs/evaluation.html",
        "availability_fn": views._iteration_tab_available_evaluation,
        "context_fn": views._iteration_tab_context_evaluation,
    },
    # {
    #     "slug": "execlog",
    #     "label": "Logs - Execution",
    #     "template": "iteration/tabs/execlog.html",
    #     "availability_fn": views._iteration_tab_available_execlog,
    #     "context_fn": views._iteration_tab_context_execlog,
    # },
    # {
    #     "slug": "processes",
    #     "label": "Processes",
    #     "template": "iteration/tabs/processes.html",
    #     "availability_fn": views._iteration_tab_available_processes,
    #     "context_fn": views._iteration_tab_context_processes,
    # },
    TAB_DIVIDER,
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "iteration/tabs/llmrequests.html",
        "availability_fn": views._iteration_tab_available_llmrequests,
        "context_fn": views._iteration_tab_context_llmrequests,
    },
    {
        "slug": "tools",
        "label": "Edit",
        "template": "iteration/tabs/tools.html",
        "availability_fn": views._iteration_tab_available_tools,
        "context_fn": views._iteration_tab_context_tools,
    },
]

BUSINESS_TAB_DEFINITIONS = [
    {
        "slug": "overview",
        "label": "Overview",
        "template": "business/tabs/overview.html",
        "availability_fn": views._tab_available_overview,
        "context_fn": views._tab_context_overview,
    },
    {
        "slug": "business-plan",
        "label": "Business Plan",
        "template": "business/tabs/business_plan.html",
        "availability_fn": views._tab_available_business_plan,
        "context_fn": views._tab_context_business_plan,
    },
    TAB_DIVIDER,
    {
        "slug": "business-analysis",
        "label": "Business Analysis",
        "template": "business/tabs/business_analysis.html",
        "availability_fn": views._tab_available_business_analysis,
        "context_fn": views._tab_context_business_analysis,
    },
    {
        "slug": "legal-analysis",
        "label": "Legal Analysis",
        "template": "business/tabs/legal_analysis.html",
        "availability_fn": views._tab_available_legal_analysis,
        "context_fn": views._tab_context_legal_analysis,
    },
    {
        "slug": "capacity-analysis",
        "label": "Capacity Analysis",
        "template": "business/tabs/capacity_analysis.html",
        "availability_fn": views._tab_available_capacity_analysis,
        "context_fn": views._tab_context_capacity_analysis,
    },
    {
        "slug": "board-guidance",
        "label": "Board Guidance",
        "template": "business/tabs/board_guidance.html",
        "availability_fn": views._tab_available_board_guidance,
        "context_fn": views._tab_context_board_guidance,
    },
    TAB_DIVIDER,
    {
        "slug": "bug-report",
        "label": "Bug Report",
        "template": "business/tabs/bug_report.html"
    },
    {
        "slug": "ceo-guidance",
        "label": "CEO Guidance",
        "template": "business/tabs/ceo_guidance.html",
        "availability_fn": views._tab_available_ceo_guidance,
        "context_fn": views._tab_context_ceo_guidance,
    },
    {
        "slug": "architecture",
        "label": "Architecture",
        "template": "business/tabs/architecture.html",
        "availability_fn": views._tab_available_architecture,
        "context_fn": views._tab_context_architecture,
    },
    {
        "slug": "product-initiatives",
        "label": "Product Initiatives",
        "template": "business/tabs/product_initiatives.html",
        "availability_fn": views._tab_available_product_initiatives,
        "context_fn": views._tab_context_product_initiatives,
    },
    {
        "slug": "codefiles",
        "label": "Code Files",
        "template": "business/tabs/codefiles.html",
        "availability_fn": views._tab_available_codefiles,
        "context_fn": views._tab_context_codefiles,
    },
    TAB_DIVIDER,
    # {
    #     "slug": "tasks",
    #     "label": "Tasks",
    #     "template": "business/tabs/tasks.html",
    #     "availability_fn": views._tab_available_tasks,
    #     "context_fn": views._tab_context_tasks,
    # },
    {
        "slug": "llmrequests",
        "label": "LLM Requests",
        "template": "business/tabs/llmrequests.html",
        "availability_fn": views._tab_available_llmrequests,
        "context_fn": views._tab_context_llmrequests,
    },
    {
        "slug": "llm-spend",
        "label": "LLM Spend",
        "template": "business/tabs/llm_spend.html",
        "availability_fn": views._tab_available_llm_spend,
        "context_fn": views._tab_context_llm_spend,
    },
    {
        "slug": "edit",
        "label": "Edit",
        "template": "business/tabs/edit.html",
        "availability_fn": views._tab_available_edit,
        "context_fn": views._tab_context_edit,
    },
]

BUSINESS_TAB_MAP = {
    definition["slug"]: definition
    for definition in BUSINESS_TAB_DEFINITIONS
    if "slug" in definition
}

TASK_TAB_MAP = {
    definition["slug"]: definition
    for definition in TASK_TAB_DEFINITIONS
    if "slug" in definition
}

ITERATION_TAB_MAP = {
    definition["slug"]: definition
    for definition in ITERATION_TAB_DEFINITIONS
    if "slug" in definition
}
