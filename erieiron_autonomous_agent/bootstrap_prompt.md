You are a senior software engineer building a production-ready Python web application template called `bootstrap_project`.

Your task is to generate the complete source code for this project, including all required files, in a directory layout that is immediately runnable both locally and on AWS. The project is designed to serve as a starting point for deploying fully containerized AWS applications for Erie Iron.

## Requirements:

### General:
- Language: Python 3.11
- Framework: Django (latest compatible version)
- Frontend: Django templates with Bootstrap 5, jQuery, and Sass
- Use Gulp to compile static assets (sass → css, js bundling if needed)
- Use PostgreSQL via RDS for persistence
- Provide a Dockerfile to run locally and in AWS ECS
- Push Docker images to AWS ECR
- All business-specific config and secrets must be handled via environment variables or passed in at deploy time via CloudFormation
- Project should be deployable via a CloudFormation template (generate this too)

### Runtime Container Model:
- The entire app (Django backend, asset pipeline, migrations, healthcheck) must run in a **single Docker container**.
- Connect to an external AWS RDS Postgres instance via environment variables.
- Local development must use the same container structure and connect to RDS or allow local override via a documented environment variable toggle (e.g. `USE_LOCAL_DB=1`).

### Django app:
- One app called `core`
- Expose:
  - `/health`: renders an HTML page using Django templates, introspects the PostgreSQL schema, and displays all table names
  - `/metrics`: exposes basic runtime metrics like uptime, request counts, and memory usage
- Include a management command to apply migrations and print out a success message
- Use `settings.py` split into `base.py`, `local.py`, and `prod.py`, using env vars to select environment
- Add `logging.conf` file for structured logging to stdout
- Add basic HTTP auth middleware for `/metrics` and `/admin` views using an admin password provided via env var

### Docker:
- Single Dockerfile that installs Python dependencies, compiles frontend, and runs the Django app
- Use multi-stage build if needed

### Frontend:
- Use gulp + npm to compile Sass to CSS and manage static assets
- Provide example SCSS file and base HTML template with Bootstrap and jQuery
- Render basic Django views using server-side rendering

### AWS:
- CloudFormation template should define:
  - ECS Service (Fargate)
  - ECR repository
  - RDS (PostgreSQL)
  - DynamoDB table for WebSocket sessions
  - IAM roles for ECS tasks
  - VPC, subnets, security groups
  - Optional: CloudWatch log group and dashboards
  - CodePipeline and CodeBuild for CI/CD
- Include helper scripts for:
  - Building and pushing Docker image to ECR
  - Running CloudFormation stack deployment
  - CI/CD pipeline bootstrap

### WebSocket:
- Add WebSocket endpoint using Django Channels or ASGI with basic routing
- Store/retrieve connection metadata in DynamoDB

### Secrets:
- All secrets (e.g. DB credentials, admin password) should be pulled from AWS Parameter Store or Secrets Manager (stub with clear TODO comments if needed)

## Deliverables:
- Full project file tree
- All source code files
- `README.md` with instructions for:
  - Local development with Docker
  - Gulp asset pipeline
  - Initial AWS bootstrap via CloudFormation
  - Pushing images to ECR
  - CI/CD pipeline operation
-
## Forbidden Actions
- **Do not** create separate containers for each service.  Only create a single container
- **Do not use Redis.** Use Postgres for all persistence, including any internal key/value store functionality. If needed, define a `kv_store` table in Postgres.
- **Do not** include a docker-compose.yml unless needed for local override use
