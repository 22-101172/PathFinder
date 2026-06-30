import os
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

GROQ_API_KEY = os.getenv('GROQ_API_KEY') or os.getenv('LLM_API_KEY')
GROQ_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.groq.com/openai/v1')
GROQ_MODEL = os.getenv('LLM_MODEL', 'llama-3.3-70b-versatile')


def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
    if not GROQ_API_KEY:
        return None  # Fall back to rule-based if no API key
    try:
        response = requests.post(
            f'{GROQ_BASE_URL}/chat/completions',
            headers={'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'},
            json={
                'model': GROQ_MODEL,
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                'max_tokens': max_tokens,
                'temperature': 0.7
            },
            timeout=15
        )
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        return None  # Always fall back gracefully
