import os
import json
import httpx
import time
from typing import List, Dict, Optional, AsyncGenerator
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

_last_env_check = 0.0
_cached_mtime = 0.0

def get_ai_config() -> dict:
    global _last_env_check, _cached_mtime
    now = time.time()
    if now - _last_env_check > 2.0:
        _last_env_check = now
        env_file = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(env_file):
            try:
                mtime = os.path.getmtime(env_file)
                if mtime != _cached_mtime:
                    load_dotenv(env_file, override=True)
                    _cached_mtime = mtime
            except Exception:
                pass
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    api_key = os.getenv("AI_API_KEY", "").strip() or gemini_key
    default_provider = "gemini" if (gemini_key or "AI_API_KEY" in os.environ) else "openai"
    provider = os.getenv("AI_PROVIDER", default_provider).strip().lower()
    base_url = os.getenv("AI_BASE_URL", "").strip()
    model = os.getenv("AI_MODEL", "").strip()

    # Provider default fallbacks
    defaults = {
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini"
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b-versatile"
        },
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "model": "auto"
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat"
        },
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model": "gemini-3.5-flash"
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-3-5-sonnet-20241022"
        }
    }

    provider_defaults = defaults.get(provider, {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini"
    })

    final_base_url = base_url if base_url else provider_defaults["base_url"]
    final_model = model if model else provider_defaults["model"]

    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": final_base_url,
        "model": final_model,
        "has_key": bool(api_key)
    }

def get_ai_provider_status() -> dict:
    cfg = get_ai_config()
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "has_key": cfg["has_key"]
    }

def get_missing_key_message(provider: str, model: str) -> str:
    return f"""⚠️ **AI API Key Not Configured**

Logical AI requires an external AI provider API key to generate intelligent answers.

To activate AI responses, create or update the `.env` file in the project folder with your provider credentials:

```env
AI_PROVIDER={provider or 'openai'}
AI_API_KEY=your_actual_api_key_here
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL={model or 'gpt-4o-mini'}
```

### Supported Providers:
- **OpenAI:** `AI_PROVIDER=openai`, `AI_BASE_URL=https://api.openai.com/v1`, `AI_MODEL=gpt-4o-mini`
- **Groq:** `AI_PROVIDER=groq`, `AI_BASE_URL=https://api.groq.com/openai/v1`, `AI_MODEL=llama-3.3-70b-versatile`
- **OpenRouter:** `AI_PROVIDER=openrouter`, `AI_BASE_URL=https://openrouter.ai/api/v1`, `AI_MODEL=auto`
- **DeepSeek:** `AI_PROVIDER=deepseek`, `AI_BASE_URL=https://api.deepseek.com/v1`, `AI_MODEL=deepseek-chat`
- **Anthropic:** `AI_PROVIDER=anthropic`, `AI_BASE_URL=https://api.anthropic.com/v1`, `AI_MODEL=claude-3-5-sonnet-20241022`
- **Gemini (OpenAI-compatible):** `AI_PROVIDER=gemini`, `AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `AI_MODEL=gemini-1.5-flash`
- **Custom / Self-hosted:** Any OpenAI-compatible endpoint.

*After saving `.env`, restart the application with `npm run dev`.*
"""

