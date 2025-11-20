import json
import textwrap
import uuid
from pathlib import Path

from tqdm import tqdm

from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business, BusinessSecondOpinionEvaluation
from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat, get_reasoning_model
from erieiron_common import common
from erieiron_common.enums import Constants, BusinessIdeaSource, PubSubMessageType, BusinessNiche, LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager


def find_new_business_opportunity(payload, niche_type=None, user_guidance=None):
    niche_type = BusinessNiche.valid_or(niche_type)
    
    placehold_business_id = payload.get("placehold_business_id")
    erieiron_business = Business.get_erie_iron_business()
    
    """
    use this:
    Failed Startup Dataset by Shivam Bansal

A repo with structured data (CSV, JSON) scraped from public postmortems. Could be a starting point for training or heuristic modeling.

Awesome Postmortems

GitHub list of both startup and engineering postmortems. Less indie-focused but still useful for failure patterns.


    """
    
    messages = LlmMessage.user_from_data(
        "Existing Erie Iron Businesses",
        [
            {
                "name": b.name,
                "summary": b.summary or b.raw_idea or "not yet defined"
            }
            for b in Business.objects.exclude(id=erieiron_business.id)
        ],
        "existing_business"
    )
    
    capacity_message = f"""
        Please find a new business idea that roughly fits this capacity.  
        It's ok to pitch a stretch idea - if we decide we can't take it on, 
        we'll remember it for the future, but if you have a really great idea that
        fits within the capacity, bias towards that.

        ## Erie Iron's current financial position minus reserves
        {json.dumps(erieiron_business.get_new_business_budget_capacity(), indent=4)}

        ## Erie Iron Capacity summary
        {common.model_to_dict_s(erieiron_business.get_latest_capacity())}
        """
    
    if niche_type:
        capacity_message += f"\n\nNICHE FOCUS: {niche_type}"
    
    # Add user guidance if provided
    if user_guidance:
        capacity_message += f"\n\nUSER GUIDANCE: {user_guidance}"
    
    messages.append(capacity_message)
    
    # Use base business finder prompt (niche prompts will be added in future phase)
    business_idea = board_level_chat(
        "Business Finder",
        [
            "corporate_development--business_finder.md",
            niche_type.get_prompt_filename() if niche_type else None
        ],
        messages,
        business=Business.objects.get(id=placehold_business_id),
        model=LlmModel.CLAUDE_4_5
    )
    
    Business.objects.filter(id=placehold_business_id).update(
        name=business_idea.get("name")
    )
    
    return {
        "existing_business_id": placehold_business_id,
        "summary": business_idea.get("summary"),
        "idea_content": business_idea.get("detailed_pitch"),
        "full_output": business_idea,
        "source": BusinessIdeaSource.BUSINESS_FINDER_AGENT,
    }


def submit_business_opportunity(payload):
    existing_business_id = payload.get("existing_business_id")
    summary = payload.get("summary")
    idea_content = payload.get("idea_content")
    source = payload.get("source")
    niche_category = payload.get("niche_category")
    full_output = payload.get("full_output")
    
    if existing_business_id:
        name = Business.objects.get(id=existing_business_id).name
    else:
        if isinstance(idea_content, Path):
            name = idea_content.name.capitalize().replace("_", " ")
            idea_content = idea_content.read_text()
        else:
            name = f"{Constants.NEW_BUSINESS_NAME_PREFIX} {common.get_now()}"
    
    token = common.strip_non_alpha(name).lower()
    business, created = Business.objects.update_or_create(
        id=existing_business_id,
        defaults={
            "name": name,
            "service_token": token,
            "business_finder_output": full_output,
            "source": source,
            "raw_idea": idea_content,
            "niche_category": niche_category
        }
    )
    
    business_structure = board_level_chat(
        "Business Structurer",
        "corporate_development--business_structurer.md",
        f"""
            Existing business names: {[b.name for b in Business.objects.all()]}
            
            Please structure this business idea:
            
            {summary}
            
            {idea_content}
        """,
        model=LlmModel.OPENAI_GPT_5_MINI
    )
    
    if business.name.startswith(Constants.NEW_BUSINESS_NAME_PREFIX.value) and business_structure.get("business_name"):
        Business.objects.filter(id=business.id).update(
            name=business_structure.get("business_name")
        )
    
    Business.objects.filter(id=business.id).update(
        summary=business_structure.get("summary"),
        business_plan=business_structure.get("business_plan"),
        value_prop=business_structure.get("value_proposition"),
        revenue_model=business_structure.get("monetization"),
        audience=business_structure.get("audience"),
        core_functions=business_structure.get("core_functions", []),
        execution_dependencies=business_structure.get("execution_dependencies", []),
        growth_channels=business_structure.get("growth_channels", []),
        personalization_options=business_structure.get("personalization_options", [])
    )
    
    return business.id


def perform_second_opinion_evaluation(business_id):
    erieiron_business = Business.get_erie_iron_business()
    business = Business.objects.get(id=business_id)
    business_analysis, legal_analysis = business.get_latest_analysist()
    
    existing_eval = business.businesssecondopinionevaluation_set.all().order_by("-timestamp").first()
    if existing_eval:
        if LlmModel.CLAUDE_4_5.eq(existing_eval.llm_model):
            llm_model = LlmModel.OPENAI_GPT_5_1
        else:
            llm_model = LlmModel.CLAUDE_4_5
    else:
        llm_model = get_reasoning_model()
    
    evaluation = board_level_chat(
        "Business Second Opinion Evaluation",
        "business--second_opinion_evaluator.md",
        LlmMessage.user_from_data("Please evaluate this business", {
            "business_id": business.id,
            "niche_category": business.niche_category,
            "summary": business.summary,
            "revenue_model": business.revenue_model,
            "audience": business.audience,
            'business_analysis': common.get_dict(business_analysis),
            'legal_analysis': common.get_dict(legal_analysis)
        }),
        business=business,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmReasoningEffort.MEDIUM,
        model=llm_model
    )
    
    BusinessSecondOpinionEvaluation.objects.create(
        business=business,
        llm_model=llm_model,
        evaluation=evaluation
    )


