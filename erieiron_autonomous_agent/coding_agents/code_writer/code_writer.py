import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

import yaml
from django.db import transaction

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException, BadPlan, AgentBlocked
from erieiron_common import common
from erieiron_common.enums import SdaPhase
from .claude_coder import ClaudeCoder
from .codex_coder import CodexCoder
from ...models import SelfDrivingTask

CODERS = [
    ("Claude", ClaudeCoder),
    ("Codex", CodexCoder),
    # ("Gemini", GeminiCoder),
]


def write_code(config: CodingAgentConfig) -> Tuple[List[Path], Dict]:
    config.set_phase(SdaPhase.CODING)
    """
    Main entry point for code generation.
    
    Tries coders in order: Claude, Codex, Gemini, Individual File Coder.
    If one fails, moves to the next.
    
    Args:
        config: Coding agent configuration
        
    Returns:
        Tuple of (changed_paths, execution_metadata)
        
    Raises:
        ExecutionException: If all coders fail
        BadPlan: If the plan is invalid
        Other exceptions: As raised by the underlying coders
    """
    
    # Define coders in order of preference
    tdd_test_file = common.get(config.current_iteration, ["planning_json", "tdd_test_file"])
    
    if config.is_stagnating:
        coders = reversed(CODERS)
    else:
        coders = CODERS
    
    errors = []
    for coder_name, coder_cls in coders:
        coder = coder_cls(config)
        try:
            config.log(f"Attempting code generation with {coder_name} coder")
            
            changed_paths, metadata = coder.execute_coding()
            
            # Add coder info to metadata
            config.log(f"{coder_name} coder execution completed successfully")
            metadata["successful_coder"] = coder_name.lower()
            metadata["attempts_made"] = len(errors) + 1
            metadata["failed_coders"] = [e["coder"] for e in errors]
            
            # if we are writing a test (ie the plan defines tdd_test_file), then make sure the test was written
            if tdd_test_file:
                if not (config.sandbox_root_dir / tdd_test_file).exists():
                    raise BadPlan(f"Failed to write a test file named `{tdd_test_file}`")
                
                with transaction.atomic():
                    SelfDrivingTask.objects.filter(id=config.self_driving_task.id).update(
                        test_file_path=tdd_test_file
                    )
                    config.self_driving_task.refresh_from_db(fields=["test_file_path"])
            
            return changed_paths, metadata
        
        except AgentBlocked as e:
            raise e
        except BadPlan as e:
            raise e
        except Exception as e:
            logging.exception(e)
            error_info = {
                "coder": coder_name.lower(),
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
            errors.append(error_info)
            
            config.log(f"{coder_name} coder failed: {e}")
            config.log(f"Falling back to next coder...")
    
    # All coders failed
    error_summary = "\n".join([
        f"- {err['coder']}: {err['error_type']}: {err['error_message']}"
        for err in errors
    ])
    
    raise ExecutionException(
        f"All coding approaches failed:\n{error_summary}"
    )


