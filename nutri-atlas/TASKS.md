# End-to-End Test Tasks — Robot Assistant Meal Recommendation Pipeline

Concrete prompts to exercise the two-phase meal recommendation flow (Phase 1
ideal → user agrees → Phase 2 grounded refinement). Each prompt stresses a
different combination of parameter extraction, allergen filtering, condition
shaping, and availability grounding.

## Pre-flight

```bash
# Robot side
ros2 launch realsense_zmq bringup_with_zmq.launch.py
python ~/test_ws/atlas/zmq_communication/zmq_bridge_node_working_v2.py

# Operator side — LLM server
cd ~/work/atlas/mimir/nutri_rag && bash scripts/start_server.sh

# Operator side — assistant (REQUIRED: AVAILABILITY_SOURCE=zmq)
cd ~/work/atlas/mimir/nutri-atlas/robot_control
export AVAILABILITY_SOURCE=zmq
python robot_assistant.py --robot-ip 192.168.0.164 --robot-port 5555 \
    --detection-mode real --detector vlm 2>&1 | tee trial_$(date +%s).log
```

Without `AVAILABILITY_SOURCE=zmq` Phase 2 is decorative — the second
`get_meal_recommendation` call will not see the kitchen items and will return
the same response as Phase 1.

## Kitchen ground truth (the physical scene during these tests)

```
avocado, bread, peanut cream, green vegetables, orange, Sprite, coffee, water
```

Two caveats:

- **Sprite** likely fails the availability filter's text-similarity threshold
  (it is a brand name, not a USDA food entry). It should not appear in the
  filtered fdc_id set, and it should not appear in any recommendation.
- **Peanut cream** maps to `Peanut butter` via the text top-1 match.
  Effective availability set is roughly
  `{avocado, bread, peanut butter, [greens], orange}` — about 4-5 fdc_ids.


## Prompt 1 — Control / smoke

**Say:**
> I ate a bowl of oatmeal for breakfast, recommend lunch.

**What it tests:** Baseline Phase 1 + 2 with no allergens, no condition.
Parameter extraction should produce `disliked=[]`, `condition=None`. Phase 2
should refine using what is in the kitchen.

**Console signature:**
```
[nutrition] eaten="...oatmeal...", meal_type=breakfast, next=lunch, disliked=[], condition=None
[nutrition] availability filter: N fdc_ids        (N = 4-5)
```

**Expected Phase 2 result:** Avocado + bread + greens (open-faced avocado
toast with greens is the obvious choice). Should not include peanut cream
(no allergy, but unusual on an oatmeal-recovery lunch), should not include
Sprite, orange may appear as a side.

**Pass:** All 10 checkpoints.

---

## Prompt 2 — Allergen extraction and filter

**Say:**
> I had toast for breakfast. I am allergic to peanuts. Recommend lunch.

**What it tests:** C1 (allergen extraction). Critically, peanut cream is
physically present in the kitchen — Phase 2 must respect the allergen even
though the availability filter would otherwise allow it.

**Console signature:**
```
[nutrition] eaten="toast", ..., disliked=['peanuts'], condition=None
```

**Expected Phase 2 result:** Avocado + greens-based meal. No peanut, no
peanut butter, no peanut cream anywhere in the final recommendation text.

**Pass:** C10 = 1 (zero peanut mentions). Verifies the allergen filter is
hard, not soft, when colliding with availability.

**Fail mode:** Pipeline's 5-tier relaxation cascade reaches Tier 5 ("drop
disliked") and puts peanut butter back in. If you see this, either the
relaxation thresholds are wrong or the disliked list is not being threaded
through to `MealRecommender`.

---

## Prompt 3 — Allergen + condition (diabetes)

**Say:**
> I had an orange for breakfast. I am allergic to peanuts and I have diabetes. Recommend lunch.

**What it tests:** Both C1 and C2 extracted simultaneously, with condition
shaping macro targets toward low-carb.

**Console signature:**
```
[nutrition] eaten="orange", ..., disliked=['peanuts'], condition='diabetes'
```

**Expected Phase 2 result:** Avocado-heavy lunch (high fat, low carb fits
diabetes). Possibly with greens. Bread (high carb) and orange (sugar) should
be de-emphasized or omitted. No peanut cream.

