"""Model service names per use case (AI-06): the one place they live.

Every model call goes through a Unity Gateway model service: quote_ai_extract, quote_ai_chat and quote_ai_embed, each
named with the bundle target (resources/quote_ai/quote_ai_*.model_service.yml), so dev and prod stay apart. The
target comes from BUNDLE_TARGET (set by the app and the jobs); hyphens become underscores, as in the variable
quote_ai_target (dev-shared -> dev_shared).
"""
import os

TARGET = os.environ.get("BUNDLE_TARGET", "dev").replace("-", "_")
EXTRACT = f"quote_ai_extract_{TARGET}"
CHAT = f"quote_ai_chat_{TARGET}"
EMBED = f"quote_ai_embed_{TARGET}"


def model(name: str) -> str:
    """The `model` value for a chat request to a model service. Verify before recording how Unity Gateway addresses a
    model service from the OpenAI-compatible client (its bare name, or catalog.schema.name). If it is the full name,
    set QUOTE_AI_AI_SCHEMA=<catalog>.<schema> and this returns it; nothing else changes."""
    schema = os.environ.get("QUOTE_AI_AI_SCHEMA")
    return f"{schema}.{name}" if schema else name
