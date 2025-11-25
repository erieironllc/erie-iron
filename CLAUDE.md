# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Essential Commands

### Environment Setup
```bash
python -m venv .venv && source .venv/bin/activate  # (.venv\Scripts\activate on Windows)
pip install -r requirements.txt && pip install -e .
npm install  # One-time frontend setup
```

### Development Workflow
```bash
# Frontend Assets
npm run compile-ui  # Build Backbone.js/Bootstrap bundle
npm run watch       # Auto-rebuild on file changes
```

**never** run the `black` code formatter (or any code formatter) over unmodified lines of code unless explicitly instructed to do so 

### Package Management
```bash
# Install with optional dependencies
pip install erieiron-common[all]     # Everything
pip install erieiron-common[aws]     # AWS features only
pip install erieiron-common[llm]     # LLM APIs only
pip install erieiron-common[dev]     # Development tools
```

## Architecture Overview

### High-Level System Design
Erie Iron is an autonomous business management platform that uses AI agents to operate businesses with minimal human intervention. The system combines infrastructure automation, AI-driven development, and comprehensive monitoring.

**Core Philosophy**: Each business entity operates as an autonomous unit with AI agents handling CEO, engineering, marketing, and product decisions while maintaining human oversight through governance structures.

### Multi-Agent Architecture
- **Board Level Agents**: Strategic oversight, portfolio planning, corporate development
- **Business Level Agents**: CEO, engineering lead, product lead, marketing lead, task manager
- **Coding Agents**: Self-driving coder, ML packager, credential manager
- **Worker Agents**: Specialized execution for coding, design, and human coordination

### Technology Stack
- **Backend**: Django 5.1.6 + PostgreSQL with pgvector for embeddings
- **Frontend**: Backbone.js + Bootstrap 5.3.3 + jQuery, built with Gulp
- **AI/ML**: Multiple LLM APIs (OpenAI, Anthropic, Google, DeepSeek), Sentence Transformers
- **Infrastructure**: AWS-native with CloudFormation, Docker containers, extensive AWS service integration
- **Message Queue**: Custom PubSub system for distributed agent coordination

### Key Modules

#### `erieiron_common/`
Shared infrastructure providing:
- **AWS Integration**: Comprehensive boto3 wrappers for all major AWS services
- **LLM APIs**: Unified interface for multiple AI providers with response parsing
- **Message Queue**: PubSub system for agent communication and task coordination
- **Chat Engine**: Conversational AI infrastructure with context management
- **Domain Manager**: DNS, SSL certificate, and domain lifecycle automation

#### `erieiron_autonomous_agent/`
Agent orchestration and task automation:
- **Models**: Business entities, tasks, infrastructure stacks, LLM request tracking
- **Agent Workflows**: Multi-level agent coordination with escalation patterns
- **Self-Driving Coder**: Autonomous code development with CloudFormation deployment
- **Management Commands**: CLI tools for agent execution and monitoring

#### `erieiron_ui/`
Django-based web interface:
- **Backbone.js Views**: Real-time monitoring of agent activities and business metrics
- **Bootstrap Components**: Responsive UI for business management and agent oversight
- **WebSocket Integration**: Live updates for agent activities and deployment status

### Database Architecture
- **UUID Primary Keys**: All models use UUID4 for distributed system compatibility
- **Business Hierarchy**: Business → Initiative → Task with agent assignments
- **Infrastructure Tracking**: CloudFormation stacks, deployments, and resource monitoring
- **LLM Request Logging**: Comprehensive tracking of all AI interactions for analysis and billing
- **Agent Learning**: Lesson extraction and knowledge base for continuous improvement

### AWS Infrastructure Pattern
- **Two-Tier Deployment**: Foundation (networking) + Application (services) CloudFormation stacks
- **Environment Isolation**: Separate dev/production with automated lifecycle management  
- **Service Integration**: Lambda, ECR, S3, SES, ELB, Secrets Manager, RDS coordination
- **Monitoring**: Multi-layer observability across CloudFormation, CloudWatch, CloudTrail, ECS

