# Using erieiron-common in Another Repository

## Installation via requirements.txt

Add the following to your `requirements.txt`:

### Option 1: Install from PyPI (when published)
```txt
erieiron-common>=0.1.0
```

### Option 2: Install from Git Repository
```txt
git+https://github.com/erieironllc/erieiron.git@main
```

### Option 3: Install from Local Path (for development)
```txt
-e /path/to/erieiron/erieiron-common
```

### Option 4: Install with Optional Dependencies
```txt
# Install with all features
erieiron-common[all]>=0.1.0

# Install with specific features
erieiron-common[aws,llm]>=0.1.0
```

## Usage in Django Project

### 1. Add to Django Settings

```python
# settings.py
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Add erieiron_common
    'erieiron_common',
    
    # Your apps
    'myapp',
]

# Optional: Configure LLM API keys
OPENAI_API_KEY = 'your-openai-key'
ANTHROPIC_API_KEY = 'your-anthropic-key'
GEMINI_API_KEY = 'your-gemini-key'

# Optional: Configure AWS
AWS_ACCESS_KEY_ID = 'your-access-key'
AWS_SECRET_ACCESS_KEY = 'your-secret-key'
AWS_DEFAULT_REGION_NAME = 'us-west-2'
```

### 2. Run Migrations

```bash
python manage.py migrate erieiron_common
```

### 3. Use in Your Code

```python
# models.py
from erieiron_common.models import BaseErieIronModel
from erieiron_common.enums import ErieEnum

class MyStatus(ErieEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"

class MyModel(BaseErieIronModel):
    name = models.CharField(max_length=100)
    status = models.CharField(choices=MyStatus.choices())
    # Automatically gets 'erieiron_mymodel' as table name

# views.py
from erieiron_common import get_now, log_info
from erieiron_common.aws_utils import get_aws_interface
from erieiron_common.llm_apis import llm_interface
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.enums import LlmMessageType

def my_view(request):
    # Use common utilities
    current_time = get_now()
    log_info(f"Request processed at {current_time}")
    
    # Use AWS utilities
    aws = get_aws_interface()
    aws.send_email(
        subject="Test Email",
        recipient="user@example.com",
        body="Hello from my app!"
    )
    
    # Use LLM APIs
    messages = [LlmMessage(LlmMessageType.USER, "Hello AI")]
    response = llm_interface.chat(messages)
    
    return JsonResponse({
        'status': 'success',
        'ai_response': response.text
    })

# utils.py
from erieiron_common.message_queue.pubsub_manager import PubSubManager
from erieiron_common.enums import PubSubMessageType

def publish_event(event_type, data):
    PubSubManager.publish(
        PubSubMessageType.ANALYSIS_REQUESTED,
        "business_123",
        data
    )
```

## Usage in Non-Django Project

```python
# For non-Django projects, you can still use many utilities
from erieiron_common import get_now, log_info
from erieiron_common.enums import ErieEnum
from erieiron_common.aws_utils import get_aws_interface
from erieiron_common.llm_apis import llm_interface

# Use common utilities without Django
current_time = get_now()
log_info("Application started")

# Use AWS utilities
aws = get_aws_interface()
# ... use AWS features

# Use LLM APIs
# ... use LLM features

# Note: Django-dependent features (models, migrations, etc.) 
# will require Django to be properly configured
```

## Environment Variables

You can configure the package using environment variables:

```bash
export DJANGO_SETTINGS_MODULE=myproject.settings
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_REGION_NAME=us-west-2
export OPENAI_API_KEY=your-openai-key
export ANTHROPIC_API_KEY=your-anthropic-key
export GEMINI_API_KEY=your-gemini-key
```

## Development Setup

If you're contributing to or modifying erieiron-common:

```bash
# Clone the repo
git clone https://github.com/erieironllc/erieiron.git
cd erieiron

# Install in development mode
pip install -e .[dev]

# In your project's requirements.txt, use:
-e /path/to/erieiron
```

This will allow you to make changes to erieiron-common and see them immediately in your project.