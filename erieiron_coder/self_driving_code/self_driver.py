import json
import logging
import os
import random
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Tuple, List

from django.utils import timezone

from aws_utils import get_aws_interface
from codegen_utils import lint_and_format, CodeCompilationError
from enums import LlmRole, LlmModel, LlmMessageType, S3Bucket
from erieiron_common import common
from erieiron_config import settings
from llm_apis import llm_interface
from llm_apis.llm_interface import CODE_MODELS_IN_ORDER, LlmMessage, MODEL_TO_MAX_TOKENS, LlmResponse

DONE = "done"
COUNT_FULL_LOGS_IN_CONTEXT = 2

SYSTEM_MESSAGE_INSTRUCTIONS = """
You are an expert in generating structured instructions for code generation. 

Your task is to
1) understand the stated GOAL
2) evaluate the code and logs from previous iterations if they exist.  
    - The previous iteration messages are in order oldest to newest.  Priorize evaluation of the very latest code and output first and give it context priority.  Then look to previous versions as necessary to add to context of previous changes
    - if no code or logs exist, proceed solely based on the GOAL
3) from your review of the previous iterations code and logs, identify optimizations to the previous code that you think will get it closer to achieving the stated goal
4) then produce clear, unambiguous instructions that the code generation model can follow exactly to implement the optimization from step 3. 

Output your answer as a json object with 
    a key "evaluation" mapping to an array of evaluation items identified in both step 2 and requested in the user prompt. each evaluation object must include:
      - "summary": a short summary of the evaluation item
      - "details": rich details on the evaluation item.  use this area to teach when applicable
    a key "instructions" mapping to an array of instruction objects. each instruction object must include:
      - "step_number": a sequential number (starting at 1)
      - "action": a concise description of the required modification or addition
      - "details": additional context or specifics to clarify the action
    a key "teaching_lessons" mapping to an array of teaching lessons.  the teaching lessons should based on and reference your evaluation and code change instructions.  The goal is to teach a junior engineer all about the techniques and theories of the code
      - "summary": a short summary of the lesson
      - "details": a detailed explaination of what you're teaching here. Give real world examples to help build a mental model.   Lean into explaining the 'why this is important'.   The audience for this content is a junior engineer trying to learn all they can about machine learning
    a key "goal_achieved" mapping to a boolean value indicating if you think we have achieved the goal with at least 97% confidence
    a key "previous_iteration_count" mapping to a value indicating the number of previous iterations your feel are useful to your task.  
      - If an iteration is before this number, we won't include it in the context for future evaluations
      - If you think all are useful, set the value to the string 'all'
    a key "file_to_modify" mapping to the name of the python file you'd like to modify.  
      - If you'd like to modify the latest version of the code, you can just say "latest".  
      - of, if you'd like to revert back to a previous version of the code prior to making your changes, file_to_modify maps to the name of this code file
    ensure that each instruction is self-contained, clearly defined, and provides all necessary context. avoid ambiguity or unnecessary verbosity.
    example output:{
        "goal_achieved": false,
        "previous_iteration_count": <number of previous iterations | 'all'>,
        "file_to_modify": "latest" or <the name of the previous version of the code you'd like to revert to>
        "evaluation": [
            {
                "summary": "logs reflected we are getting closer to our goal",
                "details": "lots of details in support of logs reflected we are getting closer to our goal"
            }
        ],
        "instructions": [
            {
                "step_number": 1,
                "action": "modify batch_size variable from 12 to 24",
                "details": "increasing the batch size will allow for more efficient learning"
            }
        ],
        "teaching_lessons": [
            {
                "summary": "increasing batch size speeds processing",
                "details": "lots of details explaining why 'increasing batch size speeds processing' and why it's important for the audience of a junior engineer trying to learn all they can about machine learning"
            }
        ]
    }

Important Policies:
  - If multiple issues exist in the previous code, attempt to address all issues you find
  - Experiment with various techniques, including adjusting variable values, to achieve the goal.
  - If recent iterations have plateaued, consider experimenting with bold, unconventional strategies to spark further progress.
  - If previous runs were killed by the keyboard, assume the previous run was going too slow and you need to make it run faster
  - If previous runs failed because of exception, go back to the previous 'good' file and modify that one.  Do not modify the file that generated the exception
  - Ensure that optimizations are genuinely effective and do not rely on superficial fixes.
"""

