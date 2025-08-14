import ast
import os
from pathlib import Path

import black
import torch
from numpy import ndarray
from transformers import AutoTokenizer, AutoModel
from tree_sitter import Language, Parser

tokenizer = AutoTokenizer.from_pretrained("microsoft/codebert-base")
model = AutoModel.from_pretrained("microsoft/codebert-base")

TREE_SITTER_LIB_PATH = 'build/my-languages.so'
Language.build_library(
    TREE_SITTER_LIB_PATH,
    [
        'vendor/tree-sitter-python',
        'vendor/tree-sitter-javascript'
    ]
)

LANGUAGES = {
    '.py': ('python', 'tree-sitter-python'),
    '.js': ('javascript', 'tree-sitter-javascript'),
}

LANG_OBJS = {}
for ext, (lang_name, _) in LANGUAGES.items():
    LANG_OBJS[ext] = Language(TREE_SITTER_LIB_PATH, lang_name)

PARSERS = {ext: Parser() for ext in LANGUAGES}
for ext in PARSERS:
    PARSERS[ext].set_language(LANG_OBJS[ext])


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
    tokens = tokenizer(code_snippet, return_tensors='pt', truncation=True, max_length=512)
    with torch.no_grad():
        output = model(**tokens)
        return (output.last_hidden_state[:, 0, :]).squeeze().numpy()


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
    if ext not in LANGUAGES:
        raise ValueError(f"Unsupported file extension: {ext}")
    
    with open(file_path, 'rb') as f:
        source_code = f.read()
    
    return extract_methods(ext, source_code)


def extract_methods(ext, source_code):
    source_code = source_code.encode("utf-8") if isinstance(source_code, str) else source_code
    language_name, _ = LANGUAGES[ext]
    parser = PARSERS[ext]
    tree = parser.parse(source_code)
    method_nodes = walk_tree(tree.root_node, language_name)
    
    method_info = []
    for node in method_nodes:
        method_info.append({
            'name': get_method_name(node, language_name),
            'signature': get_method_signature(source_code, node, language_name),
            'parameters': get_method_parameters(source_code, node, language_name),
            'start_line': node.start_point[0] + 1,
            'end_line': node.end_point[0] + 1,
            'code': get_node_text(source_code, node),
            'language': language_name
        })
    
    return method_info


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


