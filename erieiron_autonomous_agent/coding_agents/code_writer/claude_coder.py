import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Dict, List

from erieiron_common.enums import LlmModel
from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
from .base_coder import BaseCoder


class ClaudeApiException(Exception):
    """Exception for Claude API specific errors."""
    pass


class QuotaExceededException(ClaudeApiException):
    """Exception for quota/rate limit errors."""
    pass


class ClaudeCoder(BaseCoder):
    """Claude Code CLI implementation for code generation."""
    
    @property
    def coder_name(self) -> str:
        return "claude"
    
    @property
    def default_llm_model(self):
        return LlmModel.CLAUDE_SONNET_3_5
    
    def build_command(self, config: CodingAgentConfig, prompt_path: Path, artifact_paths: Dict[str, Path]) -> List[str]:
        """Build the Claude Code CLI command."""
        return [
            "claude-code",
            "--headless",
            "--auto-approve",
            "--working-directory",
            str(config.sandbox_root_dir),
            "--output-format",
            "json",
            "--session-file",
            str(artifact_paths["session"]),
            str(prompt_path)
        ]
    
    def execute_command(self, command: List[str], config: CodingAgentConfig, prompt_text: str) -> subprocess.CompletedProcess:
        """Execute the Claude Code CLI command."""
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=str(config.sandbox_root_dir),
            env=os.environ.copy()
        )
    
    def check_for_api_errors(self, result: subprocess.CompletedProcess) -> None:
        """Check for Claude-specific API errors."""
        if result.returncode != 0:
            stderr_content = result.stderr or ""
            stdout_content = result.stdout or ""
            
            # Check for quota/rate limiting errors
            quota_indicators = [
                "quota", "rate limit", "too many requests", 
                "exceeded", "429", "usage limit"
            ]
            if any(indicator.lower() in stderr_content.lower() or 
                  indicator.lower() in stdout_content.lower() 
                  for indicator in quota_indicators):
                raise QuotaExceededException(
                    f"Claude Code CLI hit quota/rate limit: {stderr_content}"
                )
            
            # Check for other API errors
            api_indicators = ["api error", "authentication", "unauthorized", "forbidden"]
            if any(indicator.lower() in stderr_content.lower() or 
                  indicator.lower() in stdout_content.lower() 
                  for indicator in api_indicators):
                raise ClaudeApiException(
                    f"Claude Code CLI API error: {stderr_content}"
                )
            
            raise ExecutionException(
                f"Claude Code CLI exited with code {result.returncode}. Check stdout and stderr for details."
            )
    
    def _get_coder_intro(self, config: CodingAgentConfig, plan_path: Path) -> str:
        """Get Claude-specific introduction text."""
        return textwrap.dedent(f"""
        You are assisting Erie Iron's self-driving coding workflow using Claude Code.
        Work within the repository at {config.sandbox_root_dir}.
        Follow the approved development plan summarized below and saved at {plan_path}.
        Consult the relevant engineering standards from the reference prompts.
        Do not commit or push changes; the orchestrator handles git commits.
        """)
    
    def _get_final_prompt_sections(self, planning_data: dict, plan_path: Path) -> List[str]:
        """Get Claude-specific final prompt sections."""
        return [
            textwrap.dedent(f"""

            ## Development Plan
            {json.dumps(planning_data, indent=2)}

            ## Execution Checklist
            1. Read and understand the full development plan above
            2. Apply all Erie Iron engineering standards from the reference prompts
            3. Implement code changes that satisfy the plan and address prior failures
            4. Scope modifications to planned files unless dependencies require changes
            5. Never modify read-only paths
            6. Leave repository with changes ready for review; do not commit
            """)
        ]
    
    def extract_usage_stats(self, stdout: str, stderr: str, metadata: dict) -> Dict:
        """Extract token/cost metrics from Claude Code CLI output."""
        session_path = metadata.get("session")
        config = metadata.get("config")
        
        metrics: dict[str, float | int] = {}
        
        # Try to extract from session JSON file first
        if session_path and session_path.exists():
            try:
                session_data = json.loads(session_path.read_text(encoding="utf-8"))
                # Look for usage data in session JSON
                if isinstance(session_data, dict):
                    usage = session_data.get("usage") or session_data.get("metadata", {}).get("usage")
                    if usage:
                        metrics.update(self._parse_claude_usage(usage))
            except Exception as e:
                if config:
                    config.log(f"Failed to parse Claude session file: {e}")
        
        # Extract from stdout/stderr as fallback
        if not metrics.get("total_tokens"):
            sources = [s for s in [stdout, stderr] if s]
            for text in sources:
                parsed_metrics = self._extract_claude_usage_from_text(text)
                if parsed_metrics.get("total_tokens"):
                    metrics.update(parsed_metrics)
                    break
        
        # Estimate cost if we have tokens but no cost
        if metrics.get("total_tokens") and not metrics.get("total_cost_usd") and config:
            metrics["total_cost_usd"] = self._estimate_claude_cost(metrics, config)
        
        return metrics

    def _parse_claude_usage(self, usage: dict) -> dict:
        """Parse usage data from Claude session JSON."""
        metrics = {}
        
        # Map common Claude usage fields
        if "input_tokens" in usage:
            metrics["prompt_tokens"] = usage["input_tokens"]
        if "output_tokens" in usage:
            metrics["completion_tokens"] = usage["output_tokens"]
        if "total_tokens" in usage:
            metrics["total_tokens"] = usage["total_tokens"]
        elif metrics.get("prompt_tokens") and metrics.get("completion_tokens"):
            metrics["total_tokens"] = metrics["prompt_tokens"] + metrics["completion_tokens"]
        
        # Handle other Claude-specific fields
        if "cached_input_tokens" in usage:
            metrics["cached_input_tokens"] = usage["cached_input_tokens"]
        if "reasoning_output_tokens" in usage:
            metrics["reasoning_output_tokens"] = usage["reasoning_output_tokens"]
        
        # Cost information
        if "cost" in usage:
            metrics["total_cost_usd"] = usage["cost"]
        elif "total_cost" in usage:
            metrics["total_cost_usd"] = usage["total_cost"]
        
        return metrics

    def _extract_claude_usage_from_text(self, text: str) -> dict:
        """Extract usage information from Claude text output."""
        import re
        
        metrics = {}
        if not text:
            return metrics
        
        # Look for JSON blocks in output
        json_blocks = []
        lines = text.split("\n")
        current_block = []
        in_json = False
        
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("{"):
                in_json = True
                current_block = [line]
            elif in_json:
                current_block.append(line)
                if stripped.endswith("}"):
                    json_blocks.append("\n".join(current_block))
                    current_block = []
                    in_json = False
        
        # Parse JSON blocks for usage data
        for block in json_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    usage = data.get("usage") or data.get("metadata", {}).get("usage")
                    if usage:
                        parsed = self._parse_claude_usage(usage)
                        if parsed.get("total_tokens"):
                            metrics.update(parsed)
                            break
            except Exception:
                continue
        
        # Regex fallback for token counts
        if not metrics.get("total_tokens"):
            patterns = [
                (r"input[_\s]+tokens?[:\s]+(\d+)", "prompt_tokens"),
                (r"output[_\s]+tokens?[:\s]+(\d+)", "completion_tokens"),
                (r"total[_\s]+tokens?[:\s]+(\d+)", "total_tokens"),
                (r"cost[:\s]+\$?([0-9]+\.?[0-9]*)", "total_cost_usd"),
            ]
            
            for pattern, key in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    try:
                        value = float(match.group(1)) if key == "total_cost_usd" else int(match.group(1))
                        metrics[key] = value
                    except ValueError:
                        continue
            
            # Calculate total if we have components
            if metrics.get("prompt_tokens") and metrics.get("completion_tokens"):
                metrics.setdefault("total_tokens", metrics["prompt_tokens"] + metrics["completion_tokens"])
        
        return metrics

    def _estimate_claude_cost(self, metrics: dict, config: CodingAgentConfig) -> float | None:
        """Estimate cost from token usage for Claude."""
        total_tokens = metrics.get("total_tokens")
        if not total_tokens:
            return None
        
        # Use Claude Sonnet 3.5 pricing as default
        from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
        pricing = MODEL_PRICE_USD_PER_MILLION_TOKENS.get(LlmModel.CLAUDE_SONNET_3_5)
        if not pricing:
            return None
        
        input_price_per_token = pricing.get("input_price_per_token", 0)
        output_price_per_token = pricing.get("output_price_per_token", 0)
        
        prompt_tokens = metrics.get("prompt_tokens", 0)
        completion_tokens = metrics.get("completion_tokens", 0)
        
        if prompt_tokens and completion_tokens:
            estimated_cost = (prompt_tokens * input_price_per_token) + (completion_tokens * output_price_per_token)
        else:
            # Use input pricing as fallback for total tokens
            estimated_cost = total_tokens * input_price_per_token
        
        return estimated_cost
