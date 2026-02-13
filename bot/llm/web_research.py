"""Web research capabilities for FactoryBot agents.

Uses duckduckgo-search for web search and curl for page fetching.
Agents call auto_research() to automatically decide if research is needed.
"""

import asyncio
import json
import logging
import re
import subprocess

from duckduckgo_search import DDGS

from bot.llm.client import chat

log = logging.getLogger(__name__)


async def search_web(query: str, num_results: int = 5) -> list[dict]:
    """Search the web using DuckDuckGo.

    Returns list of {"title": ..., "url": ..., "snippet": ...}
    """
    try:
        def _search():
            with DDGS() as ddgs:
                results = []
                for r in ddgs.text(query, max_results=num_results):
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
                return results

        return await asyncio.to_thread(_search)

    except Exception as e:
        log.warning("Web search error: %s", e)
        return []


async def fetch_page(url: str, max_chars: int = 5000) -> str:
    """Fetch a web page and extract text content."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "curl", "-s", "-L",
                "-H", "User-Agent: Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36",
                "--max-time", "15",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return ""

        text = _html_to_text(result.stdout)
        return text[:max_chars]

    except Exception as e:
        log.warning("Page fetch error for %s: %s", url, e)
        return ""


async def research(query: str, context: str = "") -> str:
    """Do web research on a topic: search, fetch top results, summarize.

    Args:
        query: What to search for.
        context: Additional context about why we're searching.

    Returns:
        A research summary string.
    """
    log.info("Researching: %s", query[:80])

    # Search
    results = await search_web(query)
    if not results:
        return f"No encontré resultados para: {query}"

    # Fetch top 3 pages
    pages = []
    for r in results[:3]:
        content = await fetch_page(r["url"], max_chars=3000)
        if content:
            pages.append({
                "title": r["title"],
                "url": r["url"],
                "content": content[:2000],
            })

    if not pages:
        # Fall back to snippets
        snippets = "\n".join(
            f"- {r['title']}: {r['snippet']}" for r in results
        )
        return f"Resultados de búsqueda para '{query}':\n{snippets}"

    # Summarize with LLM
    pages_text = "\n\n---\n\n".join(
        f"Source: {p['title']} ({p['url']})\n{p['content']}"
        for p in pages
    )

    summary = await chat(
        "You are a research assistant. Summarize the key information from these web pages "
        "that is relevant to the context. Be factual and concise. Write in Spanish.",
        [{"role": "user", "content": (
            f"RESEARCH QUERY: {query}\n"
            f"CONTEXT: {context}\n\n"
            f"WEB PAGES:\n{pages_text}"
        )}],
        heavy=False,
        temperature=0.2,
        max_tokens=1000,
    )

    sources = "\n".join(f"  - {p['url']}" for p in pages)
    return f"{summary}\n\nFuentes:\n{sources}"


async def auto_research(user_message: str, project_context: str) -> str | None:
    """Decide if research is needed and do it automatically.

    Returns research results if research was needed, None otherwise.
    """
    decision = await chat(
        "You decide if a user message requires web research to fulfill properly. "
        "Examples that need research: 'make a landing for John Doe, a famous photographer' "
        "(need to look up John Doe), 'build a site like Stripe' (need to see Stripe's approach), "
        "'the client is restaurant X in Buenos Aires' (need to find their info), "
        "'use a style similar to Apple' (need to reference Apple's design).\n"
        "Examples that DON'T need research: 'I want a to-do app', 'use blue color palette', "
        "'add a login page', 'looks good let's continue'.\n\n"
        "Respond in JSON: {\"needs_research\": true/false, \"queries\": [\"search query 1\", ...]}",
        [{"role": "user", "content": (
            f"Project context: {project_context[:500]}\n"
            f"User message: {user_message}"
        )}],
        heavy=False,
        temperature=0.1,
        max_tokens=200,
        json_mode=True,
    )

    try:
        data = json.loads(decision)
    except json.JSONDecodeError:
        return None

    if not data.get("needs_research"):
        return None

    queries = data.get("queries", [])
    if not queries:
        return None

    # Run all queries
    all_results = []
    for q in queries[:3]:  # Max 3 queries
        result = await research(q, project_context[:300])
        all_results.append(result)

    return "\n\n".join(all_results)


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"')
    text = re.sub(r'\s+', ' ', text).strip()
    return text
