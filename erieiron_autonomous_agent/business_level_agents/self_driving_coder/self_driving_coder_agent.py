import json
import logging
import os
import pprint
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

import settings
from erieiron_autonomous_agent.business_level_agents.self_driving_coder import self_driving_coder_runner
from erieiron_autonomous_agent.business_level_agents.self_driving_coder.self_driving_coder_config import SelfDriverConfig, SelfDriverConfigException, AgentBlocked, GoalAchieved
from erieiron_common import common
from erieiron_common.aws_utils import get_aws_interface
from erieiron_common.codegen_utils import CodeCompilationError
from erieiron_common.enums import LlmModel, S3Bucket, PubSubMessageType, TaskStatus
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage, MODEL_TO_MAX_TOKENS, LlmResponse
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.models import CodeVersion, SelfDrivingTaskIteration, LlmRequest, Task, RunningProcess

ARTIFACTS = "artifacts"
PROMPTS_DIR = Path("./erieiron_autonomous_agent/business_level_agents/prompts/")

COUNT_FULL_LOGS_IN_CONTEXT = 2


def execute(config_file: Path = None, task_id: str = None):
    config = None
    stop_reason = ""
    supress_eval = False
    try:
        for i in range(100):
            config = SelfDriverConfig.get(config_file, task_id)

            if i == 0:
                print(f"tail -f {os.path.abspath(config.log_path)}")

            total_spend = config.self_driving_task.get_cost()

            if total_spend > config.max_budget_usd:
                stop_reason = f"Stopping - hit the max budget ${config.max_budget_usd:.2f}"
                break

            if (config.code_directory / "restart").exists():
                supress_eval = True
                stop_reason = f"Stopping for restart"
                common.quietly_delete((config.code_directory / "restart"))

                if config_file:
                    common.execute_management_cmd(
                        f"sda_code --config={config_file}"
                    )
                elif task_id:
                    common.execute_management_cmd(
                        f"sda_code --task_id={task_id}"
                    )
                else:
                    raise Exception("either config_file or task_id must be supplied")

                break

            if (config.code_directory / "stop").exists():
                stop_reason = f"Stopping - stop file found"
                common.quietly_delete((config.code_directory / "stop"))
                break

            iteration = None
            log_output = ""
            try:
                iteration = config.initialize_new_iteration()

                iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()

                headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Self-driving {config.code_basename} 