**Pass:** C2 = 1. Recommendation reasoning should mention diabetes, low-carb,
or blood sugar.

**Quality metric:** Carb content of recommended meal should be at or below
standard diabetic guidance (~45g for the meal). If the LLM recommends a
sandwich without flagging carb content, this fails on quality even if it
passes binary checkpoints.

---

## Prompt 4 — Multi-allergen extraction

**Say:**
> I had oatmeal for breakfast. I am allergic to peanuts and gluten. Recommend lunch.

**What it tests:** Parameter extraction with a list (C1 must produce 2
items). Gluten is in bread — should exclude bread even though bread is in
the kitchen.

**Console signature:**
```
[nutrition] eaten="oatmeal", ..., disliked=['peanuts', 'gluten'], condition=None
```

**Expected Phase 2 result:** Avocado + greens + orange. Both bread and
peanut cream are physically present but both are excluded by the allergen
filter. Tests the dual-constraint case.

**Pass:** C10 strict — final text has zero mentions of peanut OR
bread/wheat/gluten products.

**Fail mode:** LLM extracts only `['peanuts']` and misses gluten. If this
happens, the parameter-extraction instruction in the system prompt needs
tightening for list handling.

---

## Prompt 5 — Stress / starvation

**Say:**
> I had a bowl of yogurt with granola for breakfast. I am allergic to peanuts, gluten, and dairy. I have diabetes. Recommend lunch.

**What it tests:** All four disliked entries + condition firing at once. The
kitchen has only avocado, greens, and orange that pass all filters (bread →
gluten, peanut cream → peanut, Sprite → sugar / diabetes, orange → marginal
on sugar).

**Expected Phase 2 result:** Avocado + greens only — possibly garnished with
orange in a small quantity. The recommendation may legitimately be brief or
sparse because little in the kitchen passes all filters.

**Pass:** C10 strict (zero allergen mentions). Availability coverage should
be high (whatever the LLM recommends should be in `{avocado, greens, orange}`).
Phase 2 should NOT pull in items from the broader corpus that are not in
the kitchen — that would mean the availability filter is not being respected.

**Fail mode:** Tier 5 relaxation fires because everything got filtered out,
and the fallback meal includes something off-pantry like "almond butter
sandwich". Tells you the cascade is too eager.

---

## Prompt 6 — Conflict with eaten foods

**Say:**
> I had two slices of avocado toast for breakfast. Recommend lunch.

**What it tests:** Gap analysis — the user already had bread + avocado, so
Phase 1's ideal should pivot to protein-heavy or different macros. Phase 2
in your kitchen has bread + avocado available again. The system has two
reasonable options:

- Recommend the same things (avocado + bread) — gap analysis is not shifting
- Recommend mostly greens (low calories, the user had enough carbs and fat)

**Expected:** A protein-leaning lunch that recognizes the bread + fat already
consumed. Probably greens-based, possibly with orange.

**Quality metric:** Phase 1 ideal recommendation's macro targets should be
visibly different from a meal recommendation given no eaten history. Look
for "protein" being elevated in the gap analyzer's targets.

---

## Suggested run order

| Step | Prompt | Why |
|---|---|---|
| 1 | #1 control | Confirms baseline works before adding constraints |
| 2 | #2 single allergen | Isolates C1 + C10 |
| 3 | #3 allergen + condition | Adds C2; tests interaction |
| 4 | #4 multi-allergen | Tests list extraction |
| 5 | #5 starvation | Tests cascade behavior under tight constraints |
| 6 | #6 conflict | Tests gap-analysis dynamics |

If #1 fails, do not continue — fix the basics first. If #2 fails C10, the
allergen filter integration is broken, also stop. Past those, each later
prompt is more diagnostic than gate-keeping.

## What to log per trial

The `tee trial_*.log` in the pre-flight handles capture. Per trial verify
the log contains:

- The two `[nutrition]` lines (Phase 1 + Phase 2) — proves what the LLM
  extracted and what filter applied
- The final assistant text — needed to grade C10 and quality metrics
- `[register_objects]` (not `[scan_objects]`) on the kitchen visit