SYSTEM_MESSAGE_CODE = f"""
You are an expert python code generator. 

Important Policies.  All policies must be followed:
  - If the code is training an ML model, the code MUST save the ensemble checkpoints to a unique directory named similar to the name of the python file (by using __file__).
        - The checkpoint files need to have all of the data required to use the model at a later point for inference
        - Print the path the model checkpoints as one of the last log statements in the code
  - If the code is training an ML model, the code must expose a method named "def infer(obj)"
        - the infer(obj) method accepts a single item from the test dataset, and returns the inferred value or values
        - the infer(obj) method must load a model from the checkpoint (or ensemble checkpoints).  This is necessary to validate the model can be reconstructed from the checkpoint files
        - the final test evaluation must use this infer() method when scoring the performance of the model
        - if you want to use the infer(obj) method in the train or test steps, that is ok but not necessary
  - If the code is training an ML model, the code must plot learing rate and loss curve diagrams and same them to the checkpoints directory
  - You will be iterating repeatedly until we get as close to the GOAL as possible.  Include any logging that you believe will be useful for future iterations
  - The code shall log metrics that identify the code's progress against the GOAL.  These metrics will be used to identify optimizations for future iterations of the code
  - As we will be iterating, cache fetched assets or api responses between runs - if the asset or api response will not change in future runs
  - If there are long running loops, the code will illustrate progress by using tqdm
  - The python code absolutely must be free of bugs (like missing import statements, etc)
  - Your output must be valid, production-ready python code and nothing else. 
  - Do not include any markdown formatting — only the final python code should be provided.
  - You will never code anything that would be destructive to your outside environment
  - If the prompt instructs to read from file, the file path should be assumed to be relative to the python process's working directory
  - If the prompt instructs to code to write a file, the file should be saved in the same directory as the generated python file and named like this: example_file_path = Path(__file__).parent / f"{{file name from prompt}}.{{extension from prompt}}
  - Code style:  comments should be in all lower case.  only add comments for non-obvious things.  use snake_case for variable names and method names
  - The python code must have a module level method named execute() that takes no paramters.  Other code depends on this module level method being there.  It cannot be a static method on a class, must be module level
  - You may only use libraries that included in the following requirements.txt:
========== begin requirements.txt ================
{Path('./requirements.txt').read_text()}
========== end requirements.txt ================
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

        self.code_directory = self.config_file.parent
        self.code_file = self.code_directory / config.get("code_file", f"{config_base_name}.py")

        self.code_basename = common.get_basename(self.code_file)
        self.output_directory = self.code_directory / "selfdriver_output"
        self.output_directory.mkdir(exist_ok=True)
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


def execute_eval(config_file: Path):
    config = SelfDriverConfig(config_file)
    print_final_evaluation(config)


def execute(config_file: Path):
    config_file = Path(config_file)

    config = None
    stop_reason = ""
    supress_eval = False
    try:
        for i in range(100):
            config = SelfDriverConfig(config_file)

            # if i == 0:
            #     config.model = LlmModel.OPENAI_GPT_45_DO_NOT_USE_VERY_VERY_EXPENSIVE

            total_spend = get_current_spend(config)

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

            try:
                should_continue = iterate_on_code(config)
                if not should_continue:
                    stop_reason = "Goal Achieved"
                    break
            except Exception as e:
                # raise e
                print(e)
    except SelfDriverConfigException as config_exception:
        print(f"unable to load config file {config_file}:  {config_exception}")
    finally:
        print("STOP REASON", stop_reason)
        if not supress_eval:
            print_final_evaluation(config)


def iterate_on_code(config) -> Tuple[bool, float]:
    previous_python_file, previous_index = get_latest_python_file(config)

    print(f"""