iteration id {config.current_iteration.id} (v{iteration_count})
sandbox root dir: {config.sandbox_root_dir}  
total spend: ${config.self_driving_task.get_cost() :.2f}/${config.max_budget_usd:.2f}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
                                    """
                print(headline)
                log(config, headline)

                iterate_on_code(
                    config,
                    iteration
                )

                log_output = execute_iteration(
                    config,
                    iteration
                )
            except AgentBlocked as agent_blocked:
                pprint.pprint(agent_blocked.blocked_data)
                stop_reason = "Agent Blocked"
                if config.self_driving_task.task_id:
                    with transaction.atomic():
                        Task.objects.filter(id=config.self_driving_task.task_id).update(
                            status=TaskStatus.BLOCKED
                        )

                    PubSubManager.publish(
                        PubSubMessageType.TASK_BLOCKED,
                        payload={
                            **agent_blocked.blocked_data,
                            "task_id": config.self_driving_task.task_id
                        }
                    )

                break
            except GoalAchieved as goal_achieved:
                stop_reason = "Goal Achieved"
                if config.self_driving_task.task_id:
                    PubSubManager.publish_id(
                        PubSubMessageType.TASK_COMPLETED,
                        config.self_driving_task.task_id
                    )

                break
            except Exception as e:
                logging.exception(e)
                config.supress_eval = True
                if config.self_driving_task.task_id:
                    PubSubManager.publish(
                        PubSubMessageType.TASK_FAILED,
                        payload={
                            "task_id": config.self_driving_task.task_id,
                            "error": traceback.format_exc()
                        }
                    )

                break
            finally:
                if iteration:
                    post_process_iteration_execution(
                        config,
                        iteration,
                        log_output
                    )

    except SelfDriverConfigException as config_exception:
        print(f"unable to load config file {config_file}:  {config_exception}")
    finally:
        print("STOP REASON", stop_reason)
        # if not supress_eval:
        #     wrap_up(config)


def execute_eval(config_file: Path = None, task_id: str = None):
    wrap_up(
        SelfDriverConfig.get(config_file, task_id)
    )


def iterate_on_code(config: SelfDriverConfig, iteration: SelfDrivingTaskIteration) -> SelfDrivingTaskIteration:
    if not config.main_code_file.get_latest_version():
        generate_code(
            config=config,
            iteration=iteration,
            iteration_instructions=f"""
        Please write the first version of the code.  
        Please take your time to think of the best initial architecture - identify an architecture that will allow for efficient code iteration and give us the best start towards achieving the user's GOAL. 
                    """
        )
    else:
        user_msg = f"{config.code_basename} has just executed.  Please review the code and its output and then write detailed instructions for the next version of this code"
        if config.comment_requests:
            comment_requests_str = "\n\t\t*".join(config.comment_requests)
            user_msg += f"\n\n Please include the following your evaluation comments: {comment_requests_str}"

        generate_code(
            config=config,
            iteration=iteration,
            iteration_instructions=user_msg
        )


def execute_iteration(config: SelfDriverConfig, iteration: SelfDrivingTaskIteration) -> str:
    import subprocess
    import time
    import os

    logfile = common.create_temp_file(f"iteration-{str(iteration.id)}", ".execution.log")
    running_process = None

    task = iteration.self_driving_task.task
    task_execution = init_task_execution(iteration)

    try:
        if config.debug:
            # In debug mode, call the runner directly
            self_driving_coder_runner.execute(
                iteration.id,
                "dev",
                logfile
            )
        else:
            # Execute sda_execute as a managed subprocess with process tracking
            python_executable = os.path.join("env", "bin", "python")
            cmd = [python_executable, "-u", "manage.py", "sda_execute", f"--execution_id={task_execution.id}"]

            # Create RunningProcess record for tracking
            running_process, _ = RunningProcess.objects.update_or_create(
                task_execution=task_execution,
                execution_type='local',
                log_file_path=str(logfile)
            )

            # Start the subprocess
            with open(logfile, "w") as log_f:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    text=True
                )

                # Update running process with PID
                running_process.process_id = process.pid
                running_process.save(update_fields=['process_id'])

                print(f"sda_execute started with PID {process.pid}, log: {logfile}, iteration_id: {iteration.id}")

                # Wait for completion while periodically updating log tail
                while process.poll() is None:
                    running_process.update_log_tail()
                    time.sleep(2)  # Check every 2 seconds

                print(f"sda_execute finished for PID {process.pid}, log: {logfile}, iteration_id: {iteration.id}")

                # Final log tail update
                running_process.update_log_tail()
                running_process.is_running = False
                running_process.terminated_at = common.get_now()
                running_process.save(update_fields=['is_running', 'terminated_at'])

                # Check return code
                if process.returncode != 0:
                    with open(logfile, "r") as log_read:
                        error_output = log_read.read()
                    task_execution.error_msg = error_output
                    task_execution.save()
                    raise Exception(f"sda_execute failed with return code {process.returncode}: {error_output}")

        log_output = logfile.read_text()
        log(config, log_output, f"iteration-{iteration.id}")

        return log_output
    except Exception as e:
        # Mark process as failed if it exists
        if running_process and running_process.is_running:
            running_process.is_running = False
            running_process.terminated_at = common.get_now()
            running_process.save(update_fields=['is_running', 'terminated_at'])

        log_output = logfile.read_text()
        log_output = f"{log_output}\n\n\nthrew:\n{traceback.format_exc()}"
        log(config, log_output, f"iteration-{iteration.id}")
    finally:
        common.quietly_delete(logfile)


def init_task_execution(iteration):
    task = iteration.self_driving_task.task

    task_input = {}
    for upstream_task in task.depends_on.all():
        if not TaskStatus.COMPLETE.eq(upstream_task.status):
            raise AgentBlocked(f"task {task.id} depends on task {upstream_task.id}, but the upstream task's status is {upstream_task.status}")

        previous_task_execution = upstream_task.get_last_execution()
        if not previous_task_execution:
            raise AgentBlocked({
                "desc": f"task {task.id} depends on upstream task {upstream_task.id}, but the upstream task has not executed"
            })

        task_input[upstream_task.id] = previous_task_execution.output

    return task.create_execution(
        input_data=task_input,
        iteration=iteration
    )


def post_process_iteration_execution(
        config,
        iteration,
        log_output
):
    # if artifacts dir is empty, delete it
    artifacts_dir = Path(config.artifacts_dir)
    artifacts_parent_dir = artifacts_dir.parent

    assert artifacts_parent_dir.name == ARTIFACTS

    if artifacts_dir.exists() and not any(artifacts_dir.iterdir()):
        artifacts_dir.rmdir()

    if artifacts_parent_dir.exists() and not any(artifacts_parent_dir.iterdir()):
        artifacts_parent_dir.rmdir()

    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=iteration.id).update(
            log_content=log_output or "no log output"
        )


def build_iteration_context_messages(config: SelfDriverConfig) -> List[LlmMessage]:
    messages = [
        get_goal_msg(config)
    ]

    eval_json = common.get(config, ["previous_iteration", "evaluation_json"], {})
    if isinstance(eval_json, dict):
        previous_iteration_count = eval_json.get("previous_iteration_count", 1)
        if previous_iteration_count == "all":
            previous_iteration_count = config.self_driving_task.selfdrivingtaskiteration_set.count()
    else:
        previous_iteration_count = 1

    previous_iteration_count = min(5, previous_iteration_count)

    previous_iterations = list(reversed(config.self_driving_task.selfdrivingtaskiteration_set.order_by("-timestamp")[0:previous_iteration_count]))
    for task_iteration in previous_iterations:
        if task_iteration == config.current_iteration:
            continue

        for code_version in task_iteration.codeversion_set.all():
            if Path(code_version.code_file.file_path).exists():
                messages.append(
                    LlmMessage.assistant(
                        f"iteration id={str(task_iteration.id)} code_file_path {code_version.code_file.file_path}",
                        code_version.code_file.file_path
                    )
                )

        eval_json = task_iteration.evaluation_json or {}
        evaluation = None
        instructions = None
        if isinstance(eval_json, dict):
            try:
                evaluation = eval_json['evaluation']
                instructions = eval_json['instructions']
            except Exception as e:
                pass
        elif isinstance(eval_json, list):
            evaluation = eval_json

        if evaluation:
            messages.append(
                LlmMessage.user(
                    f"""
            These are the results of executing iteration id={str(task_iteration.id)}: 
            {json.dumps(evaluation, indent=4)}
                    """
                )
            )

        if instructions:
            messages.append(
                LlmMessage.assistant(
                    f"""
            I have evaluated the execution results for iteration id={str(task_iteration.id)}.  Please make the following modifications:
            {json.dumps(instructions, indent=4)}
                    """
                )
            )

        if not (evaluation or instructions):
            messages.append(
                LlmMessage.user(
                    f"""
            The output of executing iteration id={str(task_iteration.id)} is as follows:
            {task_iteration.log_content}
                    """
                )
            )

    return messages


def get_sys_prompt(file_name: str, replacements: tuple[str, str] = None) -> LlmMessage:
    msg = (PROMPTS_DIR / file_name).read_text()
    for look_for_str, replace_with_str in common.ensure_list(replacements):
        msg = msg.replace(look_for_str, replace_with_str)
    return LlmMessage.sys(msg)


def build_system_message_planning(config: SelfDriverConfig):
    business = config.self_driving_task.business
    main_file_name = config.main_code_file.get_path().name

    if config.main_code_file_test:
        test_file_name = config.main_code_file_test.get_path().name
    else:
        test_file_name = None

    if config.generate_single_file:
        if config.self_driving_task.get_require_tests():
            files_strategy = " ".join([
                f"The functional (non-test, non-Dockerfile) code should be contained in a single file named '{main_file_name}' and tested by '{test_file_name}'.",
                f"You will keep all of the functional code you generate in this single file.",
                f"Be sure to list '{main_file_name}' and '{test_file_name}' in `code_files` list in the response datastructure with the associated instructions"
            ])
        else:
            files_strategy = " ".join([
                f"The functional (non-test, non-Dockerfile) code should be contained in a single file named '{main_file_name}'.",
                f"You will keep all of the functional code you generate in this single file.",
                f"Be sure to list '{main_file_name}' in `code_files` list in the response datastructure with the associated instructions"
            ])
    else:
        if config.self_driving_task.get_require_tests():
            files_strategy = " ".join([
                f"The main entry point for the code should be an 'execute' method in the file named '{main_file_name}' and shall be tested by tests in '{test_file_name}'",
                f"You may keep all code in '{main_file_name}' and tests in '{test_file_name}'",
                "but if appropriate you may define new code files.",
                "In any case, list all code files (and related instructions) in the `code_files` list in the response datastructure"
            ])
        else:
            files_strategy = " ".join([
                f"The main entry point for the code should be an 'execute' method in the file named '{main_file_name}'.",
                f"You may keep all code in '{main_file_name}', but if appropriate you may define new code files.",
                "In any case, list all code files (and related instructions) in the `code_files` list in the response datastructure"
            ])

    messages = [
        get_sys_prompt(
            "worker_coder--planning.md",
            [
                ("<aws_tag>", str(business.service_token)),
                ("<db_name>", str(business.service_token)),
                ("<iam_role_name>", str(business.get_iam_role_name())),
                ("<code_directory>", str(config.code_directory)),
                ("<sandbox_dir>", str(config.sandbox_root_dir)),
                ("<files_strategy>", files_strategy)
            ]
        )
    ]

    if config.guidance:
        messages.append(config.guidance)

    if config.self_driving_task.get_require_tests():
        messages.append(
            get_sys_prompt("worker_coder--automated_tests.md")
        )

    messages.append(
        get_helper_methods_msg(config),
    )
    messages.append(
        get_dependencies_msg()
    )

    if config.is_model_trainer:
        messages.append(get_sys_prompt(
            "worker_coder--ml_coder.md",
            [
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<execute_module>", config.code_basename)
            ]
        ))

    return messages


def generate_code(
        config: SelfDriverConfig,
        iteration: SelfDrivingTaskIteration,
        iteration_instructions: str
) -> SelfDrivingTaskIteration:
    messages = [
        *build_system_message_planning(config),
        *build_iteration_context_messages(config),
        *[LlmMessage.user(s) for s in common.ensure_list(iteration_instructions)]
    ]

    price = 0

    llm_response_planning = get_planning_llm_response(
        config,
        messages
    )
    price += llm_response_planning.price_total

    planning_data, post_process_price = ensure_parsable_json(
        config,
        llm_response_planning.text
    )
    pprint.pprint(planning_data)
    price += post_process_price

    planning_data['planning_model'] = str(config.planning_model)
    evaluation_of_previous_code = planning_data.get("evaluation", [])
    goal_achieved = common.parse_bool(planning_data.get('goal_achieved'))

    if config.previous_iteration:
        with transaction.atomic():
            SelfDrivingTaskIteration.objects.filter(id=config.previous_iteration.id).update(
                achieved_goal=goal_achieved,
                evaluation_json=planning_data
            )

    blocked_data = planning_data.get('blocked')
    if blocked_data:
        raise AgentBlocked(blocked_data)

    iteration_id_to_modify = planning_data.get("iteration_id_to_modify", "latest")
    try:
        iteration_to_modify = config.self_driving_task.selfdrivingtaskiteration_set.get(id=iteration_id_to_modify)
    except:
        iteration_to_modify = config.previous_iteration

    print(f"\n{json.dumps(planning_data, indent=4)}\n")

    if not config.never_done and goal_achieved:
        raise GoalAchieved(planning_data)

    if not iteration_to_modify:
        iteration_to_modify = iteration

    code_file_instructions = planning_data.get("code_files", [])
    if not code_file_instructions:
        raise Exception("no code files found")

    for cfi in code_file_instructions:
        code_file_path_str: str = cfi.get("code_file_path")
        if code_file_path_str.startswith(str(config.sandbox_root_dir)):
            code_file_path_str = code_file_path_str[len(str(config.sandbox_root_dir)) + 1:]

        code_file_path = config.sandbox_root_dir / code_file_path_str
        if not code_file_path:
            raise Exception(f"missing code file name: {json.dumps(cfi)}")

        code_version_to_modify = iteration_to_modify.get_code_version(
            code_file_path
        )
        code_file = code_version_to_modify.code_file

        instructions = cfi.get("instructions", [])
        if not instructions:
            print(f"no modifications for {code_file_path}")
            code_file.update(
                config.current_iteration,
                code_version_to_modify.code
            )
        else:
            previous_exception = None
            code_str = None
            for i in range(3):
                try:
                    previous_exception = None
                    llm_response_codegen = get_coding_llm_response(
                        config=config,
                        code_version_to_modify=code_version_to_modify,
                        evaluation_of_previous_code=evaluation_of_previous_code,
                        instructions=instructions,
                        previous_exception=previous_exception
                    )
                    price += llm_response_codegen.price_total

                    code_str = llm_response_codegen.text

                    break
                except CodeCompilationError as e:
                    previous_exception = e

            print(f"Iteration total cost: ${price:.4f}")
            if previous_exception:
                raise previous_exception

            if code_str:
                code_file.update(
                    config.current_iteration,
                    code_str,
                    code_instructions=instructions
                )

    return iteration


def ensure_parsable_json(config, json_text: str) -> dict:
    orig_json_text = json_text
    price = 0
    last_e = None
    for i in range(5):
        if not json_text:
            raise Exception(f"json_text is empty")

        while len(json_text) > 0 and json_text[0] != "{":
            json_text = json_text[1:]

        while len(json_text) > 0 and json_text[-1] != "}":
            json_text = json_text[:-1]

        if common.is_empty(json_text):
            raise Exception(f"unable to parse json\n{orig_json_text}")

        try:
            parsed_text = json.loads(json_text)
            return parsed_text, price
        except Exception as e:
            # print(f"----------\n{json_text}\n\n{e}\n--------------")

            last_e = e
            llm_response_reformat = llm_interface.chat(
                f"""
