"""
Tests core/explain.py against the same route-test-1 built by test_scoring.py.
Run test_scoring.py first if this errors with 'route not found'.
"""
import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.scoring import score_candidates
from core.explain import explain_route

ORDER_ID = "order-1"
ROUTE_ID = "route-test-1"
RIDER_ID = "rider-1"

candidates = score_candidates(ORDER_ID, rider_id=RIDER_ID)
print("Candidates:", candidates)
print()

explanation = explain_route(ROUTE_ID, other_candidates=candidates)
print("Explanation:")
print(explanation)
print()
print("TEST_EXPLAIN_COMPLETE")
