"""
Qwen Agent tool for nutritional meal recommendations.

Wraps nutri_rag's NutriAssistant to provide meal recommendations
based on what the user has eaten.

Requires:
    - nutri_rag LLM server running on localhost:8080
    - DuckDB knowledge base built (nutri_graph/data/nutri_kb.duckdb)
    - GAT embeddings trained (nutri_graph/outputs/embeddings/)
    - Text embeddings built (nutri_rag/data/embeddings/)
"""
import json
import os
import re
import sys

import json5
from qwen_agent.tools.base import BaseTool, register_tool

# Add nutri_rag to import path
_MIMIR_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_NUTRI_RAG_ROOT = os.path.join(_MIMIR_ROOT, 'nutri_rag')
if _NUTRI_RAG_ROOT not in sys.path:
    sys.path.insert(0, _NUTRI_RAG_ROOT)

from nutri_rag.assistant.pipeline import NutriAssistant  # noqa: E402

# Lazy singleton — initialized on first call to avoid slow startup
_assistant: NutriAssistant | None = None

# Foods check_food found, per place: {"kitchen": {"bread": fdc_id, ...}}. Kept for this session
# only and separate from the robot's landmark map (register_objects). A new check of a place
# replaces that place's foods, so eaten or moved foods drop out.
FOOD_LIST: dict[str, dict[str, int]] = {}


def _get_assistant() -> NutriAssistant:
    global _assistant
    if _assistant is None:
        print('[nutrition] Initializing NutriAssistant (loading embeddings + DB)...')
        _assistant = NutriAssistant()
        print('[nutrition] NutriAssistant ready.')
    return _assistant


