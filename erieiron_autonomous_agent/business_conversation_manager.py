import json
import logging
import re
from pathlib import Path
from typing import Optional

from django.utils import timezone

from erieiron_autonomous_agent import system_agent_llm_interface
from erieiron_autonomous_agent.models import (
    BusinessConversation,
    ConversationMessage,
    ConversationChange,
    LlmRequest,
    Business
)
from erieiron_common.enums import LlmModel, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage

logger = logging.getLogger(__name__)


class BusinessConversationManager:
    """Manages conversations about businesses with LLM-powered responses"""
    
    # noinspection RegExpRedundantEscape
    CHANGE_PROPOSAL_PATTERN = re.compile(
        r'\[PROPOSE_CHANGE\](.*?)\[/PROPOSE_CHANGE\]',
        re.DOTALL
    )
    
    def __init__(self, conversation: BusinessConversation):
        self.conversation = conversation
    
    @classmethod
    def create_conversation(cls, business: Business, initiative: Optional = None,
                            title: str = "New Conversation") -> BusinessConversation:
        """Create a new conversation for a business"""
        conversation = BusinessConversation.objects.create(
            business=business,
            initiative=initiative,
            title=title,
            status='active'
        )
        logger.info(f"Created conversation {conversation.id} for business {business.name}")
        return conversation
    
    def add_user_message(self, content: str) -> ConversationMessage:
        """Add a user message to the conversation"""
        msg = ConversationMessage.objects.create(
            conversation=self.conversation,
            role='user',
            content=content
        )
        logger.debug(f"Added user message to conversation {self.conversation.id}")
        return msg
    
    def get_conversation_history(self) -> list[LlmMessage]:
        """Convert conversation messages to LlmMessage format for API calls"""
        messages = []
        for msg in self.conversation.messages.all():
            if msg.role == 'user':
                messages.append(LlmMessage.user(msg.content))
            elif msg.role == 'assistant':
                messages.append(LlmMessage.assistant(msg.content))
        return messages
    
    def _format_business_context(self, context: dict) -> str:
        """Format business context dict into readable text for LLM"""
        lines = ["## Business Information\n"]
        
        # Core business details
        lines.append(f"**Name**: {context.get('name', 'N/A')}")
        lines.append(f"**Summary**: {context.get('summary', 'N/A')}\n")
        
        if context.get('business_plan'):
            lines.append("### Business Plan")
            lines.append(context['business_plan'])
            lines.append("")
        
        if context.get('architecture'):
            lines.append("### Architecture")
            lines.append(context['architecture'])
            lines.append("")
        
        if context.get('value_prop'):
            lines.append(f"**Value Proposition**: {context['value_prop']}\n")
        
        if context.get('revenue_model'):
            lines.append(f"**Revenue Model**: {context['revenue_model']}\n")
        
        if context.get('audience'):
            lines.append(f"**Target Audience**: {context['audience']}\n")
        
        # Core functions
        if context.get('core_functions'):
            lines.append("### Core Functions")
            for func in context['core_functions']:
                lines.append(f"- {func}")
            lines.append("")
        
        # Active tasks
        if context.get('active_tasks'):
            lines.append("### Current Active Tasks")
            for task in context['active_tasks']:
                lines.append(f"- **{task['name']}** ({task['status']}): {task.get('description', '')}")
            lines.append("")
        
        # Infrastructure
        if context.get('infrastructure_stacks'):
            lines.append("### Infrastructure Stacks")
            for stack in context['infrastructure_stacks']:
                lines.append(f"- {stack['name']} ({stack['type']}, {stack['environment']}): {stack['status']}")
            lines.append("")
        
        # Initiative scope
        if context.get('initiative'):
            init = context['initiative']
            lines.append("### Initiative Scope")
            lines.append(f"This conversation is focused on initiative: **{init['name']}**")
            lines.append(f"Description: {init.get('description', 'N/A')}")
            lines.append(f"Status: {init['status']}\n")
        
        return "\n".join(lines)
    
    def parse_change_proposals(self, assistant_message_content: str,
                               assistant_message: ConversationMessage) -> list[ConversationChange]:
        """Extract and create ConversationChange objects from assistant response"""
        changes = []
        
        matches = self.CHANGE_PROPOSAL_PATTERN.findall(assistant_message_content)
        
        for match in matches:
            try:
                change_data = json.loads(match.strip())
                
                change = ConversationChange.objects.create(
                    conversation=self.conversation,
                    message=assistant_message,
                    change_type=change_data['change_type'],
                    change_description=change_data['change_description'],
                    change_details=change_data['change_details'],
                    approved=False,
                    applied=False
                )
                changes.append(change)
                logger.info(f"Created change proposal {change.id} for conversation {self.conversation.id}")
            
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse change proposal JSON: {e}")
            except KeyError as e:
                logger.error(f"Missing required field in change proposal: {e}")
        
        return changes
    
    def suggest_conversation_title(self) -> Optional[str]:
        """Suggest a new conversation title based on messages"""
        messages = self.conversation.messages.all()

        if messages.count() < 2:
            return None

        # Build conversation summary
        conversation_text = []
        for msg in messages[:10]:  # Only use first 10 messages to keep context manageable
            conversation_text.append(f"{msg.role.upper()}: {msg.content[:200]}")

        prompt = f"""Review this business conversation and suggest a concise, descriptive title (3-6 words).

Current title: "{self.conversation.title}"

Conversation:
{chr(10).join(conversation_text)}

Only suggest a new title if the conversation topic has changed significantly from the current title.
If the current title is still appropriate, respond with "KEEP_CURRENT".
Otherwise, respond with just the new title (no quotes, no explanation).

New title:"""

        response = system_agent_llm_interface.llm_chat(
            tag_entity=self.conversation.business,
            description=f"Conversation title suggestion for {self.conversation.business.name}",
            messages=[LlmMessage.user(prompt)],
            model=LlmModel.OPENAI_GPT_5_MINI,
            verbosity=LlmVerbosity.LOW
        )

        suggested_title = response.text.strip()

        if suggested_title == "KEEP_CURRENT" or not suggested_title:
            return None

        # Remove quotes if present
        suggested_title = suggested_title.strip('"\'')

        # Limit length
        if len(suggested_title) > 100:
            suggested_title = suggested_title[:97] + "..."

        logger.info(f"Suggested title for conversation {self.conversation.id}: {suggested_title}")
        return suggested_title

    def generate_response(self, model: Optional[LlmModel] = LlmModel.OPENAI_GPT_5_1) -> tuple[ConversationMessage, list[ConversationChange]]:
        """Generate assistant response using LLM"""

        # Call LLM using system_agent_llm_interface
        logger.info(f"Generating response for conversation {self.conversation.id} using {model}")
        response = system_agent_llm_interface.llm_chat(
            tag_entity=self.conversation.business,
            description=f"Business conversation for {self.conversation.business.name}",
            messages=[
                LlmMessage.sys((system_agent_llm_interface.BASE_PROMPTS_PATH / "business_conversation_assistant.md").read_text()),
                self.conversation.get_context_snapshot(),
                self.get_conversation_history()
            ],
            model=model,
            verbosity=LlmVerbosity.MEDIUM
        )

        assistant_msg = ConversationMessage.objects.create(
            conversation=self.conversation,
            role='assistant',
            content=response.text,
            llm_request_id=response.llm_request_id
        )

        self.conversation.updated_at = timezone.now()
        self.conversation.save()

        changes = self.parse_change_proposals(response.text, assistant_msg)

        # Suggest and update conversation title if appropriate
        suggested_title = self.suggest_conversation_title()
        if suggested_title and suggested_title != self.conversation.title:
            logger.info(f"Updating conversation title from '{self.conversation.title}' to '{suggested_title}'")
            self.conversation.title = suggested_title
            self.conversation.save()

        logger.info(f"Generated response with {len(changes)} change proposals for conversation {self.conversation.id}")
        return assistant_msg, changes
    
    def get_full_conversation(self) -> list[dict]:
        """Return full conversation as list of dicts for API/UI consumption"""
        return [
            {
                'id': str(msg.id),
                'role': msg.role,
                'content': msg.content,
                'created_at': msg.created_at.isoformat()
            }
            for msg in self.conversation.messages.all()
        ]
