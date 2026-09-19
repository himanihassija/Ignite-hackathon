"""
Generates a real Alert entry via check_reroute_and_log(), reusing the severe-
spike scenario from test_reroute.py (route-test-2, Jan 23 2021) -- so the
Slack daily summary has a real reroute to report instead of zero.
Run test_reroute.py first if route-test-2 doesn't exist yet.
"""
import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.reroute import check_reroute_and_log

ROUTE_ID = "route-test-2"
ORDER_ID = "order-2"
RIDER_ID = "rider-2"
DESTINATION = "883da11585fffff"

result = check_reroute_and_log(ROUTE_ID, 100, DESTINATION, order_id=ORDER_ID, rider_id=RIDER_ID)
print(result)
print()
print("TEST_ALERTS_COMPLETE")
