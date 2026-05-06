import os
from typing import Dict, List


def generate_reply(latest_message: str, history: List[Dict[str, str]], provider: str) -> str:
    system_prompt = (
        "You are a calm meditation assistant. Keep responses concise, safe, and on-brand."
    )

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        messages = [{"role": "system", "content": system_prompt}] + history
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.5,
            )
            return response.choices[0].message.content or ""
        except Exception:
            return f"[MVP fallback] You said: {latest_message}"

    if provider == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        try:
            response = client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=300,
                system=system_prompt,
                messages=history,
            )
            text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            return "\n".join(text_blocks).strip() or ""
        except Exception:
            return f"[MVP fallback] You said: {latest_message}"

    raise ValueError("Unsupported provider. Use 'openai' or 'claude'.")
