import ast
import logging
import os
import warnings
from threading import Lock
from typing import Optional

warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")
from pathlib import Path

import black
import torch
from numpy import ndarray
from transformers import AutoTokenizer, AutoModel


_CODEBERT_MODEL_NAME = "microsoft/codebert-base"
_codebert_model: Optional[AutoModel] = None
_codebert_tokenizer: Optional[AutoTokenizer] = None
_codebert_lock = Lock()

LANGUAGE_NAMES = {
    '.py': 'python',
    '.js': 'javascript',
}


class CodeCompilationError(Exception):
    def __init__(self, code_str, *args):
        super().__init__(*args)
        self.code_str = code_str


def lint_and_format(code_str: str) -> str:
    code_str = black.format_str(code_str, mode=black.FileMode())
    
    try:
        ast.parse(code_str)
    except Exception as e:
        raise CodeCompilationError(f"syntax error: {e}")
    
    return code_str


def get_codebert_embedding(code_snippet: str) -> ndarray:
    model, tokenizer = _get_codebert_resources()
    tokens = tokenizer(code_snippet, return_tensors='pt', truncation=True, max_length=512)
    with torch.no_grad():
        output = model(**tokens)
        return (output.last_hidden_state[:, 0, :]).squeeze().numpy()


def _get_codebert_resources() -> tuple[AutoModel, AutoTokenizer]:
    global _codebert_model, _codebert_tokenizer
    if _codebert_model is not None and _codebert_tokenizer is not None:
        return _codebert_model, _codebert_tokenizer

    with _codebert_lock:
        if _codebert_model is not None and _codebert_tokenizer is not None:
            return _codebert_model, _codebert_tokenizer

        local_files_only = os.getenv("TRANSFORMERS_OFFLINE", "0") == "1"
        logging.info(
            "Loading CodeBERT resources (%s); local_files_only=%s",
            _CODEBERT_MODEL_NAME,
            local_files_only,
        )

        _codebert_tokenizer = AutoTokenizer.from_pretrained(
            _CODEBERT_MODEL_NAME,
            local_files_only=local_files_only,
        )
        _codebert_model = AutoModel.from_pretrained(
            _CODEBERT_MODEL_NAME,
            local_files_only=local_files_only,
        ).eval()

        return _codebert_model, _codebert_tokenizer


def get_node_text(source_code, node):
    return source_code[node.start_byte:node.end_byte].decode('utf-8')


def walk_tree(node, language):
    methods = []
    method_types = {
        'python': ['function_definition'],
        'javascript': ['function_declaration', 'method_definition']
    }
    if node.type in method_types.get(language, []):
        methods.append(node)
    for child in node.children:
        methods.extend(walk_tree(child, language))
    return methods


def extract_methods_from_file(file_path):
    ext = os.path.splitext(file_path)[1]
    if ext not in LANGUAGE_NAMES:
        raise ValueError(f"Unsupported file extension: {ext}")
    
    with open(file_path, 'rb') as f:
        source_code = f.read()
    
    return extract_methods(ext, source_code)


def extract_methods(ext, source_code):
    if ext not in LANGUAGE_NAMES:
        raise ValueError(f"Unsupported file extension: {ext}")

    language_name = LANGUAGE_NAMES[ext]
    source_text = source_code.decode("utf-8") if isinstance(source_code, bytes) else source_code
    
    return _extract_methods_without_tree_sitter(language_name, source_text)
    # try:
    #     return _extract_methods_with_tree_sitter(language_name, source_text)
    # except (TypeError, ImportError, OSError, AttributeError) as exc:
    #     logging.warning(
    #         "Falling back to non-tree-sitter method extraction for %s: %s",
    #         language_name,
    #         exc,
    #     )
    #     return _extract_methods_without_tree_sitter(language_name, source_text)


def _extract_methods_with_tree_sitter(language_name, source_text):
    import os
    # Prefer system-installed grammars and avoid vendored binaries if present
    os.environ.setdefault("TREE_SITTER_SKIP_VENDOR", "1")
    from tree_sitter_languages import get_parser

    parser = get_parser(language_name)
    source_bytes = source_text.encode("utf-8") if isinstance(source_text, str) else source_text
    tree = parser.parse(source_bytes)
    method_nodes = walk_tree(tree.root_node, language_name)

    method_info = []
    for node in method_nodes:
        method_info.append({
            'name': get_method_name(node, language_name),
            'signature': get_method_signature(source_bytes, node, language_name),
            'parameters': get_method_parameters(source_bytes, node, language_name),
            'start_line': node.start_point[0] + 1,
            'end_line': node.end_point[0] + 1,
            'code': get_node_text(source_bytes, node),
            'language': language_name
        })

    return method_info