please format and return the following json text as valid and parsable json:

========= json text start ================
{json_text}
========= json text end ================


the previous attempt at parsing this content resulted in this error:  {e}


resond only with parsable json.  do not include any comments, explanations, or non-json markdown
""",
                LlmModel.OPENAI_O3_MINI,
                code_response=True
            )
            price += llm_response_reformat.price_total
            log_llm_response(config, llm_response_reformat, suppress_log=True)
            json_text = llm_response_reformat.text

    raise last_e


def get_planning_llm_response(
        config,
        messages: List[LlmMessage]
) -> LlmResponse:
    model = config.planning_model

    messages_with_file = [m for m in messages if m.file]

    files = common.filter_none([m.file for m in messages_with_file])
    if files:
        print("FILES IN THE CONTEXT")
        for f in files:
            print(f"cat {os.path.abspath(f)}")
        print(" ")
        print(" ")

    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)

    if max_tokens:
        print(f"about to call out to {model} to plan optimizations.  {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"about to call out to {model} to plan optimizations.  {token_count:,} tokens used")

    log_llm_request(config, messages)
    llm_response_planning = llm_interface.chat(
        messages,
        model,
        output_schema=PROMPTS_DIR / "worker_coder--planning.md.schema.json",
        code_response=True
    )
    log_llm_response(config, llm_response_planning)

    print(f"\t\tcost of planning: total ${llm_response_planning.price_total:.4f}; input ${llm_response_planning.price_input:.4f}; output ${llm_response_planning.price_output:.4f}")

    return llm_response_planning


def get_coding_llm_response(
        config: SelfDriverConfig,
        code_version_to_modify: CodeVersion,
        evaluation_of_previous_code,
        instructions,
        previous_exception: Optional[CodeCompilationError]
) -> LlmResponse:
    model = LlmModel.OPENAI_O3_MINI

    code_file_name = code_version_to_modify.code_file.get_path().name
    if code_file_name.endswith(".py"):
        prompt = "worker_coder--python_coder.md"
    elif code_file_name == "requirements.txt":
        prompt = "worker_coder--requirements.txt.md"
    elif code_file_name.startswith("Dockerfile"):
        prompt = "worker_coder--dockerfile_coder.md"
    else:
        raise Exception(f"no coder implemented for {code_file_name}")

    messages: list[LlmMessage] = [
        get_sys_prompt(
            prompt,
            ("<sandbox_dir>", str(config.sandbox_root_dir))
        ),
        get_helper_methods_msg(config),
        get_dependencies_msg()
    ]

    if code_file_name == "requirements.txt":
        messages.append(LlmMessage.sys("requirements.txt files are always plain text - they shall never contain python code"))

    if config.is_model_trainer:
        messages.append(get_sys_prompt(
            "worker_coder--ml_coder.md",
            [
                ("<artifacts_directory>", str(config.artifacts_dir)),
                ("<execute_module>", config.code_basename)
            ]
        ))

    if previous_exception:
        messages.append(
            LlmMessage.user(
                f"""
