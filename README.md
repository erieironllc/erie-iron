# Erie Iron Common

A comprehensive utilities package providing common infrastructure, utilities, and shared functionality for Erie Iron applications.

## Features

- **Django Integration**: Models, utilities, and infrastructure for Django applications
- **AWS Integration**: Comprehensive AWS utilities including S3, DynamoDB, CloudWatch, and more
- **LLM APIs**: Unified interfaces for OpenAI, Anthropic Claude, Google Gemini, and other language models
- **Message Queue**: PubSub messaging system for distributed application architecture
- **Chat Engine**: Conversational AI infrastructure and chat management
- **Common Utilities**: Shared functions for JSON encoding, date handling, caching, and more

## Installation

Install from PyPI:

```bash
pip install erieiron-common
```

Or install with optional dependencies:

```bash
# Install with all AWS features
pip install erieiron-common[aws]

# Install with machine learning features
pip install erieiron-common[ml]

# Install with LLM API features
pip install erieiron-common[llm]

# Install with Google Cloud Platform features
pip install erieiron-common[gcp]

# Install with development tools
pip install erieiron-common[dev]

# Install everything
pip install erieiron-common[all]
```

Install from source:

```bash
git clone https://github.com/erieironllc/erieiron.git
cd erieiron-common
pip install -e .
```

## Quick Start

### Basic Usage

```python
from erieiron_common import get_now, log_info
from erieiron_common.enums import ErieEnum

# Use common utilities
current_time = get_now()
log_info("Application started")

# Use enum base class
class MyEnum(ErieEnum):
    OPTION_A = "OPTION_A"
    OPTION_B = "OPTION_B"
```

### Django Integration

Add to your Django `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ... your apps
    'erieiron_common',
]
```

Use common models:

```python
from erieiron_common.models import BaseErieIronModel

class MyModel(BaseErieIronModel):
    name = models.CharField(max_length=100)
    # Automatically gets 'erieiron_mymodel' as table name
```

### AWS Utilities

```python
from erieiron_common.aws_utils import get_aws_interface

aws = get_aws_interface()
aws.send_email(
    subject="Test Email",
    recipient="user@example.com",
    body="Hello from Erie Iron!"
)
```

### LLM APIs

```python
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.enums import LlmMessageType

messages = [
    LlmMessage(LlmMessageType.USER, "Hello, how are you?")
]

response = llm_interface.chat(messages)
print(response.text)
```

### Message Queue

```python
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.enums import PubSubMessageType

# Publish a message
PubSubManager.publish(
    PubSubMessageType.ANALYSIS_REQUESTED,
    "business_123",
    {"task": "analyze_business"}
)
```

## Autonomous Agent IaC Provider

- Toggle the IaC backend used by the autonomous agent with the `SELF_DRIVING_IAC_PROVIDER` environment variable (defaults to `opentofu`; set to `cloudformation` for the legacy path).
- When OpenTofu is active, infrastructure stack records persist workspace metadata (workspace name, directory, and state file) in `InfrastructureStack.stack_arn` so downstream systems can resolve deployment state without CloudFormation ARNs.
- The UI now surfaces an "IaC Logs" tab that renders OpenTofu plan/apply output alongside a fallback view for historical CloudFormation payloads.

## License

This project is licensed under the MIT License.

## Support

- Issues: https://github.com/erieironllc/erieiron/issues
- Email: tech@erieironllc.com