### Agent Communication Flow
1. **Task Creation**: Human or agent creates business tasks through UI or API
2. **Agent Assignment**: Task manager assigns appropriate agents based on task type
3. **Execution Coordination**: Agents communicate via PubSub for complex multi-step operations
4. **Infrastructure Provisioning**: Self-driving coder deploys via CloudFormation with monitoring
5. **Learning Integration**: All executions feed into lesson extraction for continuous improvement

## Development Guidelines

### Code Style Conventions
- **Python**: Black formatter (120 chars), snake_case functions, CamelCase classes
- **JavaScript**: ESLint defaults, Backbone patterns, consistent Sass naming
- **Configuration**: ASCII, sorted where practical, follow established patterns

### Testing Approach
- **pytest-django** framework with SQLite in-memory database for speed
- **Fixtures**: ML dependency stubs to avoid heavy model loading in tests
- **Coverage Strategy**: Focus on agent workflows, LLM integration, deployment coordination
- **Integration Tests**: Mark with `@pytest.mark.integration` for selective execution

### Agent Development Patterns
- **Prompt Engineering**: Extensive prompt library in `/prompts/` with JSON schemas
- **LLM Integration**: Use unified `llm_interface` for all AI interactions
- **Error Handling**: Comprehensive error logging with agent lesson extraction
- **State Management**: Track agent progress in database models with status fields

### Infrastructure as Code
- **CloudFormation Templates**: Located in `/aws/cloudformation/` and `/cloudformation/`
- **Parameter Injection**: Automatic AWS account, region, and environment configuration
- **Stack Lifecycle**: Automated creation, update, deletion with comprehensive monitoring
- **Multi-Environment**: Dev stacks use random tokens, production uses business identifiers

### AWS Service Integration
When working with AWS services, use the utilities in `erieiron_common/aws_utils.py` rather than direct boto3 calls. These provide:
- Consistent error handling and logging
- Automatic credential management
- Standardized naming conventions
- Integration with monitoring and alerting

### LLM Integration Best Practices
- Use `erieiron_common/llm_apis/llm_interface.py` for all AI interactions
- Track all requests in `LlmRequest` model for billing and analysis
- Follow prompt engineering patterns in `/prompts/` directory
- Implement proper error handling for rate limits and API failures
- Extract lessons from failures using `lesson_extractor.md` prompt

### Frontend Development
- Backbone.js views should extend base classes in `erieiron_common/js/`
- Use Bootstrap components consistently across all interfaces
- Implement real-time updates via WebSocket for agent status changes
- Follow existing Sass organization in `erieiron_ui/sass/`

## Important Implementation Notes

### Agent Execution Context
All agents operate within a structured context that includes:
- Business entity with AWS environment and domain configuration
- Initiative scope with infrastructure stack references
- Task assignment with success criteria and escalation patterns
- Comprehensive logging and monitoring for all activities

### Security and Credentials
- **AWS Credentials**: Managed through IAM roles and Secrets Manager
- **API Keys**: Stored in AWS Secrets Manager with namespace isolation
- **Domain Security**: Automatic SSL certificate provisioning and DNS management
- **Network Security**: Shared VPC with security group management

### Deployment Architecture
- **Container Strategy**: Docker images for web service and message processor
- **ECS Deployment**: Automated deployment with health checks and rollback
- **Infrastructure Monitoring**: CloudFormation stack events with failure analysis
- **Log Aggregation**: Multi-service log collection and correlation

### Multi-Business Operations
The system is designed to manage multiple autonomous businesses simultaneously:
- Each business operates in isolated AWS environments
- Shared infrastructure for monitoring, logging, and agent coordination
- Portfolio-level resource planning and optimization
- Board-level oversight with strategic guidance and intervention capabilities

---

# Front end rules
- All front-end javascript must be in a backbone style view.  template from view-codefile.js
- **never** use "$.ajax(" to communicate with the server.  Use "erie_server().exec_server_post()" instead
- Strongly favor painting all html on page load view HTML returned from the server.  Do not unnecessarily dynamically render javascript using dom manipulation. 

# AWS interactions
- Use aws_utils.py for all aws interactions