This is code from the previous attempt to generate code based on the instruction set:
{previous_exception.code_str}

This code as the following error(s): 
{str(previous_exception)}

Please try again, avoiding these errors
        """
            )
        )
    elif evaluation_of_previous_code and config.previous_iteration:
        messages.append(
            LlmMessage.assistant(
                f"""
Your evaluation of the previous iteration's (iteration id={str(config.previous_iteration.id)}) performance against the user's GOAL is as follows:

{json.dumps(evaluation_of_previous_code, indent=4)}
                """
            )
        )

    code_file_path = code_version_to_modify.code_file.file_path
    if code_version_to_modify.code:
        print("modifying", code_file_path)

        messages.append(
            LlmMessage.user(
                f"iteration id={str(code_version_to_modify.task_iteration.id)} code_file_path {code_file_path}",
                code_version_to_modify.code_file.file_path
            )
        )

        messages.append(
            LlmMessage.user(
                f"""
Modify {code_file_path} using these instructions

Never use the self-modification approach. Only return the final version of the Python file

{json.dumps(instructions, indent=4)}

    """
            )
        )

    else:
        messages.append(
            LlmMessage.user(
                f"""
please write the initial version of {code_file_path}, following each of these instructions exactly and in order:

{json.dumps(instructions, indent=4)}
        """
            )
        )

    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)

    if max_tokens:
        print(f"about to call out to {model} to generate code.  {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"about to call out to {model} to generate code.  {token_count:,} tokens used")

    log_llm_request(config, messages)
    llm_response_codegen = llm_interface.chat(
        messages,
        model,
        code_response=True
    )

    log_llm_response(config, llm_response_codegen)

    print(f"\t\tcost of code gen: total ${llm_response_codegen.price_total:.4f}; input ${llm_response_codegen.price_input:.4f}; output ${llm_response_codegen.price_output:.4f}")

    return llm_response_codegen


def get_dependencies_msg():
    return LlmMessage.sys(
        f"""