def validate_dockerfile(context_dir, dockerfile_path):
    import subprocess
    dockerfile_path = Path(dockerfile_path)
    
    result = subprocess.run(
        ["docker", "build", "-f", dockerfile_path, "--no-cache", "--pull", "--quiet", context_dir],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise CodeCompilationError(
            dockerfile_path.read_text(),
            f"Dockerfile has errors:\n {result.stderr}"
        )


def looks_like_unified_diff(text: str) -> bool:
    """
    Heuristic check for a unified git diff. We keep it intentionally simple:
    must start with '--- ' (after leading whitespace), contain a '+++' header and at least one '@@' hunk.
    """
    if not isinstance(text, str):
        return False
    t = text.lstrip()
    return t.startswith('--- ') and '\n+++' in t and '\n@@' in t


def apply_unified_diff_to_text(original_text: str, patch_text: str, expected_relpath: str | None = None) -> str:
    """
    Apply a single-file unified diff to original_text and return the patched text.
    This is a minimal, in-memory applier that supports standard unified diff hunks.
    It does not support file renames; callers should treat renames as delete+add.

    Raises ValueError if the patch cannot be applied cleanly.
    """
    import re
    
    lines = original_text.splitlines(keepends=True)
    patch_lines = patch_text.splitlines(keepends=False)
    
    # Parse headers
    i = 0
    # Skip any leading blank/prologue lines the model might include
    while i < len(patch_lines) and not patch_lines[i].startswith('--- '):
        i += 1
    if i >= len(patch_lines) or not patch_lines[i].startswith('--- '):
        raise ValueError('Missing --- header in unified diff')
    old_hdr = patch_lines[i];
    i += 1
    if i >= len(patch_lines) or not patch_lines[i].startswith('+++ '):
        raise ValueError('Missing +++ header in unified diff')
    new_hdr = patch_lines[i];
    i += 1
    
    # Optional path sanity check
    def _extract_path(h):
        # headers look like: '--- a/path' or '--- /dev/null'
        parts = h.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ''
    
    old_path = _extract_path(old_hdr)[2:] if _extract_path(old_hdr).startswith('a/') else _extract_path(old_hdr)
    new_path = _extract_path(new_hdr)[2:] if _extract_path(new_hdr).startswith('b/') else _extract_path(new_hdr)
    
    if expected_relpath:
        exp = expected_relpath.lstrip('./')
        # Allow either old or new header to match expected path (new files use /dev/null in old header)
        header_paths = {old_path.lstrip('./'), new_path.lstrip('./')}
        # If headers contain absolute or sandboxed paths, only compare the tail
        header_tails = {p.split('/')[-len(exp.split('/')):] for p in header_paths if p}
        # Quick tail match
        if exp not in header_paths and not any('/'.join(tail) == exp for tail in header_tails):
            # Do not fail hard; models sometimes emit slightly different prefixes. Continue.
            pass
    
    # Pattern for hunk header: @@ -l,s +l,s @@
    hunk_re = re.compile(r'^@@ -(?P<old_start>\d+)(,(?P<old_count>\d+))? \+(?P<new_start>\d+)(,(?P<new_count>\d+))? @@')
    
    # Work on a mutable copy
    out = lines[:]
    # Offset adjustment as we apply hunks
    line_offset = 0
    
    while i < len(patch_lines):
        if not patch_lines[i].startswith('@@ '):
            # Skip non-hunk lines (e.g., file metadata) until next hunk
            i += 1
            continue
        
        m = hunk_re.match(patch_lines[i])
        if not m:
            raise ValueError(f'Invalid hunk header: {patch_lines[i]}')
        i += 1
        
        old_start = int(m.group('old_start'))
        old_count = int(m.group('old_count') or '0')
        new_start = int(m.group('new_start'))
        # new_count unused for application
        
        # Convert 1-based to 0-based index into 'out' with current offset
        idx = old_start - 1 + line_offset
        
        # Build the replacement block from hunk lines
        removal_count = 0
        addition_block = []
        
        # We'll also validate context lines against current 'out'
        probe_idx = idx
        
        while i < len(patch_lines) and not patch_lines[i].startswith('@@ '):
            line = patch_lines[i]
            if line.startswith('--- ') or line.startswith('+++ '):
                # Next file header - single-file patch expected; stop processing
                break
            sign = line[:1] if line else ''
            content = line[1:] if len(line) > 0 else ''
            
            if sign == ' ':
                # Context: must match existing
                if probe_idx >= len(out) or out[probe_idx] != content + ('\n' if not out[probe_idx].endswith('\n') and content != '' else '') and out[probe_idx].rstrip('\n') != content:
                    # Be tolerant of LF/CRLF variations
                    if out[probe_idx].replace('\r\n', '\n').rstrip('\n') != content:
                        raise ValueError('Context mismatch while applying hunk')
                addition_block.append(out[probe_idx])
                probe_idx += 1
            elif sign == '-':
                # Removal: must match existing
                if probe_idx >= len(out) or out[probe_idx].replace('\r\n', '\n').rstrip('\n') != content:
                    raise ValueError('Removal mismatch while applying hunk')
                removal_count += 1
                probe_idx += 1
            elif sign == '+':
                # Addition: append with newline
                addition_block.append(content + '\n')
            elif line == r'\ No newline at end of file':
                # Ignore standard marker
                pass
            else:
                # Unknown marker - treat as error
                raise ValueError(f'Unknown hunk line prefix: {sign!r}')
            i += 1
            if i >= len(patch_lines):
                break
        
        # Apply: replace the slice [idx, idx + removed) with addition_block
        out[idx:idx + removal_count] = addition_block
        # Update offset for subsequent hunks: new_len - old_len
        line_offset += len(addition_block) - removal_count
        
        # Stop if another file header appears (we only support single-file patches here)
        if i < len(patch_lines) and (patch_lines[i].startswith('--- ') or patch_lines[i].startswith('+++ ')):
            break
    
    return ''.join(out)