def _extract_methods_without_tree_sitter(language_name, source_text):
    if language_name == 'python':
        return _extract_python_methods_with_ast(source_text)

    logging.warning(
        "Method extraction for language %s requires tree-sitter; returning no methods",
        language_name,
    )
    return []


def _extract_python_methods_with_ast(source_text: str):
    try:
        module = ast.parse(source_text)
    except SyntaxError as exc:
        logging.warning("Unable to parse python source for method extraction: %s", exc)
        return []

    methods = []
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parameters = ast.unparse(node.args) if hasattr(ast, "unparse") else ""
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            start_line = getattr(node, "lineno", None)
            end_line = getattr(node, "end_lineno", start_line)
            code_segment = ast.get_source_segment(source_text, node)
            if code_segment is None and start_line is not None and end_line is not None:
                code_segment = _get_source_by_lines(source_text, start_line, end_line)

            methods.append({
                'name': node.name,
                'signature': f"{prefix} {node.name}({parameters})",
                'parameters': parameters,
                'start_line': start_line,
                'end_line': end_line,
                'code': code_segment or "",
                'language': 'python'
            })

    return methods


def _get_source_by_lines(source_text: str, start_line: int, end_line: int):
    lines = source_text.splitlines(keepends=True)
    # ast end_lineno is inclusive, make sure indices are within bounds
    start_idx = max(start_line - 1, 0)
    end_idx = min(end_line, len(lines))
    return "".join(lines[start_idx:end_idx])


def get_method_name(node, language):
    if language == 'python':
        for child in node.children:
            if child.type == 'identifier':
                return child.text.decode('utf-8')
    elif language == 'javascript':
        for child in node.children:
            if child.type in ('identifier', 'property_identifier'):
                return child.text.decode('utf-8')
    return '<anonymous>'


def get_method_signature(source_code, node, language):
    if language == 'python':
        header_node = next((c for c in node.children if c.type == 'parameters'), None)
        name = get_method_name(node, language)
        if header_node:
            return f"def {name}{get_node_text(source_code, header_node)}"
    elif language == 'javascript':
        params_node = next((c for c in node.children if c.type == 'formal_parameters'), None)
        name = get_method_name(node, language)
        if params_node:
            return f"function {name}{get_node_text(source_code, params_node)}"
    return '<unknown signature>'


def get_method_parameters(source_code, node, language):
    if language == 'python':
        param_node = next((c for c in node.children if c.type == 'parameters'), None)
        if param_node:
            return get_node_text(source_code, param_node)[1:-1].strip()  # Strip parentheses
    elif language == 'javascript':
        param_node = next((c for c in node.children if c.type == 'formal_parameters'), None)
        if param_node:
            return get_node_text(source_code, param_node)[1:-1].strip()  # Strip parentheses
    return ''



def looks_like_unified_diff(text: str) -> bool:
    """
    Heuristic check for a unified git diff. We keep it intentionally simple:
    must start with '--- ' (after leading whitespace), contain a '+++' header and at least one '@@' hunk.
    """
    if not isinstance(text, str):
        return False
    t = text.lstrip()
    return t.startswith('--- ') and '\n+++' in t and '\n@@' in t