--------------------------------------------------
{timezone.now().strftime("%m/%d/%Y %H:%M:%S")}

Self-driving {config.code_basename} v{previous_index + 1}
previous code {os.path.abspath(previous_python_file)} {"exists" if previous_python_file.exists() else "doesn't exist"}
model: {config.model.label()}  
total spend: ${get_current_spend(config):.2f}/${config.max_budget_usd:.2f}

https://www.youtube.com/watch?v=-Ca-2FRsTx8&t=281s
--------------------------------------------------
            """)

    if not previous_python_file.exists():
        python_file_to_execute = generate_first_code_iteration(
            config
        )
    else:
        log_file = config.output_directory / f"{config.code_basename}_{previous_index}.log"
        if not log_file.exists():
            # our latest python doesn't have a logfile.  run it to generate a log
            python_file_to_execute = previous_python_file
        else:
            # generate a new version of the file
            python_file_to_execute = generate_next_code_iteration(
                config,
                previous_index + 1,
                previous_python_file
            )

    if python_file_to_execute is not None:
        logfile = config.output_directory / f"{python_file_to_execute.name[:-3]}.log"
        common.execute_management_cmd(
            f"exec_self_driver --code_file={python_file_to_execute}",
            logfile
        )
        return True
    else:
        return False


def generate_first_code_iteration(config) -> Path:
    first_python_file = config.code_directory / f"{config.code_basename}_0.py"

    if config.code_file.exists():
        shutil.copy(config.code_file, first_python_file)
    else:
        user_message = LlmMessage(
            message_type=LlmMessageType.USER,
            text=f"""
Please write the first version of the code.  
Please take your time to think of the best initial architecture - identify an architecture that will allow for efficient code iteration and give us the best start towards achieving the goal. 
                """
        )

        messages = build_instructions_system_messages(config) + [user_message]

        code_str, evaluation, price = generate_code(
            config=config,
            messages=messages
        )

        first_python_file.write_text(code_str)

    return first_python_file


def generate_next_code_iteration(config, code_version_idx, previous_python_file: Path) -> Path:
    sys_messages = build_previous_iteration_artifacts_system_messages(config, code_version_idx)
    sys_messages += build_instructions_system_messages(config)

    user_msg = f"{previous_python_file.name} has just executed.  Please review the code and its output and then write detailed instructions for the next version of this code"
    if config.comment_requests:
        comment_requests_str = "\n\t\t*".join(config.comment_requests)
        user_msg += f"\n\n Please include the following your evaluation comments: {comment_requests_str}"
    user_message = LlmMessage(message_type=LlmMessageType.USER, text=user_msg)

    messages = sys_messages + [user_message]
    token_count = LlmMessage.get_total_token_count(config.model, messages)
    while token_count > MODEL_TO_MAX_TOKENS.get(config.model, sys.maxsize):
        sys_messages = sys_messages[1:]
        messages = sys_messages + [user_message]
        token_count = LlmMessage.get_total_token_count(config.model, messages)

    response_text = generate_code(
        config=config,
        messages=messages,
        previous_code=previous_python_file
    )

    if response_text == DONE:
        print(f"""
DONE! last response:{response_text}

https://www.youtube.com/watch?v=0VkrUG3OrPc
""")
        return None

    new_python_file = config.code_directory / f"{config.code_basename}_{code_version_idx}.py"

    print(f"""writing new code to: 
