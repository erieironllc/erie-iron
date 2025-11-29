import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from erieiron_autonomous_agent.system_agent_llm_interface import llm_chat
from erieiron_common.enums import LlmModel, LlmReasoningEffort, LlmVerbosity, LlmCreativity
from erieiron_common.llm_apis.llm_interface import LlmMessage
from erieiron_common.llm_apis.llm_response import LlmResponse


def llm_chat_triple_check(
        description: str,
        messages: list[LlmMessage],
        tag_entity,
        output_schema=None,
        reasoning_effort: LlmReasoningEffort = LlmReasoningEffort.LOW,
        verbosity: LlmVerbosity = LlmVerbosity.LOW,
        creativity: LlmCreativity = LlmCreativity.NONE,
        code_response=False
) -> LlmResponse:
    """
    Get consensus best answer by:
    1. Getting responses from three models (GPT-5.1, Claude 4.5, Gemini 3.0 Pro) in parallel
    2. Having each model rank the other two responses in parallel
    3. Selecting winner based on rankings with tie-breaking: Claude > OpenAI > Gemini
    """
    
    # Define the three models to use
    models = [
        LlmModel.OPENAI_GPT_5_1,
        LlmModel.CLAUDE_4_5,
        LlmModel.GEMINI_3_0_PRO
    ]
    
    # Helper function to get response from a single model
    def _get_response(model: LlmModel) -> tuple[LlmModel, LlmResponse]:
        resp = llm_chat(
            description=f"{description} [via {model.value}]",
            messages=messages,
            tag_entity=tag_entity,
            model=model,
            output_schema=output_schema,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            creativity=creativity,
            code_response=code_response
        )
        return model, resp
    
    # Step 1: Get initial responses in parallel
    logging.info(f"Triple check starting: getting responses from {len(models)} models in parallel")
    responses = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_get_response, model): model for model in models}
        for future in as_completed(futures):
            model, response = future.result()
            responses[model] = response
            logging.info(f"Triple check: received response from {model.value}")
    
    if len(responses) != 3:
        raise Exception(f"Expected 3 responses but got {len(responses)}")
    
    # Helper function to create ranking prompt
    def _create_ranking_prompt(
            response_a_model: LlmModel,
            response_a: LlmResponse,
            response_b_model: LlmModel,
            response_b: LlmResponse
    ) -> list[LlmMessage]:
        ranking_prompt = f'''You are being asked to rank two LLM responses to determine which better answers the original question.

ORIGINAL QUESTION:
{messages[-1].text if messages else "N/A"}

RESPONSE A (from {response_a_model.value}):
{response_a.text}

RESPONSE B (from {response_b_model.value}):
{response_b.text}

Rank these responses from worst to best. Consider:
- Accuracy and correctness
- Completeness of answer
- Clarity and coherence
- Adherence to any output format requirements

Respond with a JSON object:
{{
    "worst": "A" or "B",
    "best": "A" or "B",
    "reasoning": "brief explanation"
}}
'''
        return [LlmMessage.user(ranking_prompt)]
    
    # Helper function to get ranking from one model
    def _get_ranking(
            ranker_model: LlmModel,
            response_a_model: LlmModel,
            response_a: LlmResponse,
            response_b_model: LlmModel,
            response_b: LlmResponse
    ) -> dict:
        ranking_messages = _create_ranking_prompt(
            response_a_model, response_a, response_b_model, response_b
        )
        
        ranking_resp = llm_chat(
            description=f"Ranking responses via {ranker_model.value}",
            messages=ranking_messages,
            tag_entity=tag_entity,
            model=ranker_model,
            reasoning_effort=LlmReasoningEffort.LOW,
            verbosity=LlmVerbosity.LOW,
            creativity=LlmCreativity.NONE,
            code_response=True
        )
        
        return {
            'ranker': ranker_model,
            'response_a_model': response_a_model,
            'response_b_model': response_b_model,
            'ranking': ranking_resp.json()
        }
    
    # Step 2: Execute rankings in parallel (each model ranks the other two)
    ranking_tasks = [
        # GPT ranks Claude vs Gemini
        (LlmModel.OPENAI_GPT_5_1, LlmModel.CLAUDE_4_5, LlmModel.GEMINI_3_0_PRO),
        # Claude ranks GPT vs Gemini
        (LlmModel.CLAUDE_4_5, LlmModel.OPENAI_GPT_5_1, LlmModel.GEMINI_3_0_PRO),
        # Gemini ranks GPT vs Claude
        (LlmModel.GEMINI_3_0_PRO, LlmModel.OPENAI_GPT_5_1, LlmModel.CLAUDE_4_5)
    ]
    
    logging.info(f"Triple check: starting {len(ranking_tasks)} ranking calls in parallel")
    rankings = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for ranker, resp_a_model, resp_b_model in ranking_tasks:
            future = executor.submit(
                _get_ranking,
                ranker,
                resp_a_model, responses[resp_a_model],
                resp_b_model, responses[resp_b_model]
            )
            futures.append(future)
        
        for future in as_completed(futures):
            ranking_result = future.result()
            rankings.append(ranking_result)
            logging.info(f"Triple check: received ranking from {ranking_result['ranker'].value}")
    
    # Helper function to calculate scores from rankings
    def _calculate_scores(rankings: list[dict]) -> dict[LlmModel, int]:
        scores = {model: 0 for model in models}
        
        for ranking_result in rankings:
            resp_a_model = ranking_result['response_a_model']
            resp_b_model = ranking_result['response_b_model']
            ranking = ranking_result['ranking']
            
            best = ranking.get('best', '').upper()
            
            if best == 'A':
                scores[resp_a_model] += 1
            elif best == 'B':
                scores[resp_b_model] += 1
            # If unclear or tie in ranking, no points awarded
        
        return scores
    
    # Helper function to select winner with tie-breaking
    def _select_winner(scores: dict[LlmModel, int]) -> LlmModel:
        max_score = max(scores.values())
        winners = [model for model, score in scores.items() if score == max_score]
        
        if len(winners) == 1:
            return winners[0]
        
        # Tie-breaking order: Claude > OpenAI > Gemini
        tie_break_order = [
            LlmModel.CLAUDE_4_5,
            LlmModel.OPENAI_GPT_5_1,
            LlmModel.GEMINI_3_0_PRO
        ]
        
        for preferred_model in tie_break_order:
            if preferred_model in winners:
                return preferred_model
        
        # Should never reach here, but fallback to first winner
        return winners[0]
    
    # Step 3: Calculate scores and select winner
    scores = _calculate_scores(rankings)
    winner_model = _select_winner(scores)
    
    logging.info(f"Triple check consensus winner: {winner_model.value}")
    logging.info(f"Triple check scores: {[(m.value, s) for m, s in scores.items()]}")
    
    return responses[winner_model]
