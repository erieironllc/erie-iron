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
    
    tdd_test_file = common.get(config.current_iteration, ["planning_json", "tdd_test_file"])
    
    if False and config.is_stagnating:
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
            config.log(f"{coder_name} coder execution completed successfully.  modified files:")
            for p in changed_paths:
                config.log(f"\t- {p}")
            
            metadata["successful_coder"] = coder_name.lower()
            metadata["attempts_made"] = len(errors) + 1
            metadata["failed_coders"] = [e["coder"] for e in errors]
            
            # if we are writing a test (ie the plan defines tdd_test_file), then make sure the test was written
            if tdd_test_file:
                test_file_path = config.sandbox_root_dir / tdd_test_file
                if not test_file_path.exists():
                    raise BadPlan(f"Failed to write a test file named `{tdd_test_file}`")

                # Validate test type matches the phase
                is_python_test = tdd_test_file.endswith('.py')

                # Check if it's a valid Jest test file
                # Valid Jest tests are either:
                # 1. Files with .test.js/jsx/ts/tsx extension (anywhere)
                # 2. Files in __tests__/ directory with .js/jsx/ts/tsx extension
                has_test_extension = any(tdd_test_file.endswith(ext) for ext in ['.test.js', '.test.jsx', '.test.ts', '.test.tsx'])
                in_tests_dir = '__tests__' in tdd_test_file
                has_js_extension = any(tdd_test_file.endswith(ext) for ext in ['.js', '.jsx', '.ts', '.tsx'])
                is_js_test = has_test_extension or (in_tests_dir and has_js_extension)

                if config.is_ui_first_phase:
                    # UI-first phase requires React/Jest tests, not Django tests
                    if is_python_test and not is_js_test:
                        raise BadPlan(
                            f"UI-first phase requires React/Jest tests, but test file `{tdd_test_file}` is a Python test. "
                            f"Test file must be either:\n"
                            f"  - In a __tests__/ directory with .js/.jsx/.ts/.tsx extension, or\n"
                            f"  - Have a .test.js/.test.jsx/.test.ts/.test.tsx extension"
                        )
                    if not is_js_test:
                        raise BadPlan(
                            f"UI-first phase requires React/Jest tests with proper naming convention. "
                            f"Test file `{tdd_test_file}` must be either:\n"
                            f"  - In a __tests__/ directory with .js/.jsx/.ts/.tsx extension, or\n"
                            f"  - Have a .test.js/.test.jsx/.test.ts/.test.tsx extension"
                        )

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