def apply_unified_diff_to_text(original_text: str, patch_text: str) -> str:
    """
    Apply a single-file unified diff to original_text and return the patched text.
    This is an in-memory applier that supports standard unified diff hunks.
    It intentionally does not support multi-file patches or renames.

    Raises ValueError if the patch cannot be applied cleanly.
    """
    import re
    
    # --- helpers ---------------------------------------------------------
    def _norm_line(s: str) -> str:
        """Normalize platform newlines to LF, preserve trailing newline if present."""
        return s.replace('\r\n', '\n') if isinstance(s, str) else s
    
    def _rstrip_nl(s: str) -> str:
        """Remove a single trailing LF if present."""
        if s.endswith('\n'):
            return s[:-1]
        return s
    
    def _ctx_match(out_line: str, content_wo_nl: str) -> bool:
        """Compare a file line (with possible trailing NL) to patch content (no NL)."""
        ol = _norm_line(out_line)
        return _rstrip_nl(ol) == content_wo_nl
    
    # --------------------------------------------------------------------
    lines = original_text.splitlines(keepends=True)
    patch_lines = patch_text.splitlines(keepends=False)
    
    # Parse headers (single-file only)
    i = 0
    while i < len(patch_lines) and not patch_lines[i].startswith('--- '):
        i += 1
    if i >= len(patch_lines) or not patch_lines[i].startswith('--- '):
        raise ValueError('Missing --- header in unified diff')
    old_hdr = patch_lines[i]
    i += 1
    if i >= len(patch_lines) or not patch_lines[i].startswith('+++ '):
        raise ValueError('Missing +++ header in unified diff')
    new_hdr = patch_lines[i]
    i += 1
    
    # Reject multi-file patches early if we see a second file header later
    def _is_file_header(line: str) -> bool:
        return line.startswith('--- ') or line.startswith('+++ ')
    
    # Hunk header pattern
    hunk_re = re.compile(r'^@@ -(?P<old_start>\d+)(,(?P<old_count>\d+))? \+(?P<new_start>\d+)(,(?P<new_count>\d+))? @@')
    
    out = lines[:]
    line_offset = 0
    
    # If any hunk indicates the resulting file should not end with a newline,
    # we will trim it at the end.
    final_strip_trailing_newline = False
    
    while i < len(patch_lines):
        # Explicitly fail on multi-file patches
        if _is_file_header(patch_lines[i]):
            raise ValueError('Multi-file patch not supported')
        
        if not patch_lines[i].startswith('@@ '):
            i += 1
            continue
        
        m = hunk_re.match(patch_lines[i])
        if not m:
            raise ValueError(f'Invalid hunk header: {patch_lines[i]}')
        i += 1
        
        old_start = int(m.group('old_start'))
        old_count = int(m.group('old_count') or '0')
        new_start = int(m.group('new_start'))
        new_count = int(m.group('new_count') or '0')
        
        # Convert 1-based to 0-based index into 'out' with current offset
        idx = old_start - 1 + line_offset
        
        # Build the replacement block from hunk lines
        removal_count = 0
        addition_block = []
        ctx_count = 0
        addition_count = 0
        
        last_sign = None
        saw_no_nl_marker = False
        probe_idx = idx
        
        while i < len(patch_lines) and not patch_lines[i].startswith('@@ '):
            # Stop if another file header appears (unsupported multi-file)
            if _is_file_header(patch_lines[i]):
                break
            
            curr_patch_line = i
            line = patch_lines[i]
            
            if line == r'\ No newline at end of file':
                saw_no_nl_marker = True
                i += 1
                continue
            
            sign = line[:1] if line else ''
            content = line[1:] if len(line) > 0 else ''
            
            if sign == ' ':
                # Context: must match existing
                if probe_idx >= len(out) or not _ctx_match(out[probe_idx], content):
                    raise ValueError(f'Context mismatch while applying hunk starting at -{old_start} (patch line {curr_patch_line + 1})')
                addition_block.append(out[probe_idx])
                probe_idx += 1
                ctx_count += 1
                last_sign = ' '
            elif sign == '-':
                # Removal: must match existing
                if probe_idx >= len(out) or not _ctx_match(out[probe_idx], content):
                    raise ValueError(f'Removal mismatch while applying hunk starting at -{old_start} (patch line {curr_patch_line + 1})')
                removal_count += 1
                probe_idx += 1
                last_sign = '-'
            elif sign == '+':
                # Addition: append with newline; may be trimmed by marker
                addition_block.append(content + '\n')
                addition_count += 1
                last_sign = '+'
            else:
                raise ValueError(f'Unknown hunk line prefix: {sign!r} (patch line {curr_patch_line + 1})')
            
            i += 1
        
        # Apply the standard "no newline at end of file" marker semantics
        if saw_no_nl_marker:
            if addition_block and last_sign in ('+', ' '):
                addition_block[-1] = _rstrip_nl(addition_block[-1])
                # If the marker followed a '+' line, the resulting file should not end with a newline
                if last_sign == '+':
                    final_strip_trailing_newline = True
            else:
                # No additions in this hunk: try to trim newline on the last context line already in 'out'
                if idx + ctx_count - 1 >= 0 and idx + ctx_count - 1 < len(out):
                    out[idx + ctx_count - 1] = _rstrip_nl(out[idx + ctx_count - 1])
                    final_strip_trailing_newline = True
        
        # Validate hunk counts if provided
        if old_count and old_count != ctx_count + removal_count:
            raise ValueError(f'Hunk old_count mismatch: expected {old_count}, got {ctx_count + removal_count}')
        if new_count and new_count != ctx_count + addition_count:
            raise ValueError(f'Hunk new_count mismatch: expected {new_count}, got {ctx_count + addition_count}')
        
        # Apply: replace the slice [idx, idx + removed) with addition_block
        out[idx:idx + removal_count] = addition_block
        # Update offset for subsequent hunks: new_len - old_len
        line_offset += len(addition_block) - removal_count
    
    result = ''.join(out)
    if final_strip_trailing_newline and result.endswith('\n'):
        result = result[:-1]
    return result
