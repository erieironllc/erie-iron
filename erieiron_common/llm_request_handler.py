import logging

from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_common import common, client_message_publisher
from erieiron_common.enums import ClientMessage, LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity, LlmMessageType
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.models import Person


def handle_llm_request(payload: dict, message=None):
    """
    Generic handler for async LLM requests from views.

    Expected payload structure:
    {
        "person_id": str (UUID),
        "description": str,
        "messages": list[dict] with keys: message_type, text, (optional) file,
        "model": str (LlmModel value),
        "tag_entity_type": str (e.g., "Business", "Initiative", "Task"),
        "tag_entity_id": str (UUID),
        "output_schema": dict (optional),
        "reasoning_effort": str (optional, default: "LOW"),
        "verbosity": str (optional, default: "LOW"),
        "creativity": str (optional, default: "NONE"),
        "code_response": bool (optional, default: False),
        "completion_view_url": str (optional - URL to POST results to),
        "completion_view_data": dict (optional - additional data to pass to completion view)
    }
    """
    try:
        # Extract person
        person_id = payload.get("person_id")
        if not person_id:
            raise ValueError("person_id is required in LLM request payload")

        person = Person.objects.get(id=person_id)

        # Extract LLM parameters
        description = payload.get("description", "Async LLM request")

        # Reconstruct LlmMessage objects from payload
        messages_data = payload.get("messages", [])
        messages = []
        for msg_data in messages_data:
            msg_type = LlmMessageType(msg_data.get("message_type", "user"))
            text = msg_data.get("text", "")
            # Note: file handling would require additional serialization/deserialization
            messages.append(LlmMessage(msg_type, text))

        # Get tag entity
        tag_entity = _get_tag_entity(
            payload.get("tag_entity_type"),
            payload.get("tag_entity_id")
        )

        # Get model and optional parameters
        model = LlmModel(payload.get("model", LlmModel.OPENAI_GPT_5_MINI.value))
        output_schema = payload.get("output_schema")
        reasoning_effort = LlmReasoningEffort(payload.get("reasoning_effort", LlmReasoningEffort.LOW.value))
        verbosity = LlmVerbosity(payload.get("verbosity", LlmVerbosity.LOW.value))
        creativity = LlmCreativity(payload.get("creativity", LlmCreativity.NONE.value))
        code_response = payload.get("code_response", False)

        # Execute LLM call
        common.log_info(f"Executing async LLM request: {description}")
        resp = system_agent_llm_interface.llm_chat(
            description=description,
            messages=messages,
            tag_entity=tag_entity,
            model=model,
            output_schema=output_schema,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            creativity=creativity,
            code_response=code_response
        )

        # Prepare response payload
        response_payload = {
            "llm_request_id": str(resp.llm_request_id),
            "text": resp.text,
            "price_total": resp.price_total,
            "chat_millis": resp.chat_millis,
            "model": resp.model.value if hasattr(resp, 'model') and resp.model else model.value,
            "original_description": description
        }

        # If output schema was provided, include parsed JSON
        if output_schema:
            try:
                response_payload["json"] = resp.json()
            except Exception as e:
                logging.exception(e)
                response_payload["json_parse_error"] = str(e)

        # If completion view URL is provided, POST to it (Option B)
        completion_view_url = payload.get("completion_view_url")
        if completion_view_url:
            _call_completion_view(
                completion_view_url,
                response_payload,
                payload.get("completion_view_data", {})
            )

        # Send WebSocket message to frontend
        client_message_publisher.publish(
            person=person,
            message_type=ClientMessage.LLM_RESPONSE_READY,
            payload=response_payload
        )

        common.log_info(f"Completed async LLM request: {description} (LlmRequest: {resp.llm_request_id})")

    except Exception as e:
        logging.exception(e)

        # Try to notify frontend of error
        try:
            if person_id:
                person = Person.objects.get(id=person_id)
                client_message_publisher.publish(
                    person=person,
                    message_type=ClientMessage.LLM_RESPONSE_READY,
                    payload={
                        "error": str(e),
                        "original_description": payload.get("description", "Unknown")
                    }
                )
        except Exception as e2:
            logging.exception(e2)


def _get_tag_entity(entity_type: str, entity_id: str):
    """
    Get the tag entity object from type and ID.
    """
    if not entity_type or not entity_id:
        # Default to Erie Iron business if no tag entity specified
        from erieiron_autonomous_agent.models import Business
        return Business.get_erie_iron_business()

    # Import models as needed
    if entity_type == "Business":
        from erieiron_autonomous_agent.models import Business
        return Business.objects.get(id=entity_id)
    elif entity_type == "Initiative":
        from erieiron_autonomous_agent.models import Initiative
        return Initiative.objects.get(id=entity_id)
    elif entity_type == "Task":
        from erieiron_autonomous_agent.models import Task
        return Task.objects.get(id=entity_id)
    elif entity_type == "SelfDrivingTaskIteration":
        from erieiron_autonomous_agent.models import SelfDrivingTaskIteration
        return SelfDrivingTaskIteration.objects.get(id=entity_id)
    else:
        raise ValueError(f"Unsupported tag_entity_type: {entity_type}")


def _call_completion_view(completion_view_url: str, llm_response: dict, additional_data: dict):
    """
    POST to the completion view with LLM results (Option B pattern).

    This allows view-specific business logic to execute after LLM completes,
    without blocking the original request thread.
    """
    import requests
    from django.middleware import csrf

    try:
        # Merge LLM response with any additional data
        post_data = {
            **additional_data,
            "llm_response": llm_response
        }

        # Make internal POST request to completion view
        # Note: This assumes completion_view_url is an internal endpoint
        common.log_debug(f"Calling completion view: {completion_view_url}")

        # For internal calls, we may need to use Django's test client or direct view invocation
        # For now, using requests as a simple implementation
        # In production, consider using Django's RequestFactory for internal calls

        response = requests.post(
            completion_view_url,
            json=post_data,
            timeout=30
        )

        if response.status_code >= 400:
            logging.warning(
                f"Completion view returned error: {response.status_code} - {response.text}"
            )
        else:
            common.log_debug(f"Completion view succeeded: {completion_view_url}")

    except Exception as e:
        logging.exception(f"Failed to call completion view {completion_view_url}: {e}")
