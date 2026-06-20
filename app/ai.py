import os
from typing import Dict, List, Optional

from app.config import CHAT_MODEL, CHAT_MODEL_SCHEDULE

_openai_client = None
_anthropic_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def generate_reply(
    latest_message: str,
    history: List[Dict[str, str]],
    provider: str,
    retrieved_context: Optional[List[str]] = None,
    *,
    base_script: Optional[str] = None,
    schedule_system_block: Optional[str] = None,
    profile_system_block: Optional[str] = None,
    schedule_guide_mode: bool = False,
) -> str:
    system_prompt = (
        "You are a calm meditation assistant. Keep responses concise, safe, and on-brand."
    )
    if profile_system_block:
        system_prompt = f"{system_prompt}\n\n{profile_system_block}"
    # Daily lessons ship a full script block; skip the long base script to cut tokens and latency.
    if base_script and not schedule_system_block:
        system_prompt = f"{system_prompt}\n\n[Grounded Base Script]\n{base_script}"
    if schedule_system_block:
        system_prompt = f"{system_prompt}\n\n{schedule_system_block}"
    if retrieved_context:
        context_block = "\n\n".join(retrieved_context)
        system_prompt = f"{system_prompt}\n\n[Additional context]\n{context_block}"

    temperature = (
        0.5
        if schedule_guide_mode
        else (0.2 if schedule_system_block else 0.5)
    )
    max_tokens = 300 if schedule_guide_mode else (280 if schedule_system_block else 300)
    model = CHAT_MODEL_SCHEDULE if schedule_system_block else CHAT_MODEL

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        client = _get_openai_client()
        messages = [{"role": "system", "content": system_prompt}] + history
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception:
            return f"[MVP fallback] You said: {latest_message}"

    if provider == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return f"[MVP fallback] You said: {latest_message}"

        client = _get_anthropic_client()
        try:
            response = client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=history,
            )
            text_blocks = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            return "\n".join(text_blocks).strip() or ""
        except Exception:
            return f"[MVP fallback] You said: {latest_message}"

    raise ValueError("Unsupported provider. Use 'openai' or 'claude'.")


def generate_reply_stream(
    latest_message: str,
    history: List[Dict[str, str]],
    provider: str,
    retrieved_context: Optional[List[str]] = None,
    *,
    base_script: Optional[str] = None,
    schedule_system_block: Optional[str] = None,
    profile_system_block: Optional[str] = None,
    schedule_guide_mode: bool = False,
):
    """Yield text deltas from the LLM (OpenAI streaming; Claude falls back to one chunk)."""
    system_prompt = (
        "You are a calm meditation assistant. Keep responses concise, safe, and on-brand."
    )
    if profile_system_block:
        system_prompt = f"{system_prompt}\n\n{profile_system_block}"
    if base_script and not schedule_system_block:
        system_prompt = f"{system_prompt}\n\n[Grounded Base Script]\n{base_script}"
    if schedule_system_block:
        system_prompt = f"{system_prompt}\n\n{schedule_system_block}"
    if retrieved_context:
        context_block = "\n\n".join(retrieved_context)
        system_prompt = f"{system_prompt}\n\n[Additional context]\n{context_block}"

    temperature = (
        0.5
        if schedule_guide_mode
        else (0.2 if schedule_system_block else 0.5)
    )
    max_tokens = 300 if schedule_guide_mode else (280 if schedule_system_block else 300)
    model = CHAT_MODEL_SCHEDULE if schedule_system_block else CHAT_MODEL

    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            yield f"[MVP fallback] You said: {latest_message}"
            return

        client = _get_openai_client()
        messages = [{"role": "system", "content": system_prompt}] + history
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            return
        except Exception:
            yield f"[MVP fallback] You said: {latest_message}"
            return

    # Claude: no streaming in this path — single completion
    text = generate_reply(
        latest_message,
        history,
        provider,
        retrieved_context,
        base_script=base_script,
        schedule_system_block=schedule_system_block,
        profile_system_block=profile_system_block,
        schedule_guide_mode=schedule_guide_mode,
    )
    yield text
