import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from erieiron_autonomous_agent.coding_agents.coding_agent_config import CodingAgentConfig
from erieiron_autonomous_agent.coding_agents.self_driving_coder_exceptions import ExecutionException
from .claude_coder import ClaudeCoder
from .codex_coder import CodexCoder
from .gemini_coder import GeminiCoder
from .individual_file_coder import IndividualFileCoder
from ...utils.codegen_utils import CodeCompilationError, validate_dockerfile

CODERS = [
    ("Codex", CodexCoder),
    ("Gemini", GeminiCoder),
    ("Claude", ClaudeCoder),
    ("Individual", IndividualFileCoder)
]


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
    
    errors = []
    for coder_name, coder_cls in CODERS:
        coder = coder_cls(config, planning_data)
        try:
            config.log(f"Attempting code generation with {coder_name} coder")
            
            changed_paths, metadata = coder.execute_coding()
            
            # Add coder info to metadata
            config.log(f"{coder_name} coder execution completed successfully")
            metadata["successful_coder"] = coder_name.lower()
            metadata["attempts_made"] = len(errors) + 1
            metadata["failed_coders"] = [e["coder"] for e in errors]
            
            return changed_paths, metadata
        
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


def validate_code(
        config: CodingAgentConfig,
        code_file_path: Path,
        code: str,
        validator=None
) -> str:
    code_file_name = code_file_path.name.lower()
    if validator == "jinja":
        try:
            from jinja2 import Environment
            Environment().parse(code)
        except Exception as e:
            raise CodeCompilationError(code, f"Jinja syntax error: {e}")
    elif validator == "django_template":
        from django.template import Template
        try:
            Template(code)
        except Exception as e:
            raise CodeCompilationError(code, f"Django template syntax error: {e}")
    elif code_file_name.endswith(".js"):
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix=".js", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["eslint", "--no-eslintrc", "--stdin", "--stdin-filename", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                raise CodeCompilationError(code, f"JavaScript lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif code_file_name.endswith(".css"):
        import subprocess
        import tempfile
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix=".css", delete=False) as tmp:
                tmp.write(code)
                tmp.flush()
                result = subprocess.run(
                    ["stylelint", tmp.name],
                    capture_output=True,
                    text=True
                )
            if result.returncode != 0:
                raise CodeCompilationError(code, f"CSS lint errors:\n{result.stdout.strip()}")
        finally:
            os.remove(tmp.name)
    
    elif code_file_name.endswith("json"):
        try:
            json.loads(code)
        except Exception as e:
            raise CodeCompilationError(code, f"json parse error:\n{e}")
    
    elif "Dockerfile" in code_file_name:
        validate_dockerfile(
            config.sandbox_root_dir,
            code_file_name
        )
    
    elif code_file_name.endswith(".yaml"):
        try:
            data = yaml.load(code, Loader=yaml.BaseLoader)
        except Exception as e:
            raise CodeCompilationError(code, f"yaml parse error:\n{e}")
    
    elif code_file_name.endswith(".py"):
        import ast
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise CodeCompilationError(code, f"Syntax error in Python file '{code_file_name}': {e}")
    
    elif code_file_name == "requirements.txt":
        # noinspection PyProtectedMember
        from pip._internal.req.constructors import install_req_from_line
        # noinspection PyProtectedMember
        from pip._internal.exceptions import InstallationError
        
        lines = code.splitlines()
        for i, line in enumerate(lines, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue  # Allow empty lines and comments
            try:
                install_req_from_line(line)
            except InstallationError as e:
                raise CodeCompilationError(line, f"Invalid requirement on line {i}: '{line}' — {e}")
    
    return code
