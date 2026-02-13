"""OpenAI LLM client wrapper for FactoryBot agents."""

import json
import logging
from openai import AsyncOpenAI
from bot.config import OPENAI_API_KEY, OPENAI_MODEL_HEAVY, OPENAI_MODEL_LIGHT

log = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def chat(
    system_prompt: str,
    messages: list[dict],
    *,
    heavy: bool = False,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    json_mode: bool = False,
) -> str:
    """Send a chat completion request.

    Args:
        system_prompt: System message defining agent behavior.
        messages: List of {"role": "user"|"assistant", "content": "..."}.
        heavy: Use GPT-4o (True) or GPT-4o-mini (False).
        temperature: Sampling temperature.
        max_tokens: Max response tokens.
        json_mode: If True, request JSON output.

    Returns:
        The assistant's response text.
    """
    model = OPENAI_MODEL_HEAVY if heavy else OPENAI_MODEL_LIGHT
    all_messages = [{"role": "system", "content": system_prompt}] + messages

    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        resp = await _client.chat.completions.create(
            model=model,
            messages=all_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        content = resp.choices[0].message.content or ""
        log.debug("LLM [%s] tokens: %s", model, resp.usage)
        return content
    except Exception:
        log.exception("LLM call failed (model=%s)", model)
        raise


async def classify_intent(user_message: str) -> dict:
    """Classify a user message into an intent using GPT-4o-mini.

    Returns dict with keys: intent, confidence, project_name (optional).
    Possible intents: new_idea, brainstorm, approve, reject, question,
                      scope_change, revisit, status_check, pause, resume, general
    """
    system = (
        "You are a message classifier for a software project management bot. "
        "Classify the user's message into exactly one intent.\n\n"
        "Possible intents:\n"
        "- new_idea: User is pitching a new project idea\n"
        "- brainstorm: User is continuing to discuss/refine an idea\n"
        "- approve: User is approving the current phase (idea, PRD, build)\n"
        "- reject: User wants changes to the current deliverable\n"
        "- question: User is asking a question about the project or process\n"
        "- scope_change: User wants to add features or change scope\n"
        "- revisit: User wants to revisit an existing project\n"
        "- status_check: User wants to know current project status\n"
        "- pause: User wants to pause work\n"
        "- resume: User wants to resume paused work\n"
        "- general: Anything else (greeting, off-topic, etc.)\n\n"
        "Respond in JSON: {\"intent\": \"...\", \"confidence\": 0.0-1.0, \"project_name\": \"...\" or null}"
    )
    raw = await chat(
        system,
        [{"role": "user", "content": user_message}],
        heavy=False,
        temperature=0.1,
        max_tokens=150,
        json_mode=True,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"intent": "general", "confidence": 0.5, "project_name": None}
