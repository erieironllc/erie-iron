import logging
import os
import textwrap
import traceback
import uuid

from django.db import transaction

from erieiron_autonomous_agent.coding_agents import credential_manager
from erieiron_autonomous_agent.enums import TaskStatus, BusinessStatus
from erieiron_autonomous_agent.models import Initiative, Task, ProductRequirement, Business, SelfDrivingTask
from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat, llm_chat, get_sys_prompt
from erieiron_common import common
from erieiron_common.enums import TaskExecutionSchedule, InitiativeType, TaskType, Level, PubSubMessageType, LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.git_utils import GitWrapper
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager

INITIATIVE_TITLE_BOOTSTRAP_ENVS = "Bootstrap Business"
DEFAULT_ENV_VARS = [
    "AWS_DEFAULT_REGION",
    "AWS_ACCOUNT_ID",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "LLM_API_KEYS_SECRET_ARN",
    "TASK_NAMESPACE",
    "STACK_NAME",
    "PATH"
]


def on_task_blocked(payload, msg):
    task = Task.objects.get(id=payload['task_id'])
    initiative = task.initiative
    business = initiative.business
    
    payload["GOAL"] = "unblock this task"
    
    # --- Add existing tasks
    payload["existing_tasks"] = [
        {
            "task_id": t.id,
            "description": t.description,
            "status": t.status,
            "type": t.task_type,
        }
        for t in initiative.tasks.all()
    ]
    
    # --- Add outputs from executed tasks (if tracked)
    payload["executed_tasks"] = [
        {
            "task_id": t.id,
            "output": t.get_last_execution().output,
            "status": t.status,
        }
        for t in initiative.tasks.filter(status=TaskStatus.COMPLETE)
        if hasattr(t, "execution_output")
    ]
    
    eng_lead_response = business_level_chat(
        initiative,
        "Handle Blocked Task",
        [
            "eng_lead--unblocker.md",
            "eng_lead.md",
            "common--general_coding_rules.md",
            "common--agent_provided_functionality_tofu.md",
            "common--llm_chat.md",
            "common--iam_role_tofu.md",
            "common--forbidden_actions_tofu.md",
            "common--environment_variables_tofu.md",
            "common--infrastructure_rules_tofu.md",
            "common--credentials_architecture_tofu.md"
        ],
        payload,
        output_schema="eng_lead.md.schema.json",
        replacements=[
            ("<iam_role_name>", business.get_iam_role_name())
        ]
    )
    
    new_task_ids = process_response(initiative, eng_lead_response)
    with transaction.atomic():
        Task.objects.filter(id=task.id).update(
            status=TaskStatus.BLOCKED
        )
        
        # Preserve existing dependencies and append new blocking tasks, no duplication or self-reference
        existing_dep_ids = set(task.depends_on.values_list("id", flat=True))
        new_blocking_ids = set(new_task_ids) - {task.id}
        combined_dep_ids = existing_dep_ids.union(new_blocking_ids)
        task.depends_on.set(Task.objects.filter(id__in=combined_dep_ids))
    
    return new_task_ids


def on_business_architecture_generation_requested(payload):
    business = Business.objects.get(id=payload["business_id"])
    write_business_architecture(
        business,
        payload.get("user_input")
    )
    identify_required_credentials(business)


def on_product_initiatives_defined(business_id):
    business = Business.objects.get(id=business_id)
    write_business_architecture(business)
    identify_required_credentials(business)


def define_tasks_for_initiative(initiative_id):
    initiative = Initiative.objects.get(id=initiative_id)
    business = initiative.business
    
    if BusinessStatus.ACTIVE.neq(business.status):
        logging.info(f"skipping creating tasks for {initiative.title}.  The Business is not active")
        return []
    
    if Level.LOW.eq(business.autonomy_level):
        logging.info(f"skipping creating tasks for {initiative.title}.  The Business is LOW automony")
        return []
    
    if not initiative.architecture:
        write_initiative_architecture(initiative)
    
    chat_data = build_chat_data(business, initiative)
    
    eng_lead_response = business_level_chat(
        initiative,
        "Define Initiative Tasks",
        [
            "eng_lead.md",
            "common--general_coding_rules.md",
            "common--agent_provided_functionality_tofu.md",
            "common--llm_chat.md",
            "common--iam_role_tofu.md",
            "common--forbidden_actions_tofu.md",
            "common--environment_variables_tofu.md",
            "common--infrastructure_rules_tofu.md",
            "common--credentials_architecture_tofu.md"
        ],
        chat_data,
        output_schema="eng_lead.md.schema.json",
        reasoning_effort=LlmReasoningEffort.MEDIUM,
        replacements=[
            ("<iam_role_name>", business.get_iam_role_name())
        ]
    )
    
    new_task_ids = process_response(initiative, eng_lead_response)
    
    verification_task, created = Task.objects.update_or_create(
        id=f"{initiative_id}--end_to_end_verification_tester",
        defaults={
            "initiative": initiative,
            "status": TaskStatus.NOT_STARTED,
            "description": f"Verify {initiative.title} end to end",
            "completion_criteria": "end to end test runs green",
            "execution_schedule": TaskExecutionSchedule.NOT_APPLICABLE,
            "task_type": TaskType.INITIATIVE_VERIFICATION
        }
    )
    
    if new_task_ids:
        verification_task.depends_on.set(Task.objects.filter(id__in=new_task_ids))
        Task.objects.filter(id=verification_task.id).update(
            status=TaskStatus.BLOCKED
        )
    new_task_ids.append(verification_task.pk)
    
    return new_task_ids


