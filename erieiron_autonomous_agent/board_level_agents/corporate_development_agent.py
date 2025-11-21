import json
import textwrap
import uuid
from collections import defaultdict
from pathlib import Path

from tqdm import tqdm

from erieiron_autonomous_agent.enums import BusinessStatus
from erieiron_autonomous_agent.models import Business
from erieiron_autonomous_agent.system_agent_llm_interface import board_level_chat
from erieiron_common import common
from erieiron_common.enums import Constants, BusinessIdeaSource, PubSubMessageType, BusinessNiche, LlmModel, LlmReasoningEffort, LlmVerbosity
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.message_queue.pubsub_manager import PubSubManager


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
    max_human_hours_per_week = payload.get('max_human_hours_per_week', 3)
    
    # Generate multiple business ideas for the niche
    for i in range(requested_count):
        # Create placeholder business for idea generation
        placeholder_business = Business.objects.create(
            name=f"{Constants.NEW_BUSINESS_NAME_PREFIX} {uuid.uuid4()}...",
            source=BusinessIdeaSource.BUSINESS_FINDER_AGENT,
            niche_category=niche
        )
        
        business_idea = find_new_business_opportunity(
            {
                "placehold_business_id": placeholder_business.id,
                "niche_type": niche,
                "user_guidance": user_input,
                "max_human_hours_per_week": max_human_hours_per_week
            },
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


def find_new_business_opportunity(payload):
    erieiron_business = Business.get_erie_iron_business()
    
    niche_type = BusinessNiche.valid_or(payload.get("niche_type"))
    placehold_business_id = payload.get("placehold_business_id")
    user_guidance = payload.get("user_guidance")
    max_human_hours_per_week = payload.get("max_human_hours_per_week", 3)
    
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
    
    capacity_message = textwrap.dedent(f"""
        Please find a new business idea that roughly fits this capacity.  
        It's ok to pitch a stretch idea - if we decide we can't take it on, 
        we'll remember it for the future, but if you have a really great idea that
        fits within the capacity, bias towards that.

        ## Erie Iron's current financial position minus reserves
        {json.dumps(erieiron_business.get_new_business_budget_capacity(), indent=4)}

        """)
    
    if max_human_hours_per_week > 0:
        capacity_message += textwrap.dedent(f"""
        ## Available Human hours per week:  {max_human_hours_per_week}hrs/week
        """)
    else:
        capacity_message += textwrap.dedent(f"""
        ## Must be able to run almost fully autonomously
        """)
    
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


def on_portfolio_pick_new_business(payload):
    """
    Evaluate all business ideas and select the top 3 best ones
    based on provided guidance and Erie Iron constraints.

    Returns:
        dict: Structured response with top 3 ranked businesses and analysis
    """
    # Get all idea-stage businesses
    guidance = payload.get("guidance")
    top_business_count = payload.get("top_business_count", 4)
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
        Select the {top_business_count} best businesses 
        
        ## GUIDANCE FOR BUSINESS RANKING
        {guidance or 'Pick winners'}
        """)
    ]
    
    business_choices = defaultdict(list)
    business_datas = defaultdict(list)
    
    for model in [LlmModel.CLAUDE_4_5, LlmModel.OPENAI_GPT_5_1, LlmModel.GEMINI_3_0_PRO]:
        response = board_level_chat(
            "Board Chair Business Picker",
            "board_chair--business_picker.md",
            user_messages,
            reasoning_effort=LlmReasoningEffort.HIGH,
            verbosity=LlmVerbosity.LOW,
            model=model
        )
        
        for business_data in response['top_ranked_businesses']:
            business_id = business_data["business_id"]
            confidence_score = business_data["confidence_score"]
            business_datas[business_id].append(business_data)
            business_choices[business_id].append(confidence_score)
            print(model, business_id, confidence_score)
    
    business_choices = sorted([
        (sum(scores), business_id)
        for business_id, scores in business_choices.items()
    ], key=lambda t: t[0], reverse=True)
    
    selected_business_datas = []
    for score, business_id in business_choices:
        business = Business.objects.get(id=business_id)
        selected_business_datas.append({
            **business.get_llm_data(),
            "overall_score": score,
            "business_picker_reasonings": business_datas[business_id]
        })
        print(business_id, score)
    
    for model in [LlmModel.CLAUDE_4_5, LlmModel.OPENAI_GPT_5_1, LlmModel.GEMINI_3_0_PRO]:
        response = board_level_chat(
            "Business Picker Report Maker",
            "report_maker.md",
            [
                LlmMessage.user_from_data("Business Picker Data", selected_business_datas),
                f"""
                Title:  Business Picker Output ({model})
                
                Guidance: {guidance}
                
                Goal: Create a report to help a human select the best business to move forward with.  Include overall_score value in report
                """
            ],
            text_output=True,
            reasoning_effort=LlmReasoningEffort.MEDIUM,
            verbosity=LlmVerbosity.HIGH,
            model=model
        )
    
        file = common.write_to_tempfile(response)
        print("file", Path(file.name).absolute())
    
    return response


def get_filtered_idea_businesses(guidance) -> list[Business]:
    return Business.objects.filter(id__in=[
        'ca8083ce-39ff-4307-8bdc-ec0ae8c2032b',
        'd49942b6-4d85-416e-a165-f410c7b0385f',
        'bb07f861-fff4-4696-a4b9-d6d74407d5c7',
        'd971367c-c471-4567-bc46-9dcd110289b8',
        '52a52d63-d699-4fbf-988f-6315c77c6404',
        'acab75fe-b665-41c0-8e89-72994c2a5779',
        '866198e6-62c9-4219-8cfc-88a4cafd0d54',
        '4bba3cbb-3457-4686-9cbe-9f022d5de892',
        '1f3dfc3f-59c7-4318-aa14-b28fc2546458',
        'a71b58b5-9261-49c5-a382-5a1e771ece58',
        'a3fa0a2f-f31d-4e69-bf4f-7c0bbb14fab2',
        '44009135-8b71-4bb7-a829-8f29f826b432',
        '3be0c2a5-e179-4800-af2c-0feb5c99fff5'
    ]
    )
    
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