vi {os.path.abspath(new_python_file)}
""")

    new_python_file.write_text(response_text)

    return new_python_file


def build_previous_iteration_artifacts_system_messages(config, code_version_idx) -> List[LlmMessage]:
    previous_run_assets = []
    for file_idx in range(code_version_idx):
        python_file = config.code_directory / f"{config.code_basename}_{file_idx}.py"
        log_file = config.output_directory / f"{config.code_basename}_{file_idx}.log"
        eval_file = config.output_directory / f"{config.code_basename}_{file_idx}.eval.txt"

        if python_file.exists():
            previous_run_assets.append(
                (file_idx, python_file, log_file, eval_file)
            )

    latest_eval = get_latest_eval(config)
    previous_iteration_count = common.parse_int(common.get(
        latest_eval,
        "previous_iteration_count"
    ), default_val=30)

    print("previous_iteration_count", previous_iteration_count)
    previous_run_assets = previous_run_assets[-(previous_iteration_count + COUNT_FULL_LOGS_IN_CONTEXT):]

    sys_messages = []
    for idx, (file_idx, python_file, log_file, eval_file) in enumerate(previous_run_assets):
        eval_json = None
        if eval_file.exists() and idx < len(previous_run_assets) - COUNT_FULL_LOGS_IN_CONTEXT:
            eval_json, price = ensure_parsable_json(config, eval_file.read_text())
            common.write_json(eval_file, eval_json)
            try:
                count_evals = eval_json['evaluation']
                count_instructions = eval_json['instructions']
            except Exception as e:
                eval_json = None

        if eval_json:
            sys_messages.append(
                LlmMessage(
                    message_type=LlmMessageType.USER,
                    file=eval_file,
                    text=f"""
            these are the results of executing {python_file.name}
            {json.dumps(eval_json['evaluation'], indent=4)}
                    """
                )
            )

            sys_messages.append(
                LlmMessage(
                    message_type=LlmMessageType.ASSISTANT,
                    text=f"""
            I have evaluated {python_file.name}'s execution results.  Please make the following modifications to the code and name the new version {config.code_basename}_{file_idx + 1}.py
            {json.dumps(eval_json['instructions'], indent=4)}
                    """
                )
            )
        else:
            sys_messages.append(
                LlmMessage(
                    message_type=LlmMessageType.ASSISTANT,
                    file=python_file,
                    text=f"""
            On iteration {idx}, you generated and we executed {python_file.name}.  the contents of {python_file.name} are:
            {common.read_file(python_file)}
                    """
                )
            )

            if log_file.exists():
                log_file_contents = common.read_file(
                    log_file,
                    100
                ).replace("██", "")

                sys_messages.append(
                    LlmMessage(
                        message_type=LlmMessageType.USER,
                        file=log_file,
                        text=f"""
                The output of {python_file.name}'s execution is as follows:
                {log_file_contents}
                        """
                    )
                )

    return sys_messages


def build_instructions_system_messages(config):
    messages = []
    for attachment in config.attachments:
        if isinstance(attachment, dict):
            file = Path(attachment["file"])
            desc = attachment.get("desc")
        else:
            file = Path(attachment)
            desc = ""

        if not file.exists():
            print("attachment", str(file), "not found")
            continue

        messages.append(
            LlmMessage(
                message_type=LlmMessageType.SYSTEM,
                file=file,
                text=f"""
        file {file.name} contains {desc}
        
        {file.read_text()}
        """
            )
        )

    messages.append(
        LlmMessage(
            message_type=LlmMessageType.SYSTEM,
            text=f"""
GOAL:  {config.goal}

