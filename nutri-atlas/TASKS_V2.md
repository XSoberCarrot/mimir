# End-to-End Test Tasks — Robot Assistant Meal Recommendation Pipeline

Three difficulty levels exercise progressively more of the two-phase meal
recommendation flow (Phase 1 ideal → user agrees → Phase 2 grounded refinement).
Each level is graded by a fixed set of subtasks; a trial **succeeds at its level**
when all of that level's subtasks pass.

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

## Kitchen ground truth (physical scene during these tests)

```
avocado, bread, peanut cream, green vegetables, orange, Sprite, coffee, water
```

- **Sprite** likely fails the availability filter's text-similarity threshold
  (brand name, not a USDA entry) — it should not appear in any recommendation.
- **Peanut cream** maps to `Peanut butter` via the text top-1 match. Effective
  availability ≈ `{avocado, bread, peanut butter, greens, orange}` (~4–5 fdc_ids).

## How to test

For every prompt:

1. Say the prompt verbatim.
2. Wait for the recommendation.
3. **Middle / Difficult:** when the LLM offers to check the kitchen, say `yes please`.
   **Easy:** decline the kitchen check (or do not trigger it) — Easy is Phase 1 only.
4. Capture the console output (the `tee` pipe in pre-flight does this).
5. Grade the subtasks for that level.

## Subtasks

| ID | Subtask | Passes when (console / output) |
|---|---|---|
| **S1 — Nutrition analysis** | parses intake and identifies the gap / macro targets | `[nutrition] eaten=…, meal_type=…, next=…` + gap targets in output |
| **S2 — Recommendation** | produces a valid, named meal for the gap | recommendation text names a dish + ingredients (**C3**) |
| **S3 — Navigation** | reaches the kitchen | `navigate_to_landmark` chosen + `Arrived at kitchen` (**C5 ∧ C6**) |
| **S4 — Availability check & grounding** | scans, maps detections to food, Phase 2 uses observed items | `register_objects` + `availability filter: N fdc_ids` (N>0) + coverage lift (**C7 ∧ C9**) |
| **S5 — Constraint check** | extracts and honors allergens/condition end-to-end | `disliked=[…]`/`condition=…` in both phases + final meal allergen-safe (**C1/C2 ∧ C8 ∧ C10**) |

## Levels

| Level | Required subtasks | Robot action |
|---|---|---|
| **Easy** | S1 + S2 | none (decline kitchen check) |
| **Middle** | S1 + S2 + S3 + S4 | navigate + scan kitchen |
| **Difficult** | S1 + S2 + S3 + S4 + S5 | navigate + scan + honor constraints |

Report **per-subtask pass rate** (which step is weak) and **per-level success
rate** (all required subtasks pass — one headline number per tier).

## Prompts

### Easy (S1–S2) — plain recommendation, no kitchen check

- **E1:** "I had a bowl of oatmeal for breakfast, recommend a lunch." *(decline the kitchen check)*
- **E2:** "I ate a banana and toast for breakfast — what should I have for lunch?"
- **E3:** "I had a chicken salad for lunch, suggest a dinner for me."

### Middle (S1–S4) — recommend + ground in the kitchen, no constraints

- **M1:** "I had oatmeal for breakfast, recommend lunch." → `yes please`
- **M2:** "I had two slices of avocado toast for breakfast, recommend lunch." → `yes please`
- **M3:** "I had yogurt for breakfast, recommend a light lunch I can make here." → `yes please`

### Difficult (S1–S5) — + allergen / condition

- **D1:** "I had toast for breakfast. I am allergic to peanuts. Recommend lunch." → `yes please`
- **D2:** "I had an orange for breakfast. I am allergic to peanuts and I have diabetes. Recommend lunch." → `yes please`
- **D3:** "I had yogurt with granola for breakfast. I am allergic to peanuts and gluten. Recommend lunch." → `yes please`

## Console checkpoint reference (C1–C10)

The subtasks above are built from these underlying signals; grade them off the log.

| # | Checkpoint | Pass signal (console / output) |
|---|---|---|
| C1 | Allergen extracted | `[nutrition] ... disliked=['Y']` |
| C2 | Condition extracted | `[nutrition] ... condition='Z'` (or unset if none) |
| C3 | Phase 1 nutrition tool fired | one `[nutrition]` line + recommendation text returned |
| C4 | LLM offered kitchen check | response contains "check / refine / kitchen" interrogative |
| C5 | Chose `navigate_to_landmark` | `[navigate_to_landmark] navigating to: kitchen` (NOT `navigate_and_scan`) |
| C6 | Navigation arrived | bridge reply `status=success msg=Arrived at kitchen` |
| C7 | Chose `register_objects` | `[register_objects]` in log (NOT `scan_objects`) |
| C8 | Phase 2 carries SAME params | second `[nutrition]` line with same `disliked=` and `condition=` |
| C9 | Availability filter applied | `[nutrition] availability filter: N fdc_ids` (N > 0) |
| C10 | Final recommendation has no allergen | final recipe ingredients ∩ Y = ∅ |

## What to log per trial

The `tee trial_*.log` handles capture. Per trial verify the log contains:

- The two `[nutrition]` lines (Phase 1 + Phase 2) — what the LLM extracted and which filter applied.
- The final assistant text — needed to grade S2 and S5.
- `[register_objects]` (not `[scan_objects]`) on the kitchen visit.