def build_chat_data(business, initiative):
    requirements = [
        {
            "id": req.id,
            "summary": req.summary,
            "acceptance_criteria": req.acceptance_criteria,
        } for req in initiative.requirements.all()
    ]
    
    if InitiativeType.ENGINEERING.eq(initiative.initiative_type):
        linked_goals = linked_kpis = ["Engineeering Initiative - not linked to Goals / KPIs"]
    else:
        linked_kpis = [
            {
                "id": kpi.id,
                "name": kpi.name
            } for kpi in initiative.linked_kpis.all()
        ]
        
        linked_goals = [
            {
                "id": goal.id,
                "description": goal.description
            } for goal in initiative.linked_goals.all()
        ]
    
    existing_tasks = [
        {
            "task_id": task.id,
            "task_description": task.description
        }
        for task in initiative.tasks.all()
    ]
    
    return {
        "business_name": business.name,
        "initiative_id": initiative.id,
        "initiative_title": initiative.title,
        "initiative_description": initiative.description,
        "user_documentation": initiative.user_documentation,
        "requirements": requirements,
        "linked_kpis": linked_kpis,
        "linked_goals": linked_goals,
        "existing_tasks": existing_tasks,
        "architecture": {
            "business_architecture": business.architecture,
            "initiative_architecture": initiative.architecture,
            "notes": "Business_architecture describes the whole picture for the business.  "
                     "Initiative_architecture describes details specific to this initiative.  "
                     "If there are conflicts between business_architecture and initiative_architecture, Business Architecture takes precedence."
        }
    }


@transaction.atomic
def process_response(initiative, eng_lead_response):
    updated_or_created_task_ids = []
    
    for task_data in eng_lead_response.get("tasks", []):
        task_id = task_data.get("task_id")
        
        validated_ids = set(task_data.get("validated_requirements", []))
        initiative_req_ids = set(str(req.id) for req in initiative.requirements.all())
        invalid_ids = validated_ids - initiative_req_ids
        if invalid_ids:
            raise ValueError(f"Task {task_data.get('task_id')} references invalid requirements: {invalid_ids}")
        
        existing_task = Task.objects.filter(id=task_id).first() if task_id else None
        
        defaults = {
            "initiative": initiative,
            "status": TaskStatus.NOT_STARTED,
            "description": task_data.get("task_description", ""),
            "risk_notes": task_data.get("risk_notes", ""),
            "completion_criteria": task_data.get("completion_criteria"),
            "requires_test": common.parse_bool(task_data.get("requires_test")),
            "execution_schedule": TaskExecutionSchedule(task_data.get("execution_schedule")),
            "timeout_seconds": task_data.get("timeout_seconds"),
            "task_type": TaskType(task_data.get("task_type")),
            "input_fields": task_data.get("input_fields", {}),
            "output_fields": task_data.get("output_fields", [])
        }
        
        execution_start_time = task_data.get("execution_start_time")
        if execution_start_time:
            defaults["execution_start_time"] = task_data["execution_start_time"]
        
        eng_task, created = Task.objects.update_or_create(
            id=task_id if task_id else None,
            defaults=defaults
        )
        
        # Note: design_handoff handling removed as it's not in the schema
        
        # Set depends_on M2M relationship after update_or_create
        dependency_ids = task_data.get("depends_on", [])
        dependencies = Task.objects.filter(id__in=dependency_ids)
        
        # Dependency validation before assignment
        resolved_ids = set(dependencies.values_list("id", flat=True))
        missing = set(dependency_ids) - resolved_ids
        if missing:
            raise ValueError(f"Task {task_id} has unresolved dependencies: {missing}")
        
        eng_task.depends_on.set(dependencies)
        eng_task.validated_requirements.set(
            list(ProductRequirement.objects.filter(id__in=task_data.get("validated_requirements", [])))
        )
        
        # Only compare schema-defined fields
        fields_to_compare = {
            "description": task_data.get("task_description", ""),
            "risk_notes": task_data.get("risk_notes", ""),
            "completion_criteria": task_data.get("completion_criteria"),
            "requires_test": common.parse_bool(task_data.get("requires_test")),
            "execution_schedule": task_data.get("execution_schedule"),
            "timeout_seconds": task_data.get("timeout_seconds"),
            "task_type": task_data.get("task_type"),
            "input_fields": task_data.get("input_fields", {}),
            "output_fields": task_data.get("output_fields", []),
        }
        
        was_updated = any(
            getattr(eng_task, field) != value for field, value in fields_to_compare.items()
        )
        
        # Compare current depends_on to input for update detection
        depends_on_ids = set(eng_task.depends_on.values_list("id", flat=True))
        input_dep_ids = set(dependency_ids)
        if depends_on_ids != input_dep_ids:
            was_updated = True
        
        current_req_ids = set(eng_task.validated_requirements.values_list("id", flat=True))
        input_req_ids = set(task_data.get("validated_requirements", []))
        if current_req_ids != input_req_ids:
            was_updated = True
        
        if created or was_updated:
            updated_or_created_task_ids.append(eng_task.id)
    
    return updated_or_created_task_ids


