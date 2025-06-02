import json
import logging
import os
import pprint
import random
import tempfile
import zipfile
from pathlib import Path
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

import settings
from erieiron_common import common
from erieiron_common.aws_utils import get_aws_interface
from erieiron_common.codegen_utils import lint_and_format, CodeCompilationError
from erieiron_common.enums import LlmRole, LlmModel, S3Bucket
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import CODE_MODELS_IN_ORDER, LlmMessage, MODEL_TO_MAX_TOKENS, LlmResponse
from erieiron_common.models import CodeFile, SelfDrivingTask, CodeVersion, SelfDrivingTaskIteration, LlmRequest

ARTIFACTS = "artifacts"

COUNT_FULL_LOGS_IN_CONTEXT = 2


class GoalAchieved(Exception):
    def __init__(self, planning_data):
        self.planning_data = planning_data


SYSTEM_MESSAGE_PLANNING = """
You are a Principal Engineer and expert in generating structured instructions for code generation. 

Your task is to
1) fully understand the user's GOAL
2) evaluate the code and logs from previous iterations if they exist.  
    - The previous iteration messages are in order oldest to newest.  Priorize evaluation of the very latest code and output first and give it context priority.  Then look to previous versions as necessary to add to context of previous changes
    - if no code or logs exist, proceed solely based on the GOAL
3) from your review of the previous iterations code and logs, identify optimizations to the previous code that you think will get it closer to achieving the stated GOAL
4) then produce clear, unambiguous instructions that the code generation model can follow exactly to implement the optimization from step 3.  This will be a combination of modifying existing code and optionally new code files
5) you can keep all code in the single main code file, but if you think it's better feel free to define new code files in the code_files list

Output your answer as a json object with 
    a key "best_iteration_id" mapping to the id of what you think is the 'best' iteration so far.  value should be null if this is the first iteration of the code
    a key "iteration_id_to_modify" mapping to the id of the iteration you'd like to modify in the next version of the code.  this is useful if the code has gone down a bad path and you want to revert to a previous version
      - If you'd like to modify the latest version of the code, you can just say "latest".  
      - of, if you'd like to revert back to a previous iteration of the code prior to making your changes, iteration_id_to_modify maps to the id of the iteration you'd like to revert back to
    a key "evaluation" mapping to a list of evaluation items identified in both step 2 and requested in the user prompt. each evaluation object must include:
      - "summary": a short summary of the evaluation item
      - "details": rich details on the evaluation item.  use this area to teach when applicable
    a key "code_files" mapping to a list of code_file data structures.  For each data structure in the code_files list shall contain the following keys:
        a key "code_file_path" mapping to the path (relative to working directory) of the code file to add or modify
        a key "instructions" mapping to a list of instruction objects. each instruction object must include:
          - "step_number": a sequential number (starting at 1)
          - "action": a concise description of the required modification or additions to the code file
          - "details": additional context or specifics to clarify the action
          NOTE:  if no modifications a required for the file, "instructions" shall be an empty list ([])
    a key "goal_achieved" mapping to a boolean value indicating if you think we have achieved the user's GOAL with at least 97% confidence
    a key "previous_iteration_count" mapping to a value indicating the number of previous iterations your feel are useful to your task.  
      - If an iteration is before this number, we won't include it in the context for future evaluations
      - If you think all are useful, set the value to the string 'all'
      
      
    ensure that each instruction is self-contained, clearly defined, and provides all necessary context. avoid ambiguity or unnecessary verbosity.
    example output:{
        "goal_achieved": false,
        "previous_iteration_count": <number of previous iterations | 'all'>,
        "best_iteration_id": <id of best iteration>
        "iteration_id_to_modify": "latest" or the id of the previous iteration of the code you'd like to revert to
        "evaluation": [
            {
                "summary": "logs reflected we are getting closer to the user's GOAL",
                "details": "lots of details in support of logs reflected we are getting closer to the user's GOAL"
            }
        ],
        "code_files": [
            {
                "code_file_path": "./dir1/dir2/code_file1.py",
                "instructions": [
                    {
                        "step_number": 1,
                        "action": "modify batch_size variable from 12 to 24",
                        "details": "increasing the batch size will allow for more efficient learning"
                    },
                    {
                        "step_number": 2,
                        "action": "modify cnn layers to 6",
                        "details": "more layers, more features"
                    },
                    ...
                ],
            },
            {
                "code_file_path": "./dir1/dir2/code_file2.py",
                "instructions": [],
            },
            ...
        ]
    }

Important Policies.  All policies must be followed:
  - You will never do anything that would be destructive to your outside environment.  If unsure, don't do it and raise an exception.
  - You may only create, edit, or delete files within the <sandbox_dir>. Use Path(__file__).parent / "<filename>" for all file paths.
  - Never use absolute paths or paths outside the allowed directory.
  - If multiple issues exist in the previous code, attempt to address all issues you find
  - Experiment with various techniques, including adjusting variable values, to achieve the user's GOAL.
  - If recent iterations have plateaued, consider experimenting with bold, unconventional strategies to spark further progress.
  - If previous runs were killed by the keyboard, assume the previous run was going too slow and you need to make it run faster
  - If previous runs failed because of exception, go back to the previous 'good' file and modify that one.  Do not modify the file that generated the exception
  - Ensure that optimizations are genuinely effective and do not rely on superficial fixes.
"""

