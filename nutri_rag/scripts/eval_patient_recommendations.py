"""Patient case-study evaluation for Nutri-ATLAS (paper case-study table).

Runs real patient profiles from `diet_recommendations_dataset.csv` through the
actual recommendation pipeline (LLM gap analysis -> recommend_v2 -> recommend_meal)
and emits a case-study table:

    Patient | Restriction (disease . allergy) | Requirement
            | Recommended Meal | Nutrition | Requirement met

Separation of concerns (matches the paper):
  - Health condition shapes the macro TARGET via the LLM gap analysis (LLM Call 1).
  - Allergies are RETRIEVAL constraints applied in Stage 4 (disliked_names hard-exclude).

Requirements are MEASURED against published references. Both are carbohydrate-based
and read from the per-serving recipe macros -- the only nutrients the corpus stores
per recipe (calories/protein/carbohydrate/fat; NO sodium, NO sugar). Energy is
derived from the macros (4/4/9), not the noisy `calories` column.

  - Low-Carb (diabetes): carbohydrate < 26% of meal energy.
        Ref: Feinman et al., "Dietary carbohydrate restriction as the first approach
        in diabetes management", Nutrition 31(1):1-13, 2015.
  - Balanced (obesity / none): carbohydrate within 45-65% of meal energy.
        Ref: Institute of Medicine, Dietary Reference Intakes for Energy, Carbohydrate,
        Fiber, Fat, ... Protein, and Amino Acids, 2005 -- AMDR carbohydrate 45-65%.
  - No-allergen: the recipe contains none of the patient's allergen ingredients.

Low-Sodium / Low-Sugar are intentionally excluded: the corpus stores neither per
recipe, and the patient dataset records no sodium/sugar intake (only total kcal),
so they cannot be measured at either end.

Requires: the llama.cpp LLM server (gap analysis) + the Qwen3-Embedding model + DuckDB.
Run:  python nutri_rag/scripts/eval_patient_recommendations.py
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

# Make the nutri_rag package importable when run as a script.
_PKG = Path(__file__).resolve().parents[1]          # .../nutri_rag
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from nutri_rag.assistant.gap_analyzer import analyze_gap
from nutri_rag.assistant.food_recommender import FoodRecommender
from nutri_rag.assistant.meal_recommender import MealRecommender, _ingredients_contain_any

_REPO_ROOT = Path(__file__).resolve().parents[2]    # .../mimir
_DEFAULT_CSV = _REPO_ROOT / "diet_recommendations_dataset.csv"

# Allergy -> ingredient terms (used BOTH for hard exclusion and post-hoc safety check).
_ALLERGEN_TERMS = {
    "Peanuts": ["peanut", "peanuts"],
    "Gluten": ["wheat", "flour", "bread", "barley", "rye", "gluten", "pasta",
               "noodle", "spaghetti", "macaroni", "couscous", "bun", "bagel",
               "pretzel", "cracker", "crouton", "breadcrumb", "tortilla", "crust",
               "dough", "pizza", "pastry", "biscuit"],
}

# ---------------------------------------------------------------------------
# Requirement MEASUREMENT against published references (see module docstring).
_LOWCARB_ENERGY_FRAC = 0.26        # Feinman et al. 2015: low-carb < 26% energy
_BALANCED_FRAC = (0.45, 0.65)      # IOM 2005 AMDR: carbohydrate 45-65% of energy

# Five varied (disease x allergy) combinations spanning the Low-Carb (diabetes)
# and Balanced (obesity / none) targets and both allergens. No low-sodium /
# low-sugar -- the corpus stores neither per recipe nor per patient.
_TARGET_COMBOS = [
    ("Diabetes", "Peanuts", "None"),   # Low-Carb + peanut-free
    ("Diabetes", "Gluten",  "None"),   # Low-Carb + gluten-free
    ("Obesity",  "Peanuts", "None"),   # Balanced + peanut-free
    ("Obesity",  "Gluten",  "None"),   # Balanced + gluten-free
    ("None",     "None",    "None"),   # Balanced + no allergy
]

# Dessert names mis-tagged as "lunch" in the corpus -- skip for meal recommendations.
_DESSERT_KW = (
    "sherbet", "sorbet", "ice cream", "ice-cream", "cake", "cupcake", "cookie",
    "brownie", "pudding", "frosting", "popsicle", "mousse", "parfait",
    "milkshake", "custard", "gelato", "donut", "doughnut", "candy",
)


def _is_dessert(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in _DESSERT_KW)


def _load_patients(csv_path: Path) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def _select_target_combos(patients: list[dict], seed: int) -> list[dict]:
    """One patient per entry in `_TARGET_COMBOS` (deterministic given the seed)."""
    rng = random.Random(seed)
    picked = []
    for disease, allergy, restriction in _TARGET_COMBOS:
        cands = [p for p in patients
                 if p["Disease_Type"] == disease
                 and p["Allergies"] == allergy
                 and p["Dietary_Restrictions"] == restriction]
        if cands:
            picked.append(rng.choice(cands))
    return picked


def _intake_item(p: dict) -> dict:
    """Synthesize the patient's current intake as one meal_item for analyze_gap.

    The patient dataset records only `Daily_Caloric_Intake` (no macro breakdown),
    so the macro split is estimated (50% carb / 20% protein / 30% fat). This only
    biases retrieval toward the target; the requirement is MEASURED on the recipe.
    """
    cal = float(p.get("Daily_Caloric_Intake", 0) or 0)
    return {
        "description": "the patient's typical daily intake",
        "meal_type": "daily intake",
        "nutrients": {
            "Carbohydrate, by difference": 0.50 * cal / 4.0,
            "Protein": 0.20 * cal / 4.0,
            "Total lipid (fat)": 0.30 * cal / 9.0,
            "Energy": cal,
        },
        "quantity": None,
    }


def _disliked_and_allergens(p: dict) -> tuple[list[str], list[str]]:
    """Return (disliked_names for hard-exclude, allergen_terms for safety check)."""
    allergen_terms = _ALLERGEN_TERMS.get(p["Allergies"], [])
    return list(allergen_terms), allergen_terms


def _requirements(p: dict) -> list[str]:
    """The carbohydrate requirement implied by the patient's disease."""
    return ["Low-Carb"] if p["Disease_Type"] == "Diabetes" else ["Balanced"]


