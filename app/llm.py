"""One thin model wrapper. If there is no API key the app still runs: brain.py falls back to deterministic rules,
and says so in its output (mode: 'rules'), so nothing is silently pretending to be AI."""
import os


class AnthropicLLM:
    def __init__(self, model):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, system, user, role="", media=None, max_tokens=3000):
        content = []
        for m in media or []:
            if m["kind"] == "image":
                content.append({"type": "image", "source": {"type": "base64", "media_type": m["media_type"], "data": m["data"]}})
            elif m["kind"] == "pdf":
                content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": m["data"]}})
        content.append({"type": "text", "text": user})
        r = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": content}])
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def real_key():
    """A placeholder copied from .env.example ('your-key-here') must count as NO key."""
    k = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    return k if k and not k.lower().startswith("your") and "key-here" not in k.lower() else ""


def get_llm():
    if os.getenv("PELLIPLAN_LLM", "auto") == "mock" or not real_key():
        return None
    return AnthropicLLM(os.getenv("PELLIPLAN_MODEL", "claude-sonnet-5-5"))