async def stream_openai_compatible(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict],
    provider: str
) -> AsyncGenerator[bytes, None]:
    endpoint = base_url.rstrip('/')
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    if provider == "openrouter":
        headers["HTTP-Referer"] = "http://localhost:5173"
        headers["X-Title"] = "Logical AI"

    # Candidate models for automatic graceful fallback (especially Gemini quota spikes)
    if provider == "gemini":
        gemini_fallbacks = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-flash-latest"]
        models_to_try = [model] + [m for m in gemini_fallbacks if m != model]
    else:
        models_to_try = [model]

    last_error_code = 500
    last_err_msg = ""
    last_advice = ""

    for active_model in models_to_try:
        payload = {
            "model": active_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.6
        }

        try:
            async with client.stream("POST", endpoint, headers=headers, json=payload, timeout=90.0) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    try:
                        err_json = json.loads(error_text)
                        err_msg = err_json.get("error", {}).get("message", error_text)
                    except Exception:
                        err_msg = error_text

                    last_error_code = response.status_code
                    last_err_msg = err_msg

                    if response.status_code == 401:
                        last_advice = f"Your `{provider}` API key appears to be invalid or expired. Please verify `AI_API_KEY` in your `.env` file."
                        break
                    elif response.status_code == 429:
                        last_advice = f"Rate limit or quota exceeded for `{provider}`. Please check your credit balance or usage tier on your provider console."
                        continue
                    elif response.status_code in [404, 503, 500]:
                        last_advice = f"Model `{active_model}` temporarily unavailable. Attempting backup model."
                        continue
                    else:
                        last_advice = "Verify your provider configuration in `.env`."
                        break

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    line_str = line.strip()
                    if line_str.startswith("data:"):
                        data_str = line_str[5:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    yield content.encode("utf-8")
                        except json.JSONDecodeError:
                            continue
                return
        except (httpx.ConnectError, httpx.TimeoutException) as net_err:
            if active_model == models_to_try[-1]:
                if isinstance(net_err, httpx.ConnectError):
                    yield f"⚠️ **Connection Failed:** Could not reach AI provider endpoint at `{endpoint}`. Check your internet connection and `AI_BASE_URL` in `.env`.".encode("utf-8")
                else:
                    yield f"⚠️ **Request Timeout:** The AI provider took too long to respond. Please try again.".encode("utf-8")
                return
        except Exception as e:
            if active_model == models_to_try[-1]:
                yield f"⚠️ **AI Service Error:** {str(e)}".encode("utf-8")
                return

    # If all models in the fallback chain were exhausted
    yield f"🚨 **AI Provider Error ({last_error_code}):** {last_err_msg}\n\n💡 *Note:* {last_advice}".encode("utf-8")

async def stream_anthropic(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    chat_history: List[Dict]
) -> AsyncGenerator[bytes, None]:
    endpoint = base_url.rstrip('/')
    if not endpoint.endswith("/messages"):
        endpoint = f"{endpoint}/messages"

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }

    # Anthropic separates system prompt from chat history
    anthropic_messages = []
    for m in chat_history:
        role = "assistant" if m.get("role") in ["assistant", "ai"] else "user"
        anthropic_messages.append({"role": role, "content": m.get("content", "")})

    payload = {
        "model": model,
        "system": system_prompt,
        "messages": anthropic_messages,
        "max_tokens": 4096,
        "stream": True
    }

    try:
        async with client.stream("POST", endpoint, headers=headers, json=payload, timeout=90.0) as response:
            if response.status_code != 200:
                err_text = (await response.aread()).decode("utf-8", errors="replace")
                yield f"🚨 **Anthropic Error ({response.status_code}):** {err_text}".encode("utf-8")
                return

            async for line in response.aiter_lines():
                if not line:
                    continue
                line_str = line.strip()
                if line_str.startswith("data:"):
                    data_str = line_str[5:].strip()
                    try:
                        chunk = json.loads(data_str)
                        c_type = chunk.get("type")
                        if c_type == "content_block_delta":
                            delta = chunk.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text.encode("utf-8")
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        yield f"⚠️ **Anthropic Service Error:** {str(e)}".encode("utf-8")

async def stream_external_ai(
    client: httpx.AsyncClient,
    system_prompt: str,
    chat_history: List[Dict],
    image_b64: Optional[str] = None,
    requested_model: Optional[str] = None
) -> AsyncGenerator[bytes, None]:
    """Unified provider-independent AI stream handler"""
    cfg = get_ai_config()

    if not cfg["has_key"]:
        yield get_missing_key_message(cfg["provider"], cfg["model"]).encode("utf-8")
        return

    active_model = requested_model if requested_model else cfg["model"]
    provider = cfg["provider"]

    if provider == "anthropic":
        async for chunk in stream_anthropic(
            client=client,
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=active_model,
            system_prompt=system_prompt,
            chat_history=chat_history
        ):
            yield chunk
        return

    # OpenAI-compatible format (OpenAI, Groq, OpenRouter, DeepSeek, Gemini, Custom)
    formatted_messages = [{"role": "system", "content": system_prompt}]

    for m in chat_history:
        role = "assistant" if m.get("role") in ["assistant", "ai"] else "user"
        content = m.get("content", "")
        formatted_messages.append({"role": role, "content": content})

    # Add vision / image to latest user message if provided
    if image_b64 and formatted_messages:
        clean_b64 = image_b64.split(",")[1] if "," in image_b64 else image_b64
        last_msg = formatted_messages[-1]
        if last_msg["role"] == "user":
            orig_text = last_msg.get("content", "")
            last_msg["content"] = [
                {"type": "text", "text": orig_text or "Please analyze this image."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"}}
            ]

    async for chunk in stream_openai_compatible(
        client=client,
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=active_model,
        messages=formatted_messages,
        provider=provider
    ):
        yield chunk