SYSTEM_MESSAGE_CODE = f"""
You are an expert python code generator. 

Security & File Constraints
 • You must never generate self-modifying code. Code should not read or write to its own file.
 • You may only create, edit, or delete files within the <sandbox_dir> directory. Use Path(__file__).parent / "<filename>" for all file paths.
 • Never use absolute paths or paths outside the allowed directory.

Output Format
 • Your response must contain only raw, valid Python code. No explanations, no markdown formatting.

Iteration & Logging
 • You are part of an iterative code loop. Each version builds toward a defined GOAL.
 • Include helpful print() logs and metrics to track success and support future improvement.
 • Use tqdm to show progress in long-running loops.
 • Cache any API or asset fetches that will remain constant between runs.

Code Quality
 • Remove unused imports.
 • All code must be free of bugs (e.g., missing imports).
 • Follow this style:
 • Use snake_case for variable and function names
 • Comments should be lowercase and only used for non-obvious logic

Dependencies
You may only use the libraries listed in the following requirements.txt:
========== begin requirements.txt ================
{Path('./requirements.txt').read_text()}
========== end requirements.txt ================
"""


ADDITIONAL_SYSTEM_MESSAGE_ML_CODE = f"""
Additional Important Policies.  All policies must be followed:
  - the main code file MUST save the ensemble checkpoints to a directory named <artifacts_directory>
        - The checkpoint files need to have all of the data required to use the model at a later point for inference
  - the main code file must expose a method named "def infer(obj)" on the <execute_module> - ie it should expose "<execute_module>.infer(obj)"
        - the infer(obj) method accepts a single item from the test dataset, and returns the inferred value or values
        - the infer(obj) method must load a model from the checkpoints in <artifacts_directory> (or ensemble checkpoints in <artifacts_directory>).  This is necessary to validate the model can be reconstructed from the checkpoint files
        - the final test evaluation must use this infer() method when scoring the performance of the model
        - if you want to use the infer(obj) method in the train or test steps, that is ok but not necessary
  - the code must plot learing rate and loss curve diagrams and same them to the directory <artifacts_directory>
"""


class SelfDriverConfigException(Exception):
    pass


