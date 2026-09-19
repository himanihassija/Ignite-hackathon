"""
End-to-end: candidate routes (real Google Routes API call) -> exposure score
-> explanation -> dispatch, using order-2's already-seeded pickup/drop points.
This is the PRD's MVP loop minus the initial place-name search (that's
covered separately by test_places.py): candidate routes, exposure score,
recommendation, explanation.
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.routes import create_candidates_for_order
from core.scoring import score_candidates, dispatch
from core.explain import explain_route

IST = timezone(timedelta(hours=5, minutes=30))

ORDER_ID = "order-2"
RIDER_ID = "rider-2"
DEPART_TS = datetime(2021, 1, 15, 10, 0, 0, tzinfo=IST)  # inside order-2's seeded delivery window, IST

print(f"Getting candidate routes for {ORDER_ID} from Google...")
route_ids = create_candidates_for_order(ORDER_ID, DEPART_TS)
print(f"  {len(route_ids)} candidate(s) created: {route_ids}")
print()

print(f"Scoring candidates (rider={RIDER_ID})...")
scored = score_candidates(ORDER_ID, rider_id=RIDER_ID)
for row in scored:
    print(f"  {row}")
print()

best = scored[0]
print(f"Best route: {best['route']} (dose_final={best['dose_final']:.2f}, coverage={best['coverage']:.2f})")
print()

print("Explanation:")
print(explain_route(best["route"], other_candidates=scored))
print()

print(f"Dispatching {RIDER_ID} -> {ORDER_ID} via {best['route']}...")
total_dose = dispatch(ORDER_ID, RIDER_ID, best["route"])
print(f"  total dose applied: {total_dose:.2f}")
print()
print("TEST_FULL_LOOP_COMPLETE")
