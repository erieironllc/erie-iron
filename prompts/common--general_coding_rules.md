## Django Migrations Policy
All database schema changes are managed through the Django ORM. Make changes by editing models.py only.

- Never create, edit, rename, or delete Django migration files (paths matching */migrations/*.py).
- If a schema change is required, modify the Django model classes only. Do not hand-write migration operations.
- Do not include migration files in the code_files list. Only include edits to the model file or files where the schema is defined.
- The agent orchestration layer will run "python manage.py makemigrations" and "python manage.py migrate" to generate and apply migration files. That is out of scope for this agent.
- If evaluator diagnostics mention missing or stale migrations, resolve by planning the necessary model changes and proceed. Do not attempt to fix issues by editing migration files.
- Any plan that proposes direct migration file changes must STOP and emit "blocked" with category "task_def" and a reason that cites this policy.

---

## File and Module Naming
- All python code files and tests **must** be written to dist as a child of the top level directory named "./core".  You may create sub-directories in "./core" for code organization
- All python test files **must** live in the directory "./core/tests".  **Do not** put them anywhere else
- All files and modules must be named in a professional manner that describes their purpose.
    - This is an example of bad name:  "your_lambda_function"
    - This is an example of a good name:  "email_ingestion_lambda"
    - Do not use names that duplicate the purpose of an existing file; see 'Previously Learned Lessons' for duplicate file avoidance rules.

### File Name Extensions
File extensions for code **must** follow these conventions:
- Python: `.py`
- HTML: `.html`
- JavaScript: `.js`
- CSS: `.css`
- SQL: `.sql`

---

## Lambda Code and Deployment Strategy

When planning Lambda-based functionality, follow these rules:
- All Lambda source files must be written to standalone `.py` files in the `./lambda/` directory. One function per file.
- You are responsible for planning the **Lambda application code only** — including its file path, handler implementation, and required PyPI dependencies.
- The deploy manager agent will handle packaging and uploading the Lambda to S3. **Do not** plan any packaging or deployment logic.
- When planning infrastructure edits related to Lambda deployment:
  - Do **not** embed code using `ZipFile`, `InlineCode`, or `CodeUri` in the CloudFormation templates. Lambdas live in `infrastructure-application.yaml`.
  - Each Lambda must be defined using the `Code.S3Bucket` and `Code.S3Key` fields.
  - The `S3Bucket` must be hardcoded (`erieiron-lambda-packages`).
  - The `S3Key` must be passed in via a CloudFormation `Parameter`.  **never** hardcode S3 keys.
- Each Lambda resource in `infrastructure-application.yaml` **must** include a `Metadata` section with the field `SourceFile` set to the full relative path of the Lambda `.py` file (e.g., `lambda/my_handler.py`). 
    - This is used during deployment to associate Lambda resources with their source files.
- For every Lambda, you must emit a `dependencies` list that includes **only** the PyPI packages required by that file.
- Treat Lambda deployment as a deployment concern, not an application concern.

----

## Read Only Files
The following files are read-only.  You **must never** modify any of these files under any circumstances.  
<read_only_files>

----

## File Type Constraints

You may only plan code changes for file types that have an existing codewriter.  
If the required file type does not match one of the codewriters listed below, you **must** return a `"blocked"` response with an explanation.

### Supported File → Codewriter Mapping
- `requirements.txt`, `constraints.txt` → `requirements.txt code writer`
- `test*.py` → `python tests code writer`
- `settings.py` → `django settings code writer`
- `*.py` → `python code writer`
- `*.json` → `json code writer`
- `*.eml` → `eml code writer`
- `*.md` → `documentation writer`
- `infrastructure.yaml` → `aws cloudformation code writer`
- `infrastructure-application.yaml` → `aws cloudformation code writer`
- `Dockerfile*` → `dockerfile code writer`
- `*.sql` → `sql code writer`
- `*.js` → `javascript code writer`
- `*.html` → `html code writer`
- `*.yaml` → `yaml code writer`
- `*.css` → `css code writer`
- `*.txt` → `txt code writer`
- `*.ini` → `ini code writer`
- When editing `.md` files that include URLs, prefer dynamic placeholders (e.g., `{DOMAIN_NAME}`). If tests scan for a literal, include a separate "Example" line that resolves the placeholder using DOMAIN_NAME from evaluator logs.

### Forbidden Files
- Any `.env*` file is forbidden. All environment variables must come from the OS environment.
- Any Django migration files (e.g., */migrations/*.py) are forbidden. Do not create, delete, or modify migration files. Schema changes must be made by editing Django models only; the orchestration layer will generate and apply migrations.
- Any file not listed above is unsupported; you must return `"blocked"`.

Always use the correct file name for each file in your planning output - ensure the name will match to the appropriate code writer

---

## Billing Safety
- **You must** Avoid code patterns that may cause unbounded cloud resource usage, especially with AWS services.
- **Never** design or deploy Lambdas that can recursively trigger themselves directly or indirectly.
- Guard against unbounded loops, runaway retries, or unbounded concurrency when invoking external services.
- Include runtime safeguards (e.g., counters, rate limits, timeout handling) to prevent uncontrolled execution.
