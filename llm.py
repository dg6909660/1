import json
import re
import time

import requests

import config


class OutOfTime(Exception):
    pass


def chat(model, system, user, json_mode=False, temperature=0.3, num_predict=600, num_ctx=8192):
    if time.time() > config.DEADLINE:
        raise OutOfTime()
    payload = {
        "model": model,
        "stream": False,
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
    }
    if json_mode:
        payload["format"] = "json"
    last = None
    for _ in range(3):
        try:
            timeout = max(60, min(900, config.DEADLINE - time.time() + 30))
            r = requests.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except Exception as e:
            last = e
            print(f"  LLM retry ({model}): {e}", flush=True)
            time.sleep(4)
            if time.time() > config.DEADLINE:
                raise OutOfTime()
    raise RuntimeError(f"LLM failed: {last}")


def chat_json(model, system, user, **kw):
    for _ in range(3):
        raw = chat(model, system, user, json_mode=True, **kw)
        try:
            return json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
    raise ValueError("model did not return valid JSON")
