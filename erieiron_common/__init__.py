"""
Erie Iron Common Utilities Package

This package provides common utilities, infrastructure components, and shared
functionality for Erie Iron applications.

Key modules:
- common: Core utility functions and helpers
- enums: Shared enumeration definitions
- models: Django model base classes and common models
- aws_utils: AWS integration utilities
- llm_apis: Language model API interfaces
- message_queue: PubSub messaging infrastructure
- chat_engine: Chat and conversation management
"""

__version__ = '0.1.0'
__author__ = 'Erie Iron LLC'
__email__ = 'tech@erieiron.com'

from .common import get_now, log_info, log_error
# Import commonly used components for easier access
from .enums import ErieEnum
from .json_encoder import ErieIronJSONEncoder

# Version info
VERSION = (0, 1, 0)


def get_version():
    """Return the version string."""
    return '.'.join(str(x) for x in VERSION)


# Package metadata
__all__ = [
    'ErieEnum',
    'get_now',
    'log_info',
    'log_error',
    'ErieIronJSONEncoder',
    'get_version',
    '__version__',
]
