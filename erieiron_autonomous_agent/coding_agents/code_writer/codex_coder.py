import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Dict, List

from erieiron_common.enums import LlmModel
from erieiron_common.llm_apis.llm_constants import MODEL_PRICE_USD_PER_MILLION_TOKENS
from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
from .base_coder import BaseCoder


class CodexCoder(BaseCoder):
    """Codex CLI implementation for code generation."""
    
    @property
    def coder_name(self) -> str:
        return "codex"
    
    @property
    def default_llm_model(self):
        return LlmModel.OPENAI_GPT_5_1
    
    def build_command(self, config: CodingAgentConfig, prompt_path: Path, artifact_paths: Dict[str, Path]) -> List[str]:
        """Build the Codex CLI command."""
        return [
            "codex",
            "exec",
            "--full-auto",
            "--json",
            "--cd",
            str(config.sandbox_root_dir),
            "--output-last-message",
            str(artifact_paths["last_message"]),
            "-"
        ]
    
    def execute_command(self, command: List[str], config: CodingAgentConfig, prompt_text: str) -> subprocess.CompletedProcess:
        """Execute the Codex CLI command."""
        return subprocess.run(
            command,
            input=prompt_text,
            text=True,
            capture_output=True,
            cwd=str(config.sandbox_root_dir),
            env=os.environ.copy()
        )
    
    def check_for_api_errors(self, result: subprocess.CompletedProcess) -> None:
        """Check for Codex-specific API errors."""
        if result.returncode != 0:
            raise ExecutionException(
                f"Codex CLI exited with code {result.returncode}. Check stdout and stderr for details."
            )
    
    def _get_coder_intro(self, config: CodingAgentConfig, plan_path: Path) -> str:
        """Get Codex-specific introduction text."""
        return textwrap.dedent(f"""
        You are the Codex CLI agent assisting Erie Iron's self-driving coding workflow.
        Operate strictly within the sandboxed repository at {config.sandbox_root_dir}.
        Follow the approved development plan saved at {plan_path} and summarised below.
        Before editing a file, consult the relevant engineering standards from the prompts
        directory (see the Reference Prompts section).
        Do not commit or push changes; the orchestrator handles git commits.
        """)
    
    def _get_final_prompt_sections(self, planning_data: dict, plan_path: Path) -> List[str]:
        """Get Codex-specific final prompt sections."""
        return [
            textwrap.dedent(f"""

            ## Execution Checklist
            1. Read the full development plan at {plan_path}.
            2. Adhere to all Erie Iron prompts listed above; load additional file-specific prompts (e.g. YAML, Python, SQL) as needed.
            3. Implement code changes that satisfy the plan and address prior failures. Keep modifications scoped to the planned files unless you uncover a necessary dependency.
            4. No read-only files modified
            5. Leave the repository with changes ready for review; do not commit.
            """)
        ]
    
    def extract_usage_stats(self, stdout: str, stderr: str, metadata: dict) -> Dict:
        """Extract token/cost metrics from Codex execution output."""
        last_message_path = metadata.get("last_message")
        config = metadata.get("config")
        
        metrics: dict[str, float | int] = {}
        sources: list[str] = []
        
        if last_message_path and last_message_path.exists():
            try:
                sources.append(last_message_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        
        if stdout:
            sources.append(stdout)
        if stderr:
            sources.append(stderr)
        
        token_records: list[dict] = []
        for text in sources:
            parsed_metrics, token_info = self._extract_codex_usage_from_text(text)
            metrics.update({k: v for k, v in parsed_metrics.items() if v is not None})
            if token_info:
                token_records.append(token_info)
            if metrics.get("total_tokens") and metrics.get("total_cost_usd") is not None:
                break
        
        if not metrics.get("total_tokens"):
            last_token_record = next((record for record in reversed(token_records) if record.get("total_tokens") is not None), None)
            if last_token_record:
                metrics["total_tokens"] = last_token_record["total_tokens"]
                if last_token_record.get("input_tokens") is not None:
                    metrics.setdefault("prompt_tokens", last_token_record.get("input_tokens"))
                if last_token_record.get("output_tokens") is not None:
                    metrics.setdefault("completion_tokens", last_token_record.get("output_tokens"))
                if last_token_record.get("cached_input_tokens") is not None:
                    metrics.setdefault("cached_input_tokens", last_token_record.get("cached_input_tokens"))
                if last_token_record.get("reasoning_output_tokens") is not None:
                    metrics.setdefault("reasoning_output_tokens", last_token_record.get("reasoning_output_tokens"))
        
        if metrics.get("total_tokens") and metrics.get("total_cost_usd") is None and config:
            metrics["total_cost_usd"] = self._estimate_codex_cost(metrics, config)
        
        return metrics

    def _extract_codex_usage_from_text(self, text: str) -> tuple[dict, dict]:
        """Extract usage metrics from text output."""
        metrics: dict[str, float | int | None] = {}
        token_info: dict[str, int | None] = {}
        if not text:
            return metrics, token_info
        
        json_metrics = self._extract_codex_usage_from_json(text)
        if json_metrics:
            metrics.update(json_metrics)
            token_info = json_metrics.pop("_token_info", token_info)
            if metrics.get("total_tokens") and metrics.get("total_cost_usd") is not None:
                return metrics, token_info
        
        regex_metrics = self._extract_codex_usage_with_regex(text)
        metrics.update({k: v for k, v in regex_metrics.items() if v is not None})
        return metrics, token_info

    def _extract_codex_usage_from_json(self, text: str) -> dict:
        """Extract usage from JSON output."""
        def try_parse_json(candidate: str):
            candidate = candidate.strip()
            if not candidate:
                return None
            try:
                return json.loads(candidate)
            except Exception:
                return None
        
        def find_usage(node):
            if isinstance(node, dict):
                if "usage" in node:
                    return node["usage"]
                for value in node.values():
                    result = find_usage(value)
                    if result:
                        return result
            elif isinstance(node, list):
                for item in node:
                    result = find_usage(item)
                    if result:
                        return result
            return None
        
        metrics: dict[str, float | int | None] = {}
        
        candidates = []
        lines = text.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                candidates.append(stripped)
            elif stripped.startswith("{"):
                json_block = [stripped]
                for j in range(i+1, len(lines)):
                    json_block.append(lines[j])
                    if lines[j].strip().endswith("}"):
                        candidates.append("\n".join(json_block))
                        break
        
        for candidate in candidates:
            parsed = try_parse_json(candidate)
            if not parsed:
                continue
                
            usage = find_usage(parsed)
            if not usage:
                continue
            
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            cost_usd = usage.get("cost") or usage.get("total_cost") or usage.get("total_cost_usd")
            
            token_info = {}
            if prompt_tokens is not None:
                metrics["prompt_tokens"] = prompt_tokens
                token_info["input_tokens"] = prompt_tokens
            if completion_tokens is not None:
                metrics["completion_tokens"] = completion_tokens
                token_info["output_tokens"] = completion_tokens
            if total_tokens is not None:
                metrics["total_tokens"] = total_tokens
                token_info["total_tokens"] = total_tokens
            if cost_usd is not None:
                metrics["total_cost_usd"] = cost_usd
            
            if token_info:
                metrics["_token_info"] = token_info
            
            if metrics.get("total_tokens") and metrics.get("total_cost_usd") is not None:
                break
        
        return metrics

    def _extract_codex_usage_with_regex(self, text: str) -> dict:
        """Extract usage with regex patterns."""
        metrics: dict[str, float | int | None] = {}
        if not text:
            return metrics
        
        prompt_match = re.search(r"(?:prompt|input)\s+tokens?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        completion_match = re.search(r"(?:completion|output)\s+tokens?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        total_match = re.search(r"total\s+tokens?(?:\s+used)?\s*[:=]\s*(\d+)", text, re.IGNORECASE)
        
        cost_match = re.search(
            r"(?:total\s+cost|cost)\s*[:=]\s*\$?\s*([0-9]+(?:\.[0-9]+)?)",
            text,
            re.IGNORECASE
        )
        
        if prompt_match:
            metrics["prompt_tokens"] = int(prompt_match.group(1))
        if completion_match:
            metrics["completion_tokens"] = int(completion_match.group(1))
        if total_match:
            metrics["total_tokens"] = int(total_match.group(1))
        elif metrics.get("prompt_tokens") is not None and metrics.get("completion_tokens") is not None:
            metrics["total_tokens"] = metrics["prompt_tokens"] + metrics["completion_tokens"]
        
        if cost_match:
            try:
                metrics["total_cost_usd"] = float(cost_match.group(1))
            except ValueError:
                pass
        
        return metrics

    def _estimate_codex_cost(self, metrics: dict, config: CodingAgentConfig) -> float | None:
        """Estimate cost from token usage."""
        total_tokens = metrics.get("total_tokens")
        if not total_tokens:
            return None
        
        pricing = MODEL_PRICE_USD_PER_MILLION_TOKENS.get(LlmModel.OPENAI_GPT_5_1)
        if not pricing:
            return None
        
        input_price_per_token = pricing.get("input_price_per_token", 0)
        output_price_per_token = pricing.get("output_price_per_token", 0)
        
        prompt_tokens = metrics.get("prompt_tokens", 0)
        completion_tokens = metrics.get("completion_tokens", 0)
        
        if prompt_tokens and completion_tokens:
            estimated_cost = (prompt_tokens * input_price_per_token) + (completion_tokens * output_price_per_token)
        else:
            estimated_cost = total_tokens * input_price_per_token
        
        return estimated_cost