Dependencies

You may only use the libraries listed in the following requirements.txt:
========== begin requirements.txt ================
{Path('./requirements.txt').read_text()}
========== end requirements.txt ================
"""
    )


def get_helper_methods_msg(config) -> LlmMessage:
    return LlmMessage.sys(
        f"""
Helper Methods

You may use the helper methods defined in agent_tools.py
========== begin agent_tools.py ================
{(Path(__file__).parent / "agent_tools_stub.py").read_text()}
========== end agent_tools.py ================

If you use any of these helper methods, you must import agent_tools with the following line of code:
from erieiron_autonomous_agent.business_level_agents.self_driving_coder import agent_tools

if a helper method has a parameter named 'business_id', use the following value for business_id: "{config.self_driving_task.business_id or None}"
if a helper method has a parameter named 'task_id', use the following value for task_id: "{config.self_driving_task.task_id or None}"
"""
    )


def log_llm_request(config: SelfDriverConfig, llm_messages: list[LlmMessage]):
    config.log_path.touch(exist_ok=True)
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    log(config, f"LlmRequest")
    for m in common.ensure_list(llm_messages):
        log(config, m, prefix="\t")
        log(config, "\n")
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")


def log_llm_response(config, llm_response: LlmResponse, suppress_log=False):
    LlmRequest.objects.create(
        task_iteration=config.self_driving_task.get_most_recent_iteration(),
        token_count=llm_response.token_count,
        price=llm_response.price_total
    )

    if not suppress_log:
        config.log_path.touch(exist_ok=True)
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, f"Response from {llm_response.model.label()}")
        log(config, llm_response.text, prefix="\t")
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, "\n")


def log(config: SelfDriverConfig, s: str, prefix: str = None):
    prefix = prefix or ""
    config.log_path.touch(exist_ok=True)
    with open(config.log_path, 'a') as messages_log:
        for line in common.safe_split(s, "\n"):
            messages_log.write(f"{prefix}{line}\n")


def wrap_up(config: SelfDriverConfig):
    if not config:
        return

    messages = []

    messages.append(get_goal_msg(config))

    aval_iterations = []
    for best_iteration in config.self_driving_task.selfdrivingtaskbestiteration_set.filter(iteration__evaluation_json__isnull=False).order_by("timestamp"):
        aval_iterations.append(best_iteration.iteration_id)

    if not aval_iterations:
        aval_iterations = [
            i.id for i in config.self_driving_task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).order_by("timestamp")
        ]

    for iteration in SelfDrivingTaskIteration.objects.filter(id__in=aval_iterations).order_by("timestamp"):
        messages.append(
            LlmMessage.assistant(
                f"""
