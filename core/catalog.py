"""环境目录加载。"""
import json
import os


def load_catalog(path: str = None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "env_catalog.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def by_id(catalog: dict, env_id: str) -> dict:
    for e in catalog["environments"]:
        if e["id"] == env_id:
            return e
    raise KeyError(env_id)


def categories(catalog: dict) -> list:
    seen = []
    for e in catalog["environments"]:
        c = e["category"]
        if c not in seen:
            seen.append(c)
    return seen
