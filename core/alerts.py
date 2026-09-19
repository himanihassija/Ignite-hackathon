"""
Alert logging -- the PRD's own Alert node (id, type, severity, ts, status),
linked via ABOUT to whatever Order or Rider it concerns. Nothing writes one
of these automatically; a caller that decides something alert-worthy just
happened (a reroute, a spike with no alternate) calls log_alert() to leave
an audit trail. core/summary.py counts these to answer "how many reroutes
happened today" for the Slack digest.
"""
import uuid
from datetime import datetime, timezone
from .neo4j_client import get_driver


def log_alert(alert_type, severity, order_id=None, rider_id=None, ts=None):
    """alert_type: e.g. 'reroute', 'spike_no_alternate'. severity: e.g. 'high'.
    Returns the new alert's id."""
    driver = get_driver()
    alert_id = f"alert-{uuid.uuid4().hex[:12]}"
    ts = ts or datetime.now(timezone.utc)
    with driver.session() as session:
        session.run("""
            CREATE (a:Alert {id: $id, type: $type, severity: $severity, ts: datetime($ts), status: 'open'})
        """, id=alert_id, type=alert_type, severity=severity, ts=ts.isoformat())
        if order_id:
            session.run("""
                MATCH (a:Alert {id: $id}), (o:Order {id: $order_id})
                MERGE (a)-[:ABOUT]->(o)
            """, id=alert_id, order_id=order_id)
        if rider_id:
            session.run("""
                MATCH (a:Alert {id: $id}), (r:Rider {id: $rider_id})
                MERGE (a)-[:ABOUT]->(r)
            """, id=alert_id, rider_id=rider_id)
    return alert_id
