from pathlib import Path
from typing import Dict, List, Tuple

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from .claude_coder import ClaudeCoder, ClaudeApiException, QuotaExceededException
from .gemini_coder import GeminiCoder, GeminiApiException, GeminiQuotaExceededException
from .codex_coder import CodexCoder


def write_code(config: CodingAgentConfig, planning_data: dict) -> Tuple[List[Path], Dict]:
    """
    Main entry point for code generation.
    
    Attempts Claude Code CLI first, then Gemini CLI, finally falls back to Codex CLI.
    
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
    
    # Try Claude first
    try:
        config.log("Attempting code generation with Claude Code CLI")
        claude_coder = ClaudeCoder()
        changed_paths, metadata = claude_coder.execute_coding(config, planning_data)
        config.log("Claude Code CLI execution completed successfully")
        return changed_paths, metadata
        
    except (QuotaExceededException, ClaudeApiException) as e:
        config.log(f"Claude Code CLI failed with API/quota error: {e}")
        config.log("Falling back to Gemini CLI")
        
        # Fallback to Gemini
        try:
            gemini_coder = GeminiCoder()
            changed_paths, metadata = gemini_coder.execute_coding(config, planning_data)
            config.log("Gemini CLI fallback execution completed successfully")
            return changed_paths, metadata
            
        except (GeminiQuotaExceededException, GeminiApiException) as gemini_error:
            config.log(f"Gemini CLI also failed with API/quota error: {gemini_error}")
            config.log("Falling back to Codex CLI")
            
            # Final fallback to Codex
            try:
                codex_coder = CodexCoder()
                changed_paths, metadata = codex_coder.execute_coding(config, planning_data)
                config.log("Codex CLI final fallback execution completed successfully")
                return changed_paths, metadata
                
            except Exception as codex_error:
                config.log(f"Codex CLI final fallback also failed: {codex_error}")
                # Re-raise the original Claude error for better context
                raise e from codex_error
                
        except Exception as gemini_error:
            config.log(f"Gemini CLI failed with unexpected error: {gemini_error}")
            config.log("Falling back to Codex CLI")
            
            # Final fallback to Codex
            try:
                codex_coder = CodexCoder()
                changed_paths, metadata = codex_coder.execute_coding(config, planning_data)
                config.log("Codex CLI final fallback execution completed successfully")
                return changed_paths, metadata
                
            except Exception as codex_error:
                config.log(f"Codex CLI final fallback also failed: {codex_error}")
                # Re-raise the original Gemini error for better context
                raise gemini_error from codex_error
            
    except Exception as claude_error:
        config.log(f"Claude Code CLI failed with unexpected error: {claude_error}")
        config.log("Falling back to Gemini CLI")
        
        # Fallback to Gemini for other Claude errors
        try:
            gemini_coder = GeminiCoder()
            changed_paths, metadata = gemini_coder.execute_coding(config, planning_data)
            config.log("Gemini CLI fallback execution completed successfully")
            return changed_paths, metadata
            
        except Exception as gemini_error:
            config.log(f"Gemini CLI fallback also failed: {gemini_error}")
            config.log("Falling back to Codex CLI")
            
            # Final fallback to Codex
            try:
                codex_coder = CodexCoder()
                changed_paths, metadata = codex_coder.execute_coding(config, planning_data)
                config.log("Codex CLI final fallback execution completed successfully")
                return changed_paths, metadata
                
            except Exception as codex_error:
                config.log(f"Codex CLI final fallback also failed: {codex_error}")
                # Re-raise the original Claude error for better context
                raise claude_error from codex_error