Iteration id={str(iteration.id)} ({iteration.timestamp}) results:
{json.dumps(iteration.evaluation_json, indent=4)}

NOTE: Iteration id={str(iteration.id)} {'successfully achieved the user''s GOAL' if iteration.achieved_goal else 'did not achieve the user''s GOAL'}  
                """
            )
        )

    aval_iteration_ids_str = ", ".join([str(s) for s in aval_iterations])

    if config.is_model_trainer:
        messages.append(
            LlmMessage.sys(
                f"""
You are expert level at evaluating model training results and choosing the best result

YOUR TASKS:  
1)  Look through ALL of the attached iteration results and identify the id of the Iteration that BEST ACHIEVES THE GOAL
    -  if an iteration has one or more CRITICAL ISSUES, RuntimeError, or exceptions described in its results, it is excluded from consideration for best_iteration
    -  if there is a metric value associated with the GOAL, use the metric value to identify the best iteration
2)  Respond with a json dict in the following format:
    ========= example response format ===========
    {{
        "best_iteration_id": the uuid id representing the iteration that you think best achieves the GOAL.  must be one of these values: {aval_iteration_ids_str},
        "summary":  a summary of why you chose that iteration for best - if there is a metric you are using to rank the iterations and pick the best, name the metric and the best_iteration's metric value,
        "details":  markdown formatted details a) expanding on why you chose that iteration  and b) an evaluation on how that iteration's model would perform in a production environment,
        "curriculum":  markdown formatted teaching lessons gleened from all of the iterations that got us to the best iteration.  This can be a really long response with many subject sections.  The audience for the curriculum response is a junior engineer trying to learn all they can about high-level area (machine-learning for example).  the goal here is to help the junior engineer build out their mental model, so real world examples and leaning into the 'why' is important.  NOTE:  Please do not mention the word 'curriculum' or name the audience in this response
        
    }}
    ========= end example response format ===========

