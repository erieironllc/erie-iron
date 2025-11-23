import json
import os
import re
import subprocess
import textwrap
import time
import types
from pathlib import Path
from typing import Dict, List

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
from erieiron_common.enums import LlmModel
from .base_coder import BaseCoder


class GeminiApiException(Exception):
    """Exception for Gemini API specific errors."""
    pass


class GeminiQuotaExceededException(GeminiApiException):
    """Exception for quota/rate limit errors."""
    pass


class GeminiCoder(BaseCoder):
    """Gemini CLI implementation for code generation."""
    
    @property
    def coder_name(self) -> str:
        return "gemini"
    
    @property
    def default_llm_model(self):
        return LlmModel.GEMINI_3_0_PRO
    
    def build_command(self, prompt_path: Path, artifact_paths: Dict[str, Path]) -> List[str]:
        """Build the Gemini CLI command."""
        return [
            "gemini",
            "--yolo",
            "--prompt", f"$(cat {prompt_path})",
            "--output-format", "text",
            "--include-directories", "."
        ]
    
    def execute_command(self, command: List[str], prompt_text: str) -> subprocess.CompletedProcess:
        """Execute the Gemini CLI command with real-time streaming."""
        start_time = time.time()
        
        gemini_cmd_str = " ".join(command)
        process = subprocess.Popen(
            gemini_cmd_str,
            shell=True,  # Required for $(cat ...) substitution
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(self.config.sandbox_root_dir),
            env=os.environ.copy(),
            bufsize=1
        )

        # Get stdout path for incremental writing
        iteration_id = self.config.current_iteration.id
        stdout_path = self.config.artifacts_dir / f"{iteration_id}_{self.coder_name}_stdout.log"
        
        streamed_stdout = []
        for line in process.stdout:
            clean = line.rstrip()
            streamed_stdout.append(clean)
            self.config.log(f"[gemini-stream] {clean}")
            
            # Append streamed output to stdout_path incrementally
            with stdout_path.open("a", encoding="utf-8") as fp:
                fp.write(clean + "\n")

        return_code = process.wait()
        
        # Build a structure mimicking CompletedProcess output so downstream code still works
        result = types.SimpleNamespace(
            stdout="\n".join(streamed_stdout),
            stderr="",
            returncode=return_code,
            args=command
        )
        
        return result
    
    def check_for_api_errors(self, result: subprocess.CompletedProcess) -> None:
        """Check for Gemini-specific API errors."""
        if result.returncode != 0:
            stderr_content = result.stderr or ""
            stdout_content = result.stdout or ""
            
            # Check for quota/rate limiting errors
            quota_indicators = [
                "quota", "rate limit", "too many requests",
                "exceeded", "429", "usage limit", "resource exhausted"
            ]
            if any(
                    indicator.lower() in stderr_content.lower()
                    or indicator.lower() in stdout_content.lower()
                    for indicator in quota_indicators
            ):
                raise GeminiQuotaExceededException(
                    f"Gemini CLI hit quota/rate limit: {stderr_content}"
                )
            
            # Check for other API errors
            api_indicators = ["api error", "authentication", "unauthorized", "forbidden", "invalid api key"]
            if any(indicator.lower() in stderr_content.lower() or
                   indicator.lower() in stdout_content.lower()
                   for indicator in api_indicators):
                raise GeminiApiException(
                    f"Gemini CLI API error: {stderr_content}"
                )
            
            raise ExecutionException(
                f"Gemini CLI exited with code {result.returncode}. Check stdout and stderr for details."
            )
   
    
    def extract_usage_stats(self, stdout: str, stderr: str, metadata: dict) -> Dict:
        """Extract token/cost metrics from Gemini CLI output."""
        config = metadata.get("config")
        
        metrics: dict[str, float | int] = {}
        
        # Extract from stdout/stderr
        sources = [s for s in [stdout, stderr] if s]
        for text in sources:
            parsed_metrics = self._extract_gemini_usage_from_text(text)
            if parsed_metrics.get("total_tokens"):
                metrics.update(parsed_metrics)
                break
        
        # Estimate cost if we have tokens but no cost
        if metrics.get("total_tokens") and not metrics.get("total_cost_usd") and config:
            metrics["total_cost_usd"] = self._estimate_gemini_cost(metrics, config)
        
        return metrics
    
    def _extract_gemini_usage_from_text(self, text: str) -> dict:
        """Extract usage information from Gemini text output."""
        metrics = {}
        if not text:
            return metrics
        
        # Look for common token reporting patterns in Gemini output
        patterns = [
            (r"input[_\s]+tokens?[:\s]+(\d+)", "prompt_tokens"),
            (r"output[_\s]+tokens?[:\s]+(\d+)", "completion_tokens"),
            (r"total[_\s]+tokens?[:\s]+(\d+)", "total_tokens"),
            (r"(?:cost|price)[:\s]+\$?([0-9]+\.?[0-9]*)", "total_cost_usd"),
            (r"tokens[_\s]+used[:\s]+(\d+)", "total_tokens"),
            (r"prompt[_\s]+(?:token)?[_\s]*count[:\s]+(\d+)", "prompt_tokens"),
            (r"response[_\s]+(?:token)?[_\s]*count[:\s]+(\d+)", "completion_tokens"),
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
    
    def _estimate_gemini_cost(self, metrics: dict, config: CodingAgentConfig) -> float | None:
        """Estimate cost from token usage for Gemini."""
        total_tokens = metrics.get("total_tokens")
        if not total_tokens:
            return None
        
        # Use Gemini 1.5 Pro pricing as default
        from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
        pricing = MODEL_PRICE_USD_PER_MILLION_TOKENS.get(LlmModel.GEMINI_3_0_PRO)
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
