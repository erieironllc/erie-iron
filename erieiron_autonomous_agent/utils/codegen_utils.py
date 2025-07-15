import ast
import os

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