def bootstrap_buiness(business_id):
    business = Business.objects.get(id=business_id)
    
    business.get_domain_manager().bootstrap_domain()
    
    if business.id == Business.get_erie_iron_business().id:
        return
    
    with transaction.atomic():
        if not business.github_repo_url:
            business.github_repo_url = f"https://github.com/erieironllc/{business.service_token}"
            business.save()
        
        bootstrap_initiative, _ = business.initiative_set.get_or_create(
            title=INITIATIVE_TITLE_BOOTSTRAP_ENVS,
            defaults={
                "id": uuid.uuid4(),
                "business": business,
                "title": INITIATIVE_TITLE_BOOTSTRAP_ENVS,
                "initiative_type": InitiativeType.ENGINEERING,
                "priority": Level.HIGH,
                "description": "Clone the bootstrap project and setup the runtime environments"
            }
        )
        
        task, _ = Task.objects.get_or_create(
            initiative=bootstrap_initiative,
            task_type=TaskType.BOOTSRAP_CLONE_REPO,
            defaults={
                "id": uuid.uuid4(),
                "status": TaskStatus.IN_PROGRESS,
                "description": f"Clone the bootstrap repo to {business.github_repo_url}",
                "requires_test": False
            }
        )
    
    git = GitWrapper()
    try:
        bootstrap_repo(business, git)
        
        # create a placeholder iteration
        self_driving_task, _ = SelfDrivingTask.objects.get_or_create(
            task_id=task.id,
            defaults={
                "sandbox_path": os.path.abspath(git.source_root),
                "main_name": "n/a",
                "goal": "clone repo",
                "business": business
            }
        )
        self_driving_task_iteration = self_driving_task.iterate()
        
        self_driving_task.sandbox_path = os.path.abspath(git.source_root)
        self_driving_task.save()
        
        git.mk_venv()
        business.snapshot_code(self_driving_task_iteration)
        
        PubSubManager.publish_id(
            PubSubMessageType.TASK_COMPLETED,
            task.id
        )
    except Exception as e:
        PubSubManager.publish(
            PubSubMessageType.TASK_FAILED,
            payload={
                "task_id": task.id,
                "error": traceback.format_exc()
            }
        )
        raise e
    finally:
        git.cleanup()
        pass


def bootstrap_repo(business: Business, git: GitWrapper):
    if git.exists(business.github_repo_url):
        git.clone(business.github_repo_url)
    else:
        source_repo = get_source_repo_url(business)
        target_repo = business.github_repo_url
        clone_path = git.source_root
        
        logging.info(f"Cloning bootstrap repository from {source_repo} to {clone_path} ({target_repo})")
        
        git.clone_to_new_repo(
            source_repo,
            target_repo
        )
        
        if (clone_path / "README.md").exists():
            common.replace_in_file(clone_path / "README.md", [
                ("erieiron_bootstrap", business.service_token),
                ("Bootstrap Project", f"{business.name} Project")
            ])
        else:
            (clone_path / "README.md").write_text("TODO")
        
        if (clone_path / "package.json").exists():
            common.replace_in_file(clone_path / "package.json", [
                ("erieiron_bootstrap", business.service_token)
            ])
        
        git.create_repo(business.github_repo_url)
        git.add_commit_push(f"Initialize repository for {business.name}")