def _profile_str(p: dict) -> str:
    g = (p["Gender"] or "?")[0]
    return f"{p['Patient_ID']}, {p['Age']}{g}, BMI {p['BMI']}"


def _restriction_str(p: dict) -> str:
    a = p["Allergies"]
    allergy = "no allergy" if a == "None" else f"{a.lower().rstrip('s')} allergy"
    return f"{p['Disease_Type']} $\\cdot$ {allergy}"


def _carb_energy_frac(meal) -> tuple[float, float | None]:
    """(carb grams, carbohydrate as fraction of macro-derived energy 4P+4C+9F)."""
    carb = float(meal.nutrients.get("carbohydrates", 0) or 0)
    prot = float(meal.nutrients.get("protein", 0) or 0)
    fat = float(meal.nutrients.get("fat", 0) or 0)
    energy = 4.0 * prot + 4.0 * carb + 9.0 * fat
    return carb, (carb * 4.0 / energy if energy > 0 else None)


def _check_low_carb(meal) -> dict:
    carb, frac = _carb_energy_frac(meal)
    return {"carb_g": carb, "pct": None if frac is None else 100.0 * frac,
            "pass": bool(frac is not None and frac < _LOWCARB_ENERGY_FRAC)}


def _check_balanced(meal) -> dict:
    carb, frac = _carb_energy_frac(meal)
    lo, hi = _BALANCED_FRAC
    return {"carb_g": carb, "pct": None if frac is None else 100.0 * frac,
            "pass": bool(frac is not None and lo <= frac <= hi)}