class SelfDriverConfig:
    def __init__(self, config_file: Path):
        self.config_file = common.assert_exists(config_file)
        if self.config_file.name.endswith(".json"):
            try:
                config = common.read_json(self.config_file)
            except Exception as e:
                raise SelfDriverConfigException(e)
        else:
            config = {
                "goal": self.config_file.read_text()
            }

        config_base_name = common.get_basename(self.config_file)

        self.main_file = Path(config.get("main_file", config_file.parent / f"{common.get_basename(config_file)}.py"))
        self.main_code_file = CodeFile.get(self.main_file)
        self.sandbox_root_dir = config.get("sandbox_root_dir", self.main_file.parent)
        self.code_directory = self.main_file.parent
        self.code_basename = common.get_basename(self.main_file)

        self.task = SelfDrivingTask.get_or_create(
            config_file=str(config_file),
            sandbox_root_dir=self.sandbox_root_dir,
            business_name=config.get("business")
        )
        self.previous_iteration = self.task.get_most_recent_iteration()

        self.current_iteration = None
        self.artifacts_dir = None
        self.task_log = Path(settings.BASE_DIR) / f"{self.code_basename}_task.log"

        self.is_model_trainer = common.parse_bool(config.get("is_model_trainer", "False"))

        self.attachments = common.get(config, "attachments", [])
        self.comment_requests = common.get(config, "comment_requests", [])

        self.goal = "\n".join(common.ensure_list(config['goal']))
        self.llm_role = LlmRole(config.get("llm_role", LlmRole.ML_ENGINEER))
        self.never_done = common.parse_bool(config.get("never_done", False))
        self.max_budget_usd = float(config.get("max_budget_usd", 20))

        model_str = config.get("model")
        if model_str:
            self.model = random.choice(LlmModel.to_list(model_str.split(",")))
        else:
            self.model = random.choice(CODE_MODELS_IN_ORDER)

    def initialize_new_iteration(self) -> SelfDrivingTaskIteration:
        self.current_iteration = self.task.iterate()
        self.artifacts_dir = self.main_file.parent / ARTIFACTS / f"{self.code_basename}_v{self.current_iteration.version_number}"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

        iteration_count = self.task.selfdrivingtaskiteration_set.count()

        headline = f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Self-driving {self.config_file} 
iteration id {self.current_iteration.id} (v{iteration_count})
sandbox root dir: {self.sandbox_root_dir}  
total spend: ${self.task.get_cost() :.2f}/${self.max_budget_usd:.2f}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
                    """
        print(headline)
        log(self, headline)

        return self.current_iteration


def execute(config_file: Path):
    config_file = Path(config_file)

    config = None
    stop_reason = ""
    supress_eval = False
    try:
        for i in range(100):
            config = SelfDriverConfig(config_file)

            if i == 0:
                common.quietly_delete(config.task_log)
                config.task_log.touch()
                print(f"tail -f {os.path.abspath(config.task_log)}")

            total_spend = config.task.get_cost()

            if total_spend > config.max_budget_usd:
                stop_reason = f"Stopping - hit the max budget ${config.max_budget_usd:.2f}"
                break

            if (config.code_directory / "restart").exists():
                supress_eval = True
                stop_reason = f"Stopping for restart"
                common.quietly_delete((config.code_directory / "restart"))

                common.execute_management_cmd(
                    f"exec_self_driver --config={config_file}"
                )
                break

            if (config.code_directory / "stop").exists():
                stop_reason = f"Stopping - stop file found"
                common.quietly_delete((config.code_directory / "stop"))
                break

            iteration = None
            log_output = ""
            try:
                iteration = iterate_on_code(config)

                log_output = execute_iteration(
                    config,
                    iteration
                )
            except GoalAchieved as goal_achieved:
                pprint.pprint(goal_achieved.planning_data)
                stop_reason = "Goal Achieved"
                break
            except Exception as e:
                logging.exception(e)
                supress_eval = True
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
        if not supress_eval:
            wrap_up(config)


def execute_eval(config_file: Path):
    config = SelfDriverConfig(config_file)
    wrap_up(config)


def iterate_on_code(config: SelfDriverConfig) -> SelfDrivingTaskIteration:
    if not config.main_file.exists():
        iteration = generate_code(
            config=config,
            iteration_instructions=f"""
        Please write the first version of the code.  
        Please take your time to think of the best initial architecture - identify an architecture that will allow for efficient code iteration and give us the best start towards achieving the user's GOAL. 
                    """
        )

    elif not CodeVersion.objects.filter(task_iteration__task=config.task).exists():
        # this means we have a code file to iterate on, but this is the first time
        # selfdriver has seen it.  we'll just run it as is to see where we are at
        iteration = config.initialize_new_iteration()
        config.main_code_file.init_from_codefile(
            iteration,
            config.main_file
        )
    else:
        user_msg = f"{config.config_file.name} has just executed.  Please review the code and its output and then write detailed instructions for the next version of this code"
        if config.comment_requests:
            comment_requests_str = "\n\t\t*".join(config.comment_requests)
            user_msg += f"\n\n Please include the following your evaluation comments: {comment_requests_str}"

        iteration = generate_code(
            config=config,
            iteration_instructions=user_msg
        )

    return iteration


def execute_iteration(config: SelfDriverConfig, iteration: SelfDrivingTaskIteration) -> str:
    logfile = common.create_temp_file(f"iteration-{str(iteration.id)}", ".execution.log")
    common.execute_management_cmd(
        f"exec_self_driver --code_file={config.main_code_file.file_path}",
        logfile
    )
    log_output = logfile.read_text()
    common.quietly_delete(logfile)
    return log_output


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
    messages = []

    messages.append(
        LlmMessage.user(f"""