{SYSTEM_MESSAGE_INSTRUCTIONS}
            """
        )
    )

    return messages


def get_all_eval_files(config: SelfDriverConfig) -> List[Path]:
    pattern = re.compile(rf"{config.code_basename}_(\d+)\.eval.txt")
    files = list(config.output_directory.glob(f"{config.code_basename}_*.eval.txt"))
    files = [f for f in files if pattern.search(f.name)]

    file_tuples = []
    for f in files:
        file_idx = int(pattern.search(f.name).group(1))
        file_tuples.append((file_idx, Path(f)))

    return [tpl[1] for tpl in sorted(file_tuples, key=lambda tpl: tpl[0])]


def get_latest_eval(config: SelfDriverConfig) -> dict:
    last_eval_file = common.last(
        get_all_eval_files(config)
    )

    return common.read_json(last_eval_file, {})


def get_latest_python_file(config) -> Tuple[Path, int]:
    pattern = re.compile(rf"{config.code_basename}_(\d+)\.py")
    files = list(config.code_directory.glob(f"{config.code_basename}_*.py"))
    numbers = [
        int(pattern.search(file.name).group(1))
        for file in files if pattern.search(file.name)
    ]
    last_index = max(numbers) if numbers else 0

    latest_python_file = config.code_directory / f"{config.code_basename}_{last_index}.py"
    return latest_python_file, last_index


def generate_code(
        config: SelfDriverConfig,
        messages: List[LlmMessage],
        previous_code: Path = None
) -> str:
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

    print(f"\n{json.dumps(planning_data, indent=4)}\n")

    if previous_code:
        common.write_json(
            config.output_directory / f"{previous_code.name[:-3]}.eval.txt",
            planning_data
        )

    if not config.never_done and common.parse_bool(planning_data['goal_achieved']):
        return DONE

    previous_exception = None
    for i in range(3):
        try:
            llm_response_codegen = get_coding_llm_response(
                config,
                planning_data,
                previous_exception=previous_exception
            )
            price += llm_response_codegen.price_total

            code_str = lint_and_format(llm_response_codegen.text)

            print(f"Iteration total cost: ${price:.4f}")
            return code_str
        except CodeCompilationError as e:
            previous_exception = e

    raise previous_exception


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
            record_spend(config, llm_response_reformat.price_total)
            json_text = llm_response_reformat.text

    raise last_e


def get_planning_llm_response(
        config,
        messages: List[LlmMessage]
) -> LlmResponse:
    model = config.model

    files = common.filter_none([m.file for m in messages])
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

    llm_response_planning = llm_interface.chat(
        messages,
        model,
        code_response=True
    )
    record_spend(config, llm_response_planning.price_total)

    print(f"\t\tcost of planning: total ${llm_response_planning.price_total:.4f}; input ${llm_response_planning.price_input:.4f}; output ${llm_response_planning.price_output:.4f}")

    return llm_response_planning


def get_coding_llm_response(
        config,
        planning_data: dict,
        previous_exception: CodeCompilationError = None
) -> LlmResponse:
    model = LlmModel.OPENAI_GPT_O3_MINI

    planning_data_truncated = {
        "evaluation": planning_data.get("evaluation", []),
        "instructions": planning_data.get("instructions", [])
    }

    previous_code = config.code_directory / common.get(planning_data, "file_to_modify", "latest")
    if not previous_code.exists():
        previous_code, _ = get_latest_python_file(config)

    messages = []
    messages.append(
        LlmMessage(
            message_type=LlmMessageType.SYSTEM,
            text=SYSTEM_MESSAGE_CODE
        )
    )

    if previous_exception:
        messages.append(
            LlmMessage(
                message_type=LlmMessageType.USER,
                text=f"""
This is code from the previous attempt to generate code based on the instruction set:
{previous_exception.code_str}

This code as the following error(s): 
{str(previous_exception)}

Please try again, avoiding these errors
        """
            )
        )

    if previous_code and previous_code.exists():
        print("modifying", previous_code.name)
        messages.append(
            LlmMessage(
                message_type=LlmMessageType.ASSISTANT,
                text=f"""
The version of code to modify is named {previous_code.name}.  This file contains

{common.read_file(previous_code)}
                """
            )
        )

        messages.append(
            LlmMessage(
                message_type=LlmMessageType.USER,
                text=f"""
Our GOAL is defined as:
{config.goal}

To make progress against this goal, please modify {previous_code.name}, following each of the instructions exactly and in order:

{json.dumps(planning_data_truncated, indent=4)}