RESPOND ONLY WITH IMMEDIATELY PARSEABLE JSON IN THE EXAMPLE RESPONSE FORMAT
        """
            ))
    else:
        messages.append(
            LlmMessage.assistant(
                f"""
You are a principal level engineer who loves reviewing code and teaching about programming  

YOUR TASKS:  
1)  Look through ALL of the attached iteration results and identify the id of the Iteration that BEST ACHIEVES THE GOAL
    -  if an iteration has one or more CRITICAL ISSUES, RuntimeError, or exceptions described in its results, it is excluded from consideration for best_iteration
    -  if there is a metric value associated with the GOAL, use the metric value to identify the best iteration
2)  Respond with a json dict in the following format:
    ========= example response format ===========
    {{
        "best_iteration_id": the uuid id representing the iteration that you think best achieves the user's GOAL.  must be one of these values: {aval_iterations},
        "summary":  a summary of why you chose that iteration for best - if there is a metric you are using to rank the iterations and pick the best, name the metric and the best_iteration's metric value,
        "details":  markdown formatted details a) expanding on why you chose that iteration  and b) an evaluation on how the code would perform in a production environment,
        "curriculum":  markdown formatted teaching lessons gleened from all of iterations that got us to the best iteration.  This can be a really long response with many subject sections.  The audience for the curriculum response is a junior engineer trying to learn all they can about writing quality performant code.  the goal here is to help the junior engineer build out their mental model, so real world examples and leaning into the 'why' is important.  NOTE:  Please do not mention the word 'curriculum' or name the audience in this response
    }}
    ========= end example response format ===========

RESPOND ONLY WITH IMMEDIATELY PARSEABLE JSON IN THE EXAMPLE RESPONSE FORMAT
            """
            ))

    print(f"reviewing the {len(aval_iterations)} iterations and generating a final evaluation...")

    log_llm_request(config, messages)
    llm_response = llm_interface.chat(
        messages,
        LlmModel.GEMINI_2_5_PRO,
        code_response=True
    )
    log_llm_response(config, llm_response)

    print(f"got llm response len: {len(llm_response.text)}")
    eval_data, _ = ensure_parsable_json(config, llm_response.text)

    best_iteration = SelfDrivingTaskIteration.objects.get(id=common.get(eval_data, 'best_iteration_id'))
    best_version_num = best_iteration.version_number

    code_versions: list[CodeVersion] = \
        list(best_iteration.codeversion_set.filter(code_file=config.main_code_file)) \
        + list(best_iteration.codeversion_set.exclude(code_file=config.main_code_file))

    best_iteration_code = []
    for cv in code_versions:
        best_iteration_code.append(f"""
