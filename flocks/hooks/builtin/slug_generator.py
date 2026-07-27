"""
LLM Slug Generator - Generate descriptive filenames using LLM

Uses LLM to generate a 1-2 word slug for session memory filenames.
"""

import asyncio
import re
from typing import Any, Optional

from flocks.config.config import Config
from flocks.provider.provider import ChatMessage
from flocks.provider import Provider
from flocks.utils.log import Log

log = Log.create(service="hooks.slug_generator")


async def generate_slug_via_llm(
    conversation: str,
    config: Any,
    session_id: str,
    timeout_seconds: int = 15,
) -> Optional[str]:
    """
    Generate a slug using LLM
    
    Args:
        conversation: Conversation summary
        config: Configuration object
        session_id: Session ID (for logging)
        timeout_seconds: Timeout in seconds
        
    Returns:
        slug string or None (on failure)
    """
    try:
        # Construct prompt
        prompt = f"""Based on this conversation, generate a short 1-2 word filename slug (lowercase, hyphen-separated, no file extension).

Conversation summary:
{conversation[:2000]}

Reply with ONLY the slug, nothing else. Examples: "vendor-pitch", "api-design", "bug-fix"
"""
        
        llm = await Config.resolve_default_llm()
        if not llm:
            return None
        provider_id = llm["provider_id"]
        model_id = llm["model_id"]
        await Provider.apply_config(provider_id=provider_id)
        provider = Provider.get(provider_id)
        if provider is None:
            return None

        from flocks.session.session import Session
        gateway_context = await Session.build_gateway_request_context(
            session_id,
            call_source="hooks.slug_generator",
        )
        response = await asyncio.wait_for(
            provider.chat(
                model_id=model_id,
                messages=[ChatMessage(role="user", content=prompt)],
                max_tokens=50,
                temperature=0.7,
                gateway_context=gateway_context,
            ),
            timeout=timeout_seconds,
        )

        # Extract and clean slug
        if response and response.content:
            text = response.content.strip()
            
            # Clean format
            slug = text.lower().replace(" ", "-").replace("_", "-")
            
            # Remove invalid characters
            slug = re.sub(r'[^a-z0-9-]', '', slug)
            slug = re.sub(r'-+', '-', slug)
            slug = slug.strip('-')
            
            # Limit length
            slug = slug[:30]
            
            if slug:
                log.debug("slug_generator.success", {
                    "session_id": session_id,
                    "slug": slug,
                })
                return slug
        
        log.warn("slug_generator.no_result", {
            "session_id": session_id,
        })
        return None
        
    except Exception as e:
        log.error("slug_generator.error", {
            "session_id": session_id,
            "error": str(e),
        })
        return None
