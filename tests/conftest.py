import sys
import types


class _StubSentenceTransformer:
    def __init__(self, *_, **__):
        self.model_name = "stubbed"

    def encode(self, *args, **kwargs):  # pragma: no cover - deterministic stub
        return [0.0]


stub_module = types.ModuleType("sentence_transformers")
stub_module.SentenceTransformer = _StubSentenceTransformer
sys.modules.setdefault("sentence_transformers", stub_module)