======== start {cv.code_file.file_path}'s code =============
{cv.code}
======== end {cv.code_file.file_path}'s code =============
        """
                                   )
    best_iteration_code = "\n".join(best_iteration_code)

    main_file_name = config.main_code_file.get_base_name()
    if config.is_model_trainer:
        messages = LlmMessage.user(f"""
    Please review the following code and write markdown formatted details fully explaining {config.main_code_file.get_base_name()}'s model architecture and training strategy:
{best_iteration_code}

    Policies that must be followed:
        - give as much detail as needed.  
        - the audience for this is a mid-level ML research scientist.  If there are things you'd like to teach to a mid-level ML research scientist, please add this content as well
        - DO NOT include the string "```markdown" anywhere in the response
    """)

        log_llm_request(config, messages)
        arch_llm_response = llm_interface.chat(messages, LlmModel.GEMINI_2_5_PRO)
        log_llm_response(config, arch_llm_response)
    else:
        messages = LlmMessage.user(f"""
        Please review the following code and write markdown formatted details fully explaining {main_file_name}'s architecture and execution steps
        {best_iteration_code}

        Policies that must be followed:
            - give as much detail as needed.  
            - the audience for this is a mid-level engineer.  If there are things you'd like to teach to a mid-level engineer, please add this content as well
            - DO NOT include the string "```markdown" anywhere in the response
        """)

        log_llm_request(config, messages)
        arch_llm_response = llm_interface.chat(messages, LlmModel.GEMINI_2_5_PRO)
        log_llm_response(config, arch_llm_response)

    planning_model = best_iteration.planning_model
    if planning_model:
        planning_model = f"({planning_model})"
    else:
        planning_model = ""

    curriculum = common.get(eval_data, 'curriculum', "")
    if curriculum:
        curriculum_lines = curriculum.split("\n")
        if common.default_str(curriculum_lines[0]).lower().startswith("### "):
            curriculum_lines = curriculum_lines[1:]
            curriculum = "\n".join(curriculum_lines)

    if config.is_model_trainer:
        checkpoints_dir = config.artifacts_dir

        if checkpoints_dir:
            checkpoint_files = [f for f in Path(checkpoints_dir).iterdir() if f.is_file()]
            s3_dir = f"{config.code_basename}/{config.code_basename}_v{best_version_num}"
        else:
            checkpoint_files = []
            s3_dir = None

        for file in checkpoint_files:
            get_aws_interface().upload_file(
                file,
                settings.BUCKETS[S3Bucket.MODELS],
                f"{s3_dir}/{file.name}"
            )

        print(f"model checkpoints uploaded to s3://{settings.BUCKETS[S3Bucket.MODELS]}/{s3_dir}")

        txt_output = f"""
# Best iteration
Iteration #{best_version_num} {planning_model}
Code:  {main_file_name}
### Checkpoints:
s3://{settings.BUCKETS[S3Bucket.MODELS]}/{s3_dir}

# Summary
{common.get(eval_data, 'summary')}

# Details
{common.get(eval_data, 'details')}

# Architecture
{arch_llm_response.text}

# Notes
{curriculum}
    """
    else:
        txt_output = f"""
# Best iteration
Iteration #{best_version_num} {planning_model}
Code:  {main_file_name}

# Summary
{common.get(eval_data, 'summary')}

# Details
{common.get(eval_data, 'details')}

# Architecture
{arch_llm_response.text}

# Notes
{curriculum}
            """

    archive_path = Path(settings.BASE_DIR) / f"task_{config.code_basename}_v{best_version_num}.zip"
    with tempfile.TemporaryDirectory() as tmpdirname:
        with zipfile.ZipFile(archive_path, 'w') as zipf:
            tmpdirname = Path(tmpdirname)
            print(f"created temporary directory at {tmpdirname}")

            eval_text_path = tmpdirname / f"{config.code_basename}.full-eval.md"
            eval_text_path.write_text(txt_output)
            zipf.write(
                eval_text_path,
                arcname=os.path.relpath(eval_text_path, tmpdirname)
            )

            zipf.write(
                config.log_path,
                arcname=config.log_path.name
            )

            for cv in best_iteration.codeversion_set.all():
                tmp_code_file = tmpdirname / cv.code_file.file_path
                tmp_code_file.parent.mkdir(parents=True, exist_ok=True)
                tmp_code_file.write_text(cv.code)
                zipf.write(
                    tmp_code_file,
                    arcname=os.path.relpath(tmp_code_file, tmpdirname)
                )

    common.quietly_delete(tmpdirname)
    print(txt_output)

    print(f"""grab artifacts from
scpc "{os.path.abspath(archive_path)}"
    """)

    config.self_driving_task.rollback_to(best_iteration)
    print(f"code updated to iteration v{best_version_num}")


def get_goal_msg(config):
    return LlmMessage.user(f"""
the user's GOAL is:
{config.self_driving_task.goal}""")
