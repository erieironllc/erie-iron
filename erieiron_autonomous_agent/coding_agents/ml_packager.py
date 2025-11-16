import json
import os
import tempfile
import zipfile
from pathlib import Path

import settings
from erieiron_autonomous_agent.coding_agents.coding_agent import get_goal_msg
from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.models import SelfDrivingTaskIteration, CodeFile, CodeVersion
from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat
from erieiron_common import common, aws_utils
from erieiron_common.enums import TaskType, S3Bucket
from erieiron_common.llm_apis.llm_interface import LlmMessage


def package_ml_artifacts(config: CodingAgentConfig):
    if not config:
        return
    
    import time
    if time.time() > 0:
        raise Exception("""
    NOTE FOR FUTURE SELF - THIS CODE WILL TAKE SOME ITERATION AND DEBUGGING TO GET  TO WORK
    """)
    
    messages = []
    
    messages.append(get_goal_msg(config, "Goal"))
    
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
    
    if TaskType.CODING_ML.eq(config.task_type):
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
    
    eval_data = llm_chat(
        "Package Artifacts",
        messages,
        config.current_iteration,
        config.model_code_planning,
        code_response=True
    ).json()
    
    best_iteration = SelfDrivingTaskIteration.objects.get(id=common.get(eval_data, 'best_iteration_id'))
    best_version_num = best_iteration.version_number
    
    code_file_name = config.self_driving_task.main_name
    code_file = CodeFile.get(config.business, code_file_name)
    main_file_name = code_file.get_base_name()
    
    code_versions: list[CodeVersion] = \
        list(best_iteration.codeversion_set.filter(code_file=code_file)) \
        + list(best_iteration.codeversion_set.exclude(code_file=code_file))
    
    best_iteration_code = []
    for cv in code_versions:
        best_iteration_code.append(f"""
======== start {cv.code_file.file_path}'s code =============
{cv.code}
======== end {cv.code_file.file_path}'s code =============
        """
                                   )
    best_iteration_code = "\n".join(best_iteration_code)
    
    messages = LlmMessage.user(f"""
    Please review the following code and write markdown formatted details fully explaining {main_file_name}'s model architecture and training strategy:
{best_iteration_code}

    Policies that must be followed:
        - give as much detail as needed.  
        - the audience for this is a mid-level ML research scientist.  If there are things you'd like to teach to a mid-level ML research scientist, please add this content as well
        - DO NOT include the string "```markdown" anywhere in the response
    """)
    
    arch_llm_response = llm_chat(
        "Writeup Summary",
        messages,
        config.current_iteration,
        config.model_code_planning
    )
    
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
    
    checkpoints_dir = config.artifacts_dir
    
    if checkpoints_dir:
        checkpoint_files = [f for f in Path(checkpoints_dir).iterdir() if f.is_file()]
        s3_dir = f"{main_file_name}/{main_file_name}_v{best_version_num}"
    else:
        checkpoint_files = []
        s3_dir = None
    
    for file in checkpoint_files:
        aws_utils.get_aws_interface().upload_file(
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
    
    archive_path = Path(settings.BASE_DIR) / f"task_{config.task.id}_v{best_version_num}.zip"
    with tempfile.TemporaryDirectory() as tmpdirname:
        with zipfile.ZipFile(archive_path, 'w') as zipf:
            tmpdirname = Path(tmpdirname)
            print(f"created temporary directory at {tmpdirname}")
            
            eval_text_path = tmpdirname / f"{config.task.id}.full-eval.md"
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