def find_niche_business_ideas(payload):
    """
    Generate niche-specific business ideas based on user input.
    
    Expected payload:
    {
        "niche": "local_service_arbitrage", 
        "user_input": "user provided text",
        "requested_count": 10
    }
    """
    niche = payload.get('niche')
    user_input = payload.get('user_input', '')
    requested_count = payload.get('requested_count', 10)
    
    # Generate multiple business ideas for the niche
    for i in range(requested_count):
        # Create placeholder business for idea generation
        placeholder_business = Business.objects.create(
            name=f"{Constants.NEW_BUSINESS_NAME_PREFIX} {uuid.uuid4()}...",
            source=BusinessIdeaSource.BUSINESS_FINDER_AGENT,
            niche_category=niche
        )
        
        business_idea = find_new_business_opportunity(
            {"placehold_business_id": placeholder_business.id},
            niche_type=niche,
            user_guidance=user_input
        )
        
        # Publish each idea as a separate BUSINESS_IDEA_SUBMITTED message
        if business_idea:
            enhanced_payload = {
                **business_idea,
                'niche_category': niche,
                'user_input': user_input
            }
            
            PubSubManager.publish(
                PubSubMessageType.BUSINESS_IDEA_SUBMITTED,
                payload=enhanced_payload
            )


def on_portfolio_pick_new_business(payload):
    """
    Evaluate all business ideas and select the top 3 best ones
    based on provided guidance and Erie Iron constraints.

    Returns:
        dict: Structured response with top 3 ranked businesses and analysis
    """
    # Get all idea-stage businesses
    guidance = payload.get("guidance")
    filtered_idea_business = get_filtered_idea_businesses(guidance)
    
    print(textwrap.dedent(f"""
    
    Qualified Businesses:
    {[b.id for b in filtered_idea_business]}
    
    
    """))
    
    # Prepare comprehensive business context
    business_context = []
    for business in filtered_idea_business:
        business_analysis, legal_analysis = business.get_latest_analysist()
        business_context.append({
            "business_id": business.id,
            "niche_category": business.niche_category,
            "summary": business.summary,
            "revenue_model": business.revenue_model,
            "audience": business.audience,
            "critical_evaluations": common.get_dict(business.businesssecondopinionevaluation_set.all().order_by("-timestamp")),
            'business_analysis': common.get_dict(business_analysis),
            'legal_analysis': common.get_dict(legal_analysis)
        })
    
    user_messages = [
        LlmMessage.user_from_data("Business Ideas to Evaluate", business_context, "business"),
        textwrap.dedent(f"""
        ## GUIDANCE FOR BUSINESS SELECTION
        {guidance or 'Pick a winner'}
        """)
    ]
    
    response = board_level_chat(
        "Board Chair Business Picker",
        "board_chair--business_picker.md",
        user_messages,
        reasoning_effort=LlmReasoningEffort.HIGH,
        verbosity=LlmVerbosity.LOW,
        model=LlmModel.CLAUDE_4_5
    )
    
    return response


def get_filtered_idea_businesses(guidance) -> list[Business]:
    return Business.objects.filter(id__in=['4f7a81b5-2208-4266-a6b7-04bed943b006', '7074d6f5-2c88-4a17-9914-bcab92df988b', '135df621-3511-437a-a6dd-091c9aafa679', '9e2ba564-5c9c-4715-8b66-0c27d7cccf51', 'dd3468b2-e82f-4ef3-b3b5-1082962fdeeb', 'd1923488-bf2f-434e-8f46-7f361193ed99', 'ddc11ef6-322e-47ad-8868-61a8e09c1a41', '2bf1279c-6efe-401f-96d3-d027ee0099e6', 'b4732bc1-4d56-4eaa-bc29-c08805746b25', '06099fbc-d79a-41f8-ba37-37a59ef7a3c5', 'ec1b802d-3744-4c2d-8303-1734212a3c2c', 'bb660974-47b2-4c68-9b6f-dc576b7cdee2', '6f137e3d-0067-4dc5-b002-af0dc0b40f58', '370912dd-ddf4-459c-b0a7-0b2aac73d209', 'e8d011a5-7a4f-4519-ba4b-fbd6a0c58f77', 'bc427f49-5ee1-4b37-978e-5c306a2a34eb', 'dd65d4ad-07e9-4121-82ac-f4b10dc25474', '02ff14e3-9bba-4e5f-972e-0957d3b1a58d', '00193907-d889-4bc9-a237-47a8904f490e'])
    idea_businesses = Business.objects.filter(
        niche_category__isnull=False
    ).exclude(
        status=BusinessStatus.ACTIVE
    )
    
    filtered_idea_business = []
    for business in tqdm(idea_businesses):
        resp = board_level_chat(
            "Business Picker Filterer",
            "board_chair--business_picker_filterer.md",
            [
                LlmMessage.user_from_data("Business to eval", business.get_llm_data()),
                f"uses this guidance: {guidance}" if guidance else None,
            ],
            model=LlmModel.OPENAI_GPT_5_NANO
        )
        if resp.get("qualifies"):
            filtered_idea_business.append(business)
    
    return filtered_idea_business
