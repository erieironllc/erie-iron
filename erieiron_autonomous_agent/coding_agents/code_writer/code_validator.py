import json
import os
import subprocess
import tempfile
from enum import auto
from pathlib import Path

import yaml

from erieiron_autonomous_agent.utils.codegen_utils import CodeCompilationError
from erieiron_common import common, ErieEnum


class JavascriptContext(ErieEnum):
    BARE = auto()
    REACT = auto()
    NODE = auto()


class LintRules(ErieEnum):
    ALLOW_USED_VARS = auto()
    ALLOW_UNDEFINED_VARS = auto()


def validate(code_file_path: Path):
    """
    Validate a code file by applying syntax or lint checks based on file type.

    This function inspects the file extension and routes validation to the
    appropriate subsystem. It raises `CodeCompilationError` when validation
    fails for any supported type.

    Args:
        code_file_path (Path): Absolute or relative path to the code file
            that should be validated.
    Raises:
        CodeCompilationError: If the file fails any syntax or lint validation.
    """
    code_file_path = common.assert_exists(code_file_path)
    code_file_name = code_file_path.name.lower()
    code = code_file_path.read_text(encoding="utf-8")
    
    validator = None  # think about how we might implement this
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
    elif code_file_name.endswith(".tsx"):
        # TypeScript React validation using ESLint
        run_eslint(code, ".tsx")
    
    elif code_file_name.endswith(".ts"):
        # TypeScript validation using ESLint
        run_eslint(code, ".ts")
    
    elif code_file_name.endswith(".jsx"):
        # React JSX validation using ESLint
        run_eslint(code, ".jsx")
    
    elif code_file_name.endswith(".js"):
        ctx = detect_js_context(code_file_path)
        if JavascriptContext.REACT.eq(ctx):
            run_eslint(code, ".js")
        elif JavascriptContext.NODE.eq(ctx):
            run_eslint(code, ".js", [LintRules.ALLOW_USED_VARS])
        elif JavascriptContext.BARE.eq(ctx):
            run_eslint(code, ".js", [LintRules.ALLOW_USED_VARS, LintRules.ALLOW_UNDEFINED_VARS])
        else:
            raise Exception(f"unhandled javascript context: {ctx}")
    
    elif code_file_name.endswith(".css"):
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


def validate_dockerfile(dockerfile_path):
    dockerfile_path = Path(dockerfile_path)
    
    result = subprocess.run(
        ["docker", "build", "-f", dockerfile_path, "--no-cache", "--pull", "--quiet", dockerfile_path.parent],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise CodeCompilationError(
            dockerfile_path.read_text(),
            f"Dockerfile has errors:\n {result.stderr}"
        )


def run_eslint(
        code: str,
        file_extension: str,
        rules: list[LintRules] = None
) -> None:
    """
    Run ESLint on code content and raise CodeCompilationError if validation fails.

    Args:
        code: The source code to validate
        file_extension: File extension (.js, .jsx, .ts, .tsx) to determine parser

    Raises:
        CodeCompilationError: If ESLint finds errors
    """
    # Get the project root directory (where package.json and .eslint-flat-config.js are located)
    project_root = Path(os.getcwd())
    eslint_config = common.assert_exists(project_root / ".eslint-flat-config.js")
    
    # Create temporary file with appropriate extension
    with tempfile.NamedTemporaryFile(
            mode='w+',
            suffix=file_extension,
            delete=False,
            dir=str(project_root)
    ) as tmp:
        tmp_path = tmp.name
        try:
            tmp.write(code)
            tmp.flush()
            
            cmd = ["npx", "eslint"]
            
            for rule in LintRules.to_list(rules):
                if LintRules.ALLOW_USED_VARS.eq(rule):
                    cmd += ["--rule", "no-unused-vars:off"]
                elif LintRules.ALLOW_UNDEFINED_VARS.eq(rule):
                    cmd += ["--rule", "no-undef:off"]
                else:
                    raise Exception(f"unsupported LintRule {rule}")
            
            cmd = cmd + [
                "--config", str(eslint_config),
                tmp_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(project_root)
            )
            
            if result.returncode != 0:
                # ESLint found errors
                error_output = result.stdout.strip() if result.stdout else result.stderr.strip()
                raise CodeCompilationError(code, f"ESLint validation errors:\n{error_output}")
        finally:
            # Clean up temporary file
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def detect_js_context(start_path: Path) -> JavascriptContext:
    code = common.assert_exists(start_path).read_text()
    if "View.extend(" in code:
        return JavascriptContext.BARE
    
    """
    Return 'react', 'node', or 'bare' based on nearest package.json.
    """
    p = start_path.resolve()
    for parent in [p, *p.parents]:
        views_file = parent / "views.py"
        if views_file.exists():
            # if the javascript is part of a django project, assume it's bare
            # node / react stuff usually in a different part of a monorepo
            return JavascriptContext.BARE
        
        pkg = parent / "package.json"
        if pkg.exists():
            try:
                data = json.loads(pkg.read_text())
            except Exception:
                return JavascriptContext.BARE
            
            deps = data.get("dependencies", {}) or {}
            dev = data.get("devDependencies", {}) or {}
            all_deps = {**deps, **dev}
            
            # React indicators
            if "react" in all_deps or "react-dom" in all_deps:
                return JavascriptContext.REACT
            
            # Node indicators
            if data.get("type") in ("module", "commonjs"):
                return JavascriptContext.NODE
            node_signatures = {"express", "koa", "fastify", "node-fetch"}
            if any(d in all_deps for d in node_signatures):
                return JavascriptContext.NODE
            
            # Unknown JS project
            return JavascriptContext.BARE
    
    # No package.json anywhere
    return JavascriptContext.BARE