the user's GOAL is:
{config.goal}
        """)

    )

    for task_iteration in config.task.selfdrivingtaskiteration_set.order_by("timestamp"):
        if task_iteration == config.current_iteration:
            continue

        for code_version in task_iteration.codeversion_set.all():
            messages.append(
                LlmMessage.assistant(
                    f"iteration id={str(task_iteration.id)} code_file_path {code_version.code_file.file_path}",
                    code_version.code_file.file_path
                )
            )

        eval_json = task_iteration.evaluation_json
        if eval_json:
            try:
                count_evals = eval_json['evaluation']
                count_instructions = eval_json['instructions']
            except Exception as e:
                eval_json = None

        if eval_json:
            messages.append(
                LlmMessage.user(
                    f"""
            These are the results of executing iteration id={str(task_iteration.id)}: 
            {json.dumps(eval_json['evaluation'], indent=4)}
                    """
                )
            )

            messages.append(
                LlmMessage.assistant(
                    f"""
            I have evaluated the execution results for iteration id={str(task_iteration.id)}.  Please make the following modifications:
            {json.dumps(eval_json['instructions'], indent=4)}
                    """
                )
            )
        elif task_iteration.log_content:
            messages.append(
                LlmMessage.user(
                    f"""
            The output of executing iteration id={str(task_iteration.id)} is as follows:
            {task_iteration.log_content}
                    """
                )
            )

    return messages


def build_instructions_system_messages(config: SelfDriverConfig):
    messages = []

    messages.append(
        LlmMessage.sys(SYSTEM_MESSAGE_PLANNING.replace("<sandbox_dir>", str(config.sandbox_root_dir)))
    )

    if config.is_model_trainer:
        messages.append(
            LlmMessage.sys(ADDITIONAL_SYSTEM_MESSAGE_ML_CODE.replace(
                "<artifacts_directory>", str(config.artifacts_dir)
            ).replace(
                "<execute_module>", config.code_basename
            ))
        )

    return messages


def generate_code(
        config: SelfDriverConfig,
        iteration_instructions: str
) -> SelfDrivingTaskIteration:
    messages = [
        *build_instructions_system_messages(config),
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
    price += post_process_price

    planning_data['planning_model'] = str(config.model)
    evaluation_of_previous_code = planning_data.get("evaluation", [])
    goal_achieved = common.parse_bool(planning_data['goal_achieved'])

    with transaction.atomic():
        SelfDrivingTaskIteration.objects.filter(id=config.previous_iteration.id).update(
            achieved_goal=goal_achieved,
            evaluation_json=planning_data.get("evaluation", [])
        )

    iteration_id_to_modify = planning_data.get("iteration_id_to_modify", "latest")
    try:
        iteration_to_modify = config.task.selfdrivingtaskiteration_set.get(id=iteration_id_to_modify)
    except:
        iteration_to_modify = config.previous_iteration

    print(f"\n{json.dumps(planning_data, indent=4)}\n")

    if not config.never_done and goal_achieved:
        raise GoalAchieved(planning_data)

    iteration = config.initialize_new_iteration()

    code_file_instructions = planning_data.get("code_files", [])
    if not code_file_instructions:
        raise Exception("no code files found")

    for cfi in code_file_instructions:
        code_file_path = cfi.get("code_file_path")
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

                    code_str = lint_and_format(llm_response_codegen.text)
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
                LlmModel.OPENAI_GPT_O3_MINI,
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
    model = config.model

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
    model = LlmModel.OPENAI_GPT_O3_MINI

    messages = []
    messages.append(
        LlmMessage.sys(SYSTEM_MESSAGE_CODE.replace("<sandbox_dir>", str(config.sandbox_root_dir)))
    )

    if config.is_model_trainer:
        messages.append(
            LlmMessage.sys(ADDITIONAL_SYSTEM_MESSAGE_ML_CODE.replace(
                "<artifacts_directory>", str(config.artifacts_dir)
            ).replace(
                "<execute_module>", config.code_basename
            ))
        )

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


def log_llm_request(config: SelfDriverConfig, llm_messages: list[LlmMessage]):
    config.task_log.touch(exist_ok=True)
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    log(config, f"LlmRequest")
    for m in common.ensure_list(llm_messages):
        log(config, m, prefix="\t")
        log(config, "\n")
    log(config, ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")


def log_llm_response(config, llm_response: LlmResponse, suppress_log=False):
    LlmRequest.objects.create(
        task_iteration=config.task.get_most_recent_iteration(),
        token_count=llm_response.token_count,
        price=llm_response.price_total
    )

    if not suppress_log:
        config.task_log.touch(exist_ok=True)
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, f"Response from {llm_response.model.label()}")
        log(config, llm_response.text, prefix="\t")
        log(config, "<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
        log(config, "\n")


def log(config: SelfDriverConfig, s: str, prefix: str = None):
    prefix = prefix or ""
    config.task_log.touch(exist_ok=True)
    with open(config.task_log, 'a') as messages_log:
        for line in common.safe_split(s, "\n"):
            messages_log.write(f"{prefix}{line}\n")


def wrap_up(config: SelfDriverConfig):
    if not config:
        return

    messages = []

    messages.append(
        LlmMessage.user(f"""
