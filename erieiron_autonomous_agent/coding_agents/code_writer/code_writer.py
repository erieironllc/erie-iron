from pathlib import Path
from typing import Dict, List, Tuple, Union

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
from .claude_coder import ClaudeCoder, ClaudeApiException, QuotaExceededException
from .codex_coder import CodexCoder
from .gemini_coder import GeminiCoder, GeminiApiException, GeminiQuotaExceededException
from .individual_file_coder import IndividualFileCoder


def write_code(config: CodingAgentConfig, planning_data: dict) -> Tuple[List[Path], Dict]:
    """
    Main entry point for code generation.
    
    Tries coders in order: Claude, Codex, Gemini, Individual File Coder.
    If one fails, moves to the next.
    
    Args:
        config: Coding agent configuration
        planning_data: Planning data dictionary
        
    Returns:
        Tuple of (changed_paths, execution_metadata)
        
    Raises:
        ExecutionException: If all coders fail
        BadPlan: If the plan is invalid
        Other exceptions: As raised by the underlying coders
    """
    
    # Define coders in order of preference
    coders = [
        ("Claude", ClaudeCoder()),
        ("Codex", CodexCoder()),
        ("Gemini", GeminiCoder()),
        ("Individual", IndividualFileCoder())
    ]
    
    errors = []
    
    for coder_name, coder in coders:
        try:
            config.log(f"Attempting code generation with {coder_name} coder")
            changed_paths, metadata = coder.execute_coding(config, planning_data)
            config.log(f"{coder_name} coder execution completed successfully")
            
            # Add coder info to metadata
            metadata["successful_coder"] = coder_name.lower()
            metadata["attempts_made"] = len(errors) + 1
            metadata["failed_coders"] = [e["coder"] for e in errors]
            
            return changed_paths, metadata
            
        except Exception as e:
            error_info = {
                "coder": coder_name.lower(),
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
            errors.append(error_info)
            
            config.log(f"{coder_name} coder failed: {e}")
            
            # If this is not the last coder, continue to next
            if coder != coders[-1][1]:
                config.log(f"Falling back to next coder...")
                continue
            else:
                # This was the last coder, all failed
                break
    
    # All coders failed
    error_summary = "\n".join([
        f"- {err['coder']}: {err['error_type']}: {err['error_message']}"
        for err in errors
    ])
    
    raise ExecutionException(
        f"All coding approaches failed:\n{error_summary}"
    )