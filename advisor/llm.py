import json
import os
import re
from langchain_groq import ChatGroq

MODEL = "openai/gpt-oss-20b"

class LLM:
    def __init__(self, api_key):
        self.chat = ChatGroq(model=MODEL, api_key=api_key, temperature=0)

    def complete(self, system, user):
        response = self.chat.invoke([("system", system), ("user", user)])
        return response.content

def from_env():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    return LLM(key)

def parse_json(text):
    # the model sometimes adds extra text around the JSON, so cut out just the {...} part
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("no JSON in model output")
    return json.loads(match.group(0))