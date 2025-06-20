def llm_chat_text_response(task_id: str, messages: list[tuple[str, str]]) -> str:
    """Text Chat with a LLM
task_id:  the id of the task this chat is related to.  (used for logging and budget purposes)
messages: list of tuples of [message_type, message_text].  message_type is one of [system | assistant | user], message_text is the message text

returns: raw string text response from the llm"""
    raise NotImplementedError()


def llm_chat_json_response(task_id: str, messages: list[tuple[str, str]]) -> dict:
    """Chat with a LLM and get a JSON response
task_id:  the id of the task this chat is related to.  (used for logging and budget purposes)
messages: list of tuples of [message_type, message_text].  message_type is one of [system | assistant | user], message_text is the message text

returns: json response from the llm parsed and returned as dict"""
    raise NotImplementedError()
