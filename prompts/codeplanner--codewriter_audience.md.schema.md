{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Code Change Instruction Generator Output",
  "type": "object",
  "required": [
    "implementation_directive",
    "required_rule_contexts",
    "diagnostic_context",
    "relevant_lessons"
  ],
  "additionalProperties": false,

  "properties": {
    "implementation_directive": {
      "type": "object",
      "required": [
        "objective",
        "high_level_approach",
        "key_constraints",
        "success_criteria"
      ],
      "additionalProperties": false,
      "properties": {
        "objective": { "type": "string" },
        "high_level_approach": { "type": "string" },
        "key_constraints": {
          "type": "array",
          "items": { "type": "string" }
        },
        "success_criteria": { "type": "string" }
      }
    },

    "required_rule_contexts": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": [
          "infrastructure_rules",
          "lambda_rules",
          "python_rules",
          "javascript_rules",
          "sql_rules",
          "django_rules",
          "test_rules",
          "ui_rules",
          "security_rules",
          "database_rules",
          "ses_email_rules",
          "s3_storage_rules",
          "sqs_queue_rules",
          "cognito_rules"
        ]
      }
    },

    "required_credentials": {
      "type": "object",
      "description": "Mapping of service names to credential specification objects required for this code change.",
      "additionalProperties": {
        "type": "object",
        "required": ["secret_arn_env_var", "schema"],
        "additionalProperties": false,
        "properties": {
          "secret_arn_env_var": {
            "type": "string",
            "description": "Name of the environment variable containing the AWS Secrets Manager secret ARN at runtime.",
            "pattern": "^[A-Z][A-Z0-9_]*$"
          },
          "secret_arn_cfn_parameter": {
            "type": "string",
            "description": "Optional CloudFormation/OpenTofu parameter name for the secret ARN."
          },
          "schema": {
            "type": "array",
            "description": "Array describing each key/value pair expected inside the secret.",
            "items": {
              "type": "object",
              "required": ["key", "type", "required", "description"],
              "additionalProperties": false,
              "properties": {
                "key": { "type": "string", "description": "Key name inside the secret." },
                "type": { "type": "string", "description": "Data type (string, number, boolean, object)." },
                "required": { "type": "boolean", "description": "Whether this key is required." },
                "description": { "type": "string", "description": "Purpose of this credential field." }
              }
            }
          }
        }
      }
    },

    "diagnostic_context": {
      "type": "object",
      "required": [
        "primary_error",
        "error_location",
        "relevant_logs",
        "environment_state",
        "prior_attempts"
      ],
      "additionalProperties": false,
      "properties": {
        "primary_error": { "type": "string" },
        "error_location": { "type": "string" },
        "relevant_logs": { "type": "string" },

        "environment_state": {
          "type": "object",
          "additionalProperties": {
            "type": ["string", "number", "boolean", "null"]
          }
        },

        "prior_attempts": { "type": "string" }
      }
    },

    "relevant_lessons": {
      "type": "array",
      "items": { "type": "string" }
    }
  }
}