@register_tool('get_meal_recommendation')
class GetMealRecommendation(BaseTool):
    description = (
        'Get a personalized meal recommendation based on what the user has eaten. '
        'Uses nutritional gap analysis to suggest what to eat next. '
        'Example: user ate "an apple and a cup of milk" for breakfast, '
        'this tool recommends what to have for lunch.'
    )
    parameters = [
        {
            'name': 'eaten_foods',
            'type': 'string',
            'description': (
                'Description of what the user has eaten, '
                'e.g. "an apple and a cup of milk", "rice with chicken and broccoli".'
            ),
            'required': True,
        },
        {
            'name': 'eaten_meal_type',
            'type': 'string',
            'description': 'Which meal the eaten food was for: "breakfast", "lunch", "dinner", or "snack". Default: "breakfast".',
            'required': False,
        },
        {
            'name': 'next_meal',
            'type': 'string',
            'description': 'Which meal to recommend for: "breakfast", "lunch", "dinner", or "snack". Default: "lunch".',
            'required': False,
        },
        {
            'name': 'disliked_ingredients',
            'type': 'array',
            'description': (
                'Ingredients the user said they dislike or are allergic to, '
                'e.g. ["peanuts", "shellfish", "cilantro"]. Extract from the user '
                'message — only include what they actually stated. Omit if none mentioned.'
            ),
            'required': False,
        },
        {
            'name': 'health_condition',
            'type': 'string',
            'description': (
                'Chronic condition stated by the user that should shape macro targets: '
                '"diabetes" | "hypertension" | "obesity". Omit if not stated.'
            ),
            'required': False,
        },
        {
            'name': 'available_foods',
            'type': 'string',
            'description': (
                'Comma-separated foods the user said they have right now, e.g. "eggs, bread, milk". '
                'The meal is then built only from these. Omit if the user did not list any.'
            ),
            'required': False,
        },
    ]

    def call(self, params: str, **kwargs) -> str:
        args = json5.loads(params)
        eaten_foods = args.get('eaten_foods', '')
        eaten_meal_type = args.get('eaten_meal_type', 'breakfast')
        next_meal = args.get('next_meal', 'lunch')
        disliked = args.get('disliked_ingredients') or []
        health_condition = args.get('health_condition') or None
        available_foods = args.get('available_foods') or ''
        if isinstance(available_foods, list):
            available_foods = ', '.join(available_foods)

        if not eaten_foods.strip():
            return json.dumps({'status': 'error', 'message': 'Please describe what you have eaten.'})

        print(
            f'[nutrition] eaten="{eaten_foods}", meal_type={eaten_meal_type}, '
            f'next={next_meal}, disliked={disliked}, condition={health_condition}, '
            f'available="{available_foods}"'
        )

        # Foods the user listed win; then the foods check_food found; otherwise the env-configured
        # source (none|json|zmq). Default "none" preserves the current behavior with no filter.
        from nutri_rag.assistant.availability import fdc_ids_for_labels, get_available_fdc_ids
        if available_foods.strip():
            available_fdc_ids = fdc_ids_for_labels(re.split(r',|\band\b', available_foods))
            if not available_fdc_ids:
                return json.dumps({'status': 'error', 'message': (
                    f'None of the available foods "{available_foods}" was found in the food database. '
                    'Ask the user to name them more specifically.')})
        elif any(FOOD_LIST.values()):
            print(f'[nutrition] foods found by check_food: {FOOD_LIST}')
            available_fdc_ids = {fid for foods in FOOD_LIST.values() for fid in foods.values()}
        else:
            available_fdc_ids = get_available_fdc_ids()
        if available_fdc_ids is not None:
            print(f'[nutrition] availability filter: {len(available_fdc_ids)} fdc_ids')

        try:
            assistant = _get_assistant()
            recommendation = assistant.recommend(
                user_message=f'What should I eat for {next_meal}?',
                eaten_description=eaten_foods,
                eaten_meal_type=eaten_meal_type,
                next_meal=next_meal,
                available_fdc_ids=available_fdc_ids,
                health_condition=health_condition,
                constraints={'disliked_ingredients': disliked} if disliked else None,
            )
            return json.dumps({
                'status': 'ok',
                'recommendation': recommendation,
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({'status': 'error', 'message': str(e)})


@register_tool('check_food')
class CheckFood(BaseTool):
    description = (
        'Look at the current camera view and remember the FOODS in it for get_meal_recommendation. '
        'Non-food objects are ignored. Checking the same place again replaces its foods. '
        'Does not add anything to the landmark map.'
    )
    parameters = [
        {
            'name': 'place',
            'type': 'string',
            'description': 'Where the robot is, e.g. "kitchen". Default: "here".',
            'required': False,
        },
    ]

    def call(self, params: str, **kwargs) -> str:
        import cv2
        from nutri_rag.assistant.availability import fdc_ids_for_labels
        from .detect_tool import _get_scanner
        from .detector_core import VLMDetector

        args = json5.loads(params) if params.strip() else {}
        place = (args.get('place') or 'here').strip().lower()
        scanner = _get_scanner()
        color_frame, _ = scanner._grab_frame()   # a fresh frame, not the last saved one
        if color_frame is None:
            return json.dumps({'status': 'error',
                               'message': 'No camera frame available. Is the RealSense camera running?'})
        bgr = (cv2.cvtColor(color_frame.data, cv2.COLOR_RGB2BGR)
               if color_frame.encoding == 'rgb8' else color_frame.data)
        cv2.imwrite(os.path.join(os.path.dirname(__file__), '..', 'data', 'food_check.png'), bgr)  # to see what was checked
        if isinstance(scanner.detector, VLMDetector):  # names the food in each package, not "box" / "bag"
            names = scanner.detector.list_foods(bgr)
        else:
            names = [d.label for d in scanner.detector.detect(bgr)]
        labels = {n.replace('_', ' ').strip().lower() for n in names}

        foods, not_food = {}, []
        for label in sorted(labels):
            ids = fdc_ids_for_labels([label])
            if ids:
                foods[label] = ids.pop()
            else:
                not_food.append(label)
        FOOD_LIST[place] = foods
        print(f'[check_food] {place}: foods {sorted(foods)}, ignored {not_food}')
        return json.dumps({
            'status': 'ok',
            'place': place,
            'foods': sorted(foods),
            'ignored_not_food': not_food,
            'note': 'get_meal_recommendation now uses only the foods found by check_food.'
                    if any(FOOD_LIST.values()) else 'No food found.',
        }, ensure_ascii=False)