def get_source_repo_url(business: Business) -> str:
    # at some point we might support different source repos depending the the business requirements
    return "https://github.com/erieironllc/erieiron_bootstrap"


def write_business_architecture(business, user_input: str = None):
    business_architecture = llm_chat(
        "Write business architecture",
        [
            get_sys_prompt(
                [
                    "system_architect.md",
                    "common--general_coding_rules.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--llm_chat.md",
                    "common--iam_role_tofu.md",
                    "common--domain_management_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md",
                    "common--infrastructure_rules_tofu.md",
                    "common--credentials_architecture_tofu.md"
                ],
                replacements=[
                    ("<env_vars>", ", ".join(DEFAULT_ENV_VARS))
                ]
            ),
            LlmMessage.user_from_data("Business Description", {
                "business_description": business.llm_data()
            }),
            LlmMessage.user_from_data(
                "Business Initiatives", [i.llm_data() for i in business.initiative_set.all()], "planned_initiatives"
            ),
            textwrap.dedent(f"""
            ## The CTO's guidance for the architecture:
            {user_input}
            
            **You must** respect any guidance give here as closely as possible
            """) if user_input else None,
            "Please write a markdown-formatted high-level design document for Business's architecture"
        ],
        model=LlmModel.OPENAI_GPT_5_1,
        tag_entity=business,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.MEDIUM
    ).text
    
    Business.objects.filter(id=business.id).update(
        architecture=business_architecture
    )
    business.refresh_from_db(fields=["architecture"])


def write_initiative_architecture(initiative: Initiative):
    business = initiative.business
    architecture = llm_chat(
        "Write Initiative design doc",
        [
            get_sys_prompt(
                [
                    "system_architect.md",
                    "common--general_coding_rules.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--llm_chat.md",
                    "common--iam_role_tofu.md",
                    "common--domain_management_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md",
                    "common--infrastructure_rules_tofu.md",
                    "common--credentials_architecture_tofu.md"
                ],
                replacements=[
                    ("<env_vars>", ", ".join(DEFAULT_ENV_VARS))
                ]
            ),
            *LlmMessage.user_from_data("Full Business Architecture", {
                "architecture_docs": business.architecture
            }),
            *LlmMessage.user_from_data(
                "Current Initiative", initiative.llm_data()
            ),
            "Please write a markdown-formatted high-level design document for this **Initiatives's** architecture. It should **never conflict** with the supplied business's architecture - it should only clarify details needed to implement the Current Initiative."
        ],
        model=LlmModel.OPENAI_GPT_5_1,
        tag_entity=initiative,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.MEDIUM
    ).text
    
    Initiative.objects.filter(id=initiative.id).update(
        architecture=architecture
    )
    initiative.refresh_from_db(fields=["architecture"])


def identify_required_credentials(business: Business):
    planning_data = llm_chat(
        "Identify required credentials",
        [
            *common.ensure_list(
                get_sys_prompt([
                    "codeplanner--initial_credentials.md",
                    "codeplanner--common.md",
                    "common--general_coding_rules.md",
                    "common--agent_provided_functionality_tofu.md",
                    "common--llm_chat.md",
                    "common--iam_role_tofu.md",
                    "common--forbidden_actions_tofu.md",
                    "common--environment_variables_tofu.md",
                    "common--credentials_architecture_tofu.md"
                ], replacements=[
                    ("<credential_manager_existing_services>", credential_manager.get_existing_service_names_desc()),
                    ("<credential_manager_existing_service_schemas>", credential_manager.get_existing_service_schema_desc()),
                    ("<business_tag>", business.service_token),
                    ("<env_vars>", ", ".join(DEFAULT_ENV_VARS))
                ]),
            ),
            *LlmMessage.user_from_data(
                "Architecture", {
                    "business_architecture": business.architecture,
                    "notes": "Business_architecture describes the whole picture for the business"
                }
            ),
            *business.get_existing_required_credentials_llmm(),
            "Please identify the credentials required"
        ],
        model=LlmModel.OPENAI_GPT_5_1,
        tag_entity=business,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.MEDIUM,
        output_schema="codeplanner.schema.json"
    ).json()
    
    current_credentials = business.required_credentials or {}
    Business.objects.filter(id=business.id).update(
        required_credentials={
            **current_credentials,
            **(planning_data["required_credentials"] or {})
        }
    )
    business.refresh_from_db(fields=["required_credentials"])