Please include the "evaluation" items and then the "instructions" items in a nicely formatted block comment at the start of the python file.  Use bullet points for each explanation item 
    """
            )
        )

    else:
        messages.append(
            LlmMessage(
                message_type=LlmMessageType.USER,
                text=f"""
Our GOAL is defined as:
{config.goal}

To get started on this GOAL, please write the initial version of the code, following each of the instructions exactly and in order:

{json.dumps(planning_data_truncated, indent=4)}

Please include the "evaluation" items and then the "instructions" items in a nicely formatted block comment at the start of the python file.  Use bullet points for each explanation item 
        """
            )
        )

    token_count = LlmMessage.get_total_token_count(model, messages)
    max_tokens = MODEL_TO_MAX_TOKENS.get(model)

    if max_tokens:
        print(f"about to call out to {model} to generate code.  {token_count:,}/{max_tokens:,} tokens used")
    else:
        print(f"about to call out to {model} to generate code.  {token_count:,} tokens used")

    llm_response_codegen = llm_interface.chat(
        messages,
        model,
        code_response=True
    )
    llm_response_codegen.text = f"""
# planning model: {config.model}"
# coding model: {model}"

{llm_response_codegen.text}

"""
    record_spend(config, llm_response_codegen.price_total)

    print(f"\t\tcost of code gen: total ${llm_response_codegen.price_total:.4f}; input ${llm_response_codegen.price_input:.4f}; output ${llm_response_codegen.price_output:.4f}")

    return llm_response_codegen


def get_current_spend(config):
    total_spend_file = config.code_directory / f"{config.code_basename}.cost"

    if total_spend_file.exists():
        total_spend = float(total_spend_file.read_text().strip().splitlines()[0])
    else:
        total_spend = 0

    return total_spend


def record_spend(config, price: float):
    total_spend = get_current_spend(config)
    total_spend += price

    total_spend_file = config.code_directory / f"{config.code_basename}.cost"
    total_spend_file.write_text(f"{total_spend}")

    return total_spend


def print_final_evaluation(config):
    if not config:
        return

    messages = []

    pattern = re.compile(rf"{config.code_basename}_(\d+)\.eval.txt")
    eval_files = get_all_eval_files(config)
    iteration_idxs = []
    for eval_file in eval_files:
        iteration_idx = int(pattern.search(eval_file.name).group(1))
        iteration_idxs.append(iteration_idx)
        eval_json, _ = ensure_parsable_json(config, eval_file.read_text())
        evaluation = common.get(eval_json, "evaluation")
        if evaluation:
            messages.append(
                LlmMessage(
                    message_type=LlmMessageType.ASSISTANT,
                    text=f"""
            Iteration {iteration_idx} results:
            {json.dumps(evaluation, indent=4)}
                    """
                )
            )

    count_evals = len(messages)
    aval_iterations = ",".join([str(i) for i in iteration_idxs])

    messages.append(LlmMessage(
        message_type=LlmMessageType.ASSISTANT,
        text=f"""
You are expert level at evaluating model training results and choosing the best result

This is the GOAL of the code:
{config.goal}

YOUR TASKS:  
1)  Look through ALL of the attached iteration results and identify the Iteration number that BEST ACHIEVES THE ABOVE GOAL
    -  if an iteration has one or more CRITICAL ISSUES, RuntimeError, or exceptions described in its results, it is excluded from consideration for best_iteration
    -  if there is a metric value associated with the GOAL, use the metric value to identify the best iteration
2)  Respond with a json dict in the following format:
    ========= example response format ===========
    {{
        "best_iteration": an integer value representing the iteration number that you think best achieves the goal.  must be one of these values: {aval_iterations},
        "summary":  a summary of why you chose that iteration for best - if there is a metric you are using to rank the iterations and pick the best, name the metric and the best_iteration's metric value,
        "details":  markdown formatted details a) expanding on why you chose that iteration  and b) an evaluation on how that iteration's model would perform in a production environment,
        "curriculum":  markdown formatted summarizing the significant teaching_lessons from all of the iterations that got us to the best iteration.  This can be a really long response with many subject sections.  The audience for the curriculum response is a junior engineer trying to learn all they can about high-level area (machine-learning for example).  the goal here is to help the junior engineer build out their mental model, so real world examples and leaning into the 'why' is important.  NOTE:  Please do not mention the word 'curriculum' or name the audience in this response
        
    }}
    ========= end example response format ===========

