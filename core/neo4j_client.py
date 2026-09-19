"""Shared Neo4j connection, used by every other module in core/ (and by both
front doors -- Streamlit and FastAPI -- once they're built). One driver per
process, reads the same .env the pipeline scripts use.
"""
import os
from neo4j import GraphDatabase

_PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _load_env():
    env = {}
    with open(os.path.join(_PROJECT_ROOT, ".env")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


_ENV = _load_env()
_driver = None


def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(_ENV["NEO4J_URI"], auth=(_ENV["NEO4J_USERNAME"], _ENV["NEO4J_PASSWORD"]))
    return _driver


def env(key, default=None):
    return _ENV.get(key, default)