def _meets_requirement(meal, reqs: list[str]) -> bool:
    """True if the meal satisfies the patient's carbohydrate requirement."""
    if "Low-Carb" in reqs:
        return _check_low_carb(meal)["pass"]
    return _check_balanced(meal)["pass"]


def _band_distance(meal, reqs: list[str]) -> float:
    """Distance from the meal's carb%E to the requirement band (0 if inside)."""
    lo, hi = (0.0, _LOWCARB_ENERGY_FRAC) if "Low-Carb" in reqs else _BALANCED_FRAC
    _, frac = _carb_energy_frac(meal)
    if frac is None:
        return float("inf")
    return max(lo - frac, frac - hi, 0.0)


def _has_allergen(meal, terms: list[str]) -> bool:
    """Allergen present in the recipe's ingredients OR its name (the name catches
    dishes like ``Pizza Sticks`` whose ingredient strings hide the gluten source)."""
    return bool(terms) and (
        _ingredients_contain_any(meal.ingredients, terms)
        or _ingredients_contain_any([meal.recipe_name], terms))


def _select_meal(meals, reqs: list[str], allergen_terms: list[str],
                 used_names: set[str] | None = None):
    """Constraint-aware selection: among the retrieved candidates, take the
    highest-ranked non-dessert, allergen-free recipe that meets the patient's
    carbohydrate band; if none qualify, fall back to the closest such recipe.
    This applies Stage 4's constraint filter to the clinical macro target.
    Recipes in `used_names` (already shown for another patient) are de-prioritized
    so the case-study table shows distinct meals where possible."""
    used_names = used_names or set()
    safe = [m for m in meals
            if not _is_dessert(m.recipe_name) and not _has_allergen(m, allergen_terms)]
    if not safe:
        safe = [m for m in meals if not _is_dessert(m.recipe_name)] or list(meals)
    in_band = [m for m in safe if _meets_requirement(m, reqs)]
    if in_band:
        fresh = [m for m in in_band if m.recipe_name not in used_names]
        return (fresh or in_band)[0]            # prefer an unused in-band meal
    fresh_safe = [m for m in safe if m.recipe_name not in used_names] or safe
    return min(fresh_safe, key=lambda m: _band_distance(m, reqs))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=_DEFAULT_CSV)
    ap.add_argument("--n", type=int, default=5, help="max patients (target combos)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--macro-weight", type=float, default=1.0,
                    help="weight pulling recipe retrieval toward the target macros")
    ap.add_argument("--out", type=Path, default=None, help="optional CSV output path")
    args = ap.parse_args()

    patients = _select_target_combos(_load_patients(args.csv), args.seed)[: args.n]
    print(f"Selected {len(patients)} patients (target combos) from {args.csv}\n")

    food_rec = FoodRecommender()
    meal_rec = MealRecommender()

    rows = []
    used_names: set[str] = set()
    for p in patients:
        pid = p["Patient_ID"]
        disease = p["Disease_Type"]
        disliked, allergen_terms = _disliked_and_allergens(p)
        reqs = _requirements(p)

        # LLM Call 1 -- gap analysis shaped by the health condition (retrieval bias).
        gap = analyze_gap([_intake_item(p)], next_meal="lunch", health_condition=disease)
        targets = gap["targets"]

        # Stage 2/4 -- gap-filling foods then meal composition with allergen exclusion.
        foods = food_rec.recommend_v2(targets=targets, n_results=10)
        meals = meal_rec.recommend_meal(
            recommended_foods=foods, targets=targets, next_meal="lunch",
            disliked_names=disliked or None, min_overlap=0, top_k_final=50,
            nutrient_tolerance=0.0,   # disable hard +-50% macro filter -> wide pool;
                                      # the carb band is enforced in _select_meal instead
            macro_weight=args.macro_weight,
        )
        if not meals:
            rows.append({"pid": pid, "p": p, "meal": None})
            print(f"[{pid}] {disease:10s} -> NO MEAL RETURNED")
            continue

        top = _select_meal(meals, reqs, allergen_terms, used_names)
        used_names.add(top.recipe_name)

        # ---- MEASURE the carbohydrate requirement + allergen safety ----
        checks: dict[str, dict] = {}
        if "Low-Carb" in reqs:
            checks["Low-Carb"] = _check_low_carb(top)
        if "Balanced" in reqs:
            checks["Balanced"] = _check_balanced(top)
        allergen_free = not _has_allergen(top, allergen_terms)
        all_pass = allergen_free and all(c["pass"] for c in checks.values())

        rows.append({"pid": pid, "p": p, "meal": top, "reqs": reqs, "checks": checks,
                     "allergen_free": allergen_free, "all_pass": all_pass})
        req = reqs[0]
        print(f"[{pid}] {disease:10s} {req:9s} -> {top.recipe_name[:34]:34s} "
              f"carb {checks[req]['pct']:.0f}%E pass={checks[req]['pass']} "
              f"allergen_free={allergen_free}")

    # ---- LaTeX table rows ----
    print("\n" + "=" * 96)
    print("Met = allergen-safe AND the carbohydrate requirement passes its reference threshold")
    print("  Low-Carb  carb < 26% of meal energy           (Feinman et al. 2015)")
    print("  Balanced  carb within 45-65% of meal energy    (IOM 2005 AMDR)")
    print("=" * 96 + "\nLaTeX rows for the case-study table:\n")

    def _ok(b: bool) -> str:
        return r"\checkmark" if b else r"$\times$"

    for r in rows:
        p = r["p"]
        if not r.get("meal"):
            print(f"% {r['pid']}: no meal returned")
            continue
        meal = r["meal"]
        meal_name = meal.recipe_name.replace("&", r"\&").replace("_", r"\_")
        n = meal.nutrients
        nutr = (f"{n.get('calories',0):.0f}; "
                f"{n.get('protein',0):.0f}/{n.get('carbohydrates',0):.0f}/{n.get('fat',0):.0f}")
        c = r["checks"]
        ev = []
        if "Low-Carb" in c:
            ev.append(f"Low-Carb {c['Low-Carb']['pct']:.0f}\\,\\%E ($<$26) {_ok(c['Low-Carb']['pass'])}")
        if "Balanced" in c:
            ev.append(f"Balanced {c['Balanced']['pct']:.0f}\\,\\%E (45--65) {_ok(c['Balanced']['pass'])}")
        if p["Allergies"] != "None":
            ev.append(f"{p['Allergies'].lower().rstrip('s')}-free {_ok(r['allergen_free'])}")
        met_cell = r"\makecell[l]{" + r" \\ ".join(ev) + "}"
        reqs_cell = ", ".join(r["reqs"])
        print(f"{_profile_str(p)} & {_restriction_str(p)} & {reqs_cell} & "
              f"\\textit{{{meal_name}}} & {nutr} & {met_cell} \\\\ \\hline")

    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Patient", "Profile", "Restriction", "Requirement",
                        "Recommended_Meal", "Calories", "Protein_g", "Carb_g", "Fat_g",
                        "Carb_pctE", "Allergen_Free", "Met_All"])
            for r in rows:
                if not r.get("meal"):
                    continue
                n = r["meal"].nutrients
                pct = next(iter(r["checks"].values()))["pct"]
                w.writerow([r["pid"], _profile_str(r["p"]), _restriction_str(r["p"]),
                            r["reqs"][0], r["meal"].recipe_name,
                            f"{n.get('calories',0):.0f}", f"{n.get('protein',0):.0f}",
                            f"{n.get('carbohydrates',0):.0f}", f"{n.get('fat',0):.0f}",
                            f"{pct:.0f}" if pct is not None else "",
                            r["allergen_free"], r["all_pass"]])
        print(f"\nWrote CSV -> {args.out}")


if __name__ == "__main__":
    main()