the user's GOAL is:
{config.goal}
        """)
    )

    aval_iterations = []
    for best_iteration in config.task.selfdrivingtaskbestiteration_set.filter(iteration__evaluation_json__isnull=False).order_by("timestamp"):
        aval_iterations.append(best_iteration.iteration_id)

    if not aval_iterations:
        aval_iterations = [
            i.id for i in config.task.selfdrivingtaskiteration_set.filter(evaluation_json__isnull=False).order_by("timestamp")
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
        list(best_iteration.codeversion_set.filter(code_file__file_path=str(config.main_file))) \
        + list(best_iteration.codeversion_set.exclude(code_file__file_path=str(config.main_file)))

    best_iteration_code = []
    for cv in code_versions:
        best_iteration_code.append(f"""
======== start {cv.code_file.file_path}'s code =============
{cv.code}
======== end {cv.code_file.file_path}'s code =============
        """)
    best_iteration_code = "\n".join(best_iteration_code)

    main_file_name = config.main_file.name
    if config.is_model_trainer:
        messages = LlmMessage.user(f"""
    Please review the following code and write markdown formatted details fully explaining {config.main_file.name}'s model architecture and training strategy:
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
                config.task_log,
                arcname=config.task_log.name
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

    config.task.rollback_to(best_iteration)
    print(f"code updated to iteration v{best_version_num}")
