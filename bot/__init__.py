"""
upfh_bot package
────────────────────────────────────────
Nothing heavy should run at *import-time*.
All resource-intensive work (OpenAI client,
crawler, Gradio UI, etc.) lives in the
sub-modules such as `chatbot.py` and
`gradio_app.py`.
"""

# Re-export the chat() function so callers
# can simply `from upfh_bot import chat`
__all__ = ["chat"]

from .chatbot import chat   # noqa: F401  – keep import for re-export only