RESPOND ONLY WITH IMMEDIATELY PARSEABLE JSON IN THE EXAMPLE RESPONSE FORMAT
    """
    ))

    print(f"reviewing the {count_evals} iterations and generating a final evaluation...")

    llm_response = llm_interface.chat(
        messages,
        LlmModel.GEMINI_2_5_PRO,
        code_response=True
    )
    record_spend(config, llm_response.price_total)

    print(f"got llm response len: {len(llm_response.text)}")
    eval_data, _ = ensure_parsable_json(config, llm_response.text)

    best_iteration = common.get(eval_data, 'best_iteration')
    if best_iteration is None:
        raise Exception(f"unable to determine the best iteration from \n{json.dumps(eval_data)}")

    checkpoints_dir = common.first([
        f for f in
        config.code_directory.glob(f"{config.code_basename}_{best_iteration}_*checkpoints*")
        if not f.is_file()
    ])
    checkpoints_dir = Path(checkpoints_dir)

    if checkpoints_dir:
        checkpoint_files = [f for f in Path(checkpoints_dir).iterdir() if f.is_file()]
        s3_dir = f"{config.code_basename}/{config.code_basename}_{best_iteration}"
    else:
        checkpoint_files = []
        s3_dir = None

    best_iteration_plan = config.output_directory / f"{config.code_basename}_{best_iteration}.eval.txt"
    best_python_file = config.code_directory / f"{config.code_basename}_{best_iteration}.py"

    arch_llm_response = llm_interface.chat(
        f"""
Please respond only with markdown formatted details fully explaining {best_python_file.name}'s model architecture and training strategy:

======== start {best_python_file.name}'s code =============
{best_python_file.read_text()}
======== end {best_python_file.name}'s code =============


Policies that must be followed:
    - give as much detail as needed.  
    - the audience for this is a mid-level ML research scientist.  If there are things you'd like to teach to a mid-level ML research scientist, please add this content as well
    - DO NOT include the string "```markdown" anywhere in the response
""",
        LlmModel.GEMINI_2_5_PRO
    )
    record_spend(config, arch_llm_response.price_total)

    archive_path = config.code_directory / f"{config.code_basename}_{best_iteration}.zip"

    planning_model = None
    try:
        best_iteration_plan, _ = ensure_parsable_json(config, best_iteration_plan.read_text())
        planning_model = best_iteration_plan['planning_model']
    except Exception as e:
        logging.exception(e)

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

    txt_output = f"""
# Best iteration
Iteration #{best_iteration} {planning_model}
Code:  {best_python_file.name}
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

    eval_text_path = config.code_directory / f"{config.code_basename}.full-eval.md"
    eval_text_path.write_text(txt_output)

    with zipfile.ZipFile(archive_path, 'w') as zipf:
        zipf.write(
            best_python_file,
            arcname=os.path.relpath(best_python_file, config.code_directory)
        )

        zipf.write(
            eval_text_path,
            arcname=os.path.relpath(eval_text_path, config.code_directory)
        )

    for file in checkpoint_files:
        get_aws_interface().upload_file(
            file,
            settings.BUCKETS[S3Bucket.MODELS],
            f"{s3_dir}/{file.name}"
        )

    print(txt_output)

    print(f"""grab artifacts from
scpc "{os.path.abspath(archive_path)}"
    """)

    if s3_dir:
        print(f"model checkpoints uploaded to s3://{settings.BUCKETS[S3Bucket.MODELS]}/{s3_dir}")
