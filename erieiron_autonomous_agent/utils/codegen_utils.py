import ast

import black
import torch
from numpy import ndarray
from transformers import AutoTokenizer, AutoModel

tokenizer = AutoTokenizer.from_pretrained("microsoft/codebert-base")
model = AutoModel.from_pretrained("microsoft/codebert-base")


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


def embed_code(code_snippet: str) -> ndarray:
    tokens = tokenizer(code_snippet, return_tensors='pt', truncation=True, max_length=512)
    with torch.no_grad():
        output = model(**tokens)
        return (output.last_hidden_state[:, 0, :]).squeeze().numpy()
