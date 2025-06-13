from django.db import transaction

from erieiron_autonomous_agent.system_agent_llm_interface import business_level_chat
from erieiron_common.models import ProductInitiative, EngineeringTask, EngineeringTaskStep, ProductRequirement, Capability


def define_tasks_for_initiative(product_initiative_id):
    initiative = ProductInitiative.objects.get(id=product_initiative_id)
    business = initiative.business

    requirements = []
    for req in initiative.requirements.all():
        requirements.append({
            "id": req.id,
            "summary": req.summary,
            "acceptance_criteria": req.acceptance_criteria,
        })

    linked_kpis = [
        {"id": kpi.id, "name": kpi.name} for kpi in initiative.linked_kpis.all()
    ]

    linked_goals = [
        {"id": goal.id, "description": goal.description} for goal in initiative.linked_goals.all()
    ]

    existing_tasks_qs = EngineeringTask.objects.filter(product_initiative=initiative)
    existing_tasks = [
        {
            "task_id": task.id,
            "task_description": task.task_description
        }
        for task in existing_tasks_qs
    ]

    existing_capabilities = [
        {
            "id": cap.id,
            "name": cap.name,
            "description": cap.description,
            "inputs": cap.inputs,
            "output_schema": cap.output_schema,
        }
        for cap in Capability.objects.all()
    ]

    eng_lead_response = business_level_chat(
        "eng_lead.md",
        {
            "business_name": business.name,
            "product_initiative_id": initiative.id,
            "product_initiative_title": initiative.title,
            "product_initiative_description": initiative.description,
            "requirements": requirements,
            "linked_kpis": linked_kpis,
            "linked_goals": linked_goals,
            "existing_tasks": existing_tasks,
            "existing_capabilities": existing_capabilities,
        },
        debug=True
    )

    new_capability_ids = []
    with transaction.atomic():
        resolved_capabilities = {}

        # Add existing capabilities by name (to support backward compatibility) and by id
        for cap in Capability.objects.all():
            resolved_capabilities[cap.name] = cap
            resolved_capabilities[cap.id] = cap

        for spec in eng_lead_response.get("new_capabilities", []):
            capability_id = spec.get("id")
            if not capability_id:
                raise ValueError(f"Capability missing required 'id': {spec}")

            base_name = spec.get("name", "")
            name = base_name
            counter = 1
            while Capability.objects.filter(name=name).exists():
                name = f"{base_name}_{counter}"
                counter += 1
            spec["name"] = name

            capability = Capability.objects.create(
                id=capability_id,
                name=spec.get("name", ""),
                description=spec.get("description", ""),
                inputs=spec.get("inputs", {}),
                output_schema=spec.get("output_schema", {}),
                test_plan=spec.get("test_plan", ""),
                runtime_env=spec.get("runtime_env", ""),
                entry_point=spec.get("entry_point", ""),
                execution_sandbox=spec.get("execution_sandbox", ""),
                can_be_built_autonomously=spec.get("can_be_built_autonomously", False),
                builder_notes=spec.get("builder_notes", ""),
            )
            resolved_capabilities[capability_id] = capability
            resolved_capabilities[capability.id] = capability
            resolved_capabilities[capability.name] = capability
            new_capability_ids.append(capability.id)

        for task_data in eng_lead_response.get("engineering_tasks", []):
            base_required_fields = [
                "task_id",
                "depends_on",
                "task_description",
                "required_capabilities",
                "risk_notes",
                "test_plan",
                "execution_mode"
            ]
            for field in base_required_fields:
                if field not in task_data:
                    raise ValueError(f"Missing required task field '{field}': {task_data}")

            if task_data["execution_mode"] == "AUTONOMOUS":
                if "steps" not in task_data or not isinstance(task_data["steps"], list) or len(task_data["steps"]) == 0:
                    raise ValueError(f"'steps' is required for AUTONOMOUS tasks: {task_data}")
            elif "steps" in task_data:
                raise ValueError(f"'steps' must be omitted for HUMAN tasks: {task_data}")

            task_id = task_data.get("task_id")
            eng_task, _ = EngineeringTask.objects.update_or_create(
                id=task_id if task_id else None,
                defaults={
                    "product_initiative": initiative,
                    "task_description": task_data.get("task_description", ""),
                    "depends_on": task_data.get("depends_on", []),
                    "risk_notes": task_data.get("risk_notes", ""),
                    "test_plan": task_data.get("test_plan", ""),
                    "execution_mode": task_data.get("execution_mode"),
                    "raw_llm_payload": task_data,
                }
            )
            eng_task.validated_requirements.set(
                list(ProductRequirement.objects.filter(id__in=task_data.get("validated_requirements", [])))
            )
            print(f"Saved task: {eng_task.id}")

            # NOTE: We delete and recreate steps to ensure alignment with the LLM's output.
            # This is safe because steps are stateless and derived from prompt output.
            # If steps gain state (e.g. logs, audit trails), this may need to be changed to a diff-and-update strategy.
            eng_task.engineeringtaskstep_set.all().delete()
            for step_data in task_data.get("steps", []):
                step = EngineeringTaskStep.objects.create(
                    task=eng_task,
                    step_index=step_data.get("step_index"),
                    inputs=step_data.get("inputs", {}),
                    outputs=step_data.get("outputs", {}),
                )

                capability = resolved_capabilities.get(step_data["capability"])
                if not capability:
                    raise ValueError(f"Step references unknown capability: {step_data['capability']}")
                step.capabilities.add(capability)

    return new_capability_ids
