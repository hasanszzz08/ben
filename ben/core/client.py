"""Anthropic Claude API Client integration for BEN using the official anthropic SDK."""

import time
from typing import Dict, Any, Generator, List, Optional
from pydantic import BaseModel
import anthropic
from anthropic import Anthropic, APIError, AuthenticationError, RateLimitError, APIConnectionError

from ben.config import settings
from ben.utils.logger import logger


class ClaudeConnectionResult(BaseModel):
    """Result of testing Anthropic Claude API connectivity."""
    success: bool
    model: str
    response: Optional[str] = None
    latency_ms: float = 0.0
    error: Optional[str] = None
    char_count: int = 0


# Backward compatibility aliases
AnthropicConnectionResult = ClaudeConnectionResult
GeminiConnectionResult = ClaudeConnectionResult


def redact_sensitive(text: str, secret: Optional[str]) -> str:
    """Ensure no secret token or key appears in log or error text."""
    if not text or not secret or len(secret) < 6:
        return text
    return text.replace(secret, "[REDACTED_API_KEY]")


class AnthropicClientManager:
    """Manages Anthropic Claude API interactions using the official anthropic SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self._custom_api_key = api_key
        self._custom_model = model
        self._custom_base_url = base_url
        self._client: Optional[Anthropic] = None

    @property
    def _api_key(self) -> Optional[str]:
        """Effective API key, prioritizing explicit custom key over environment settings."""
        return self._custom_api_key if self._custom_api_key is not None else settings.api_key

    @_api_key.setter
    def _api_key(self, value: Optional[str]) -> None:
        self._custom_api_key = value
        self._client = None

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key

    @property
    def _base_url(self) -> Optional[str]:
        """Effective base URL, prioritizing explicit custom URL over environment settings."""
        return self._custom_base_url if self._custom_base_url is not None else settings.base_url

    @_base_url.setter
    def _base_url(self, value: Optional[str]) -> None:
        self._custom_base_url = value
        self._client = None

    @property
    def base_url(self) -> Optional[str]:
        return self._base_url

    @property
    def is_gemini(self) -> bool:
        """Check if active key is a Google Gemini API key."""
        k = self.api_key
        if not k:
            return False
        return k.startswith(("AQ.", "AIza", "gemini"))

    @property
    def default_model(self) -> str:
        """Default model identifier based on key type and configuration."""
        if self._custom_model:
            return self._custom_model
        if self.is_gemini:
            return "gemini-3.5-flash-lite"
        return settings.ben_model

    @default_model.setter
    def default_model(self, value: str) -> None:
        self._custom_model = value

    def _gemini_model(self, requested_model: Optional[str] = None) -> str:
        m = requested_model or self.default_model
        if m and m.startswith("gemini-"):
            return m
        return "gemini-3.5-flash-lite"

    @property
    def is_configured(self) -> bool:
        """Check if a valid, non-empty API key is present."""
        k = self.api_key
        return bool(k and k.strip())

    @property
    def key_char_count(self) -> int:
        """Return the character count of the API key without revealing the key."""
        k = self.api_key
        return len(k.strip()) if k and self.is_configured else 0

    @property
    def client(self) -> Anthropic:
        """Get or initialize the official anthropic.Anthropic client."""
        if not self.is_configured:
            raise ValueError(
                "Anthropic API key is not configured. Please set ANTHROPIC_API_KEY in your environment variables."
            )
        if self._client is None:
            kwargs: Dict[str, Any] = {
                "api_key": self.api_key,
                "max_retries": 3,
                "timeout": 30.0,
            }
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = Anthropic(**kwargs)
        return self._client

    def test_connection(
        self,
        prompt: str = "BEN diagnostic check: confirm your operational readiness in one sentence."
    ) -> ClaudeConnectionResult:
        """Test API connectivity by sending a lightweight probe to Claude or Gemini."""
        if not self.is_configured:
            return ClaudeConnectionResult(
                success=False,
                model=self.default_model,
                char_count=0,
                error="No valid Anthropic API key provided. Set ANTHROPIC_API_KEY in the .env file."
            )

        start_time = time.perf_counter()
        char_count = self.key_char_count

        if self.is_gemini:
            target_model = self._gemini_model()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={self._api_key}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 100}
            }
            try:
                import requests
                logger.info(f"Testing Gemini connection with model: [cyan]{target_model}[/cyan]...")
                r = requests.post(url, json=payload, timeout=15)
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                if r.status_code == 200:
                    data = r.json()
                    candidates = data.get("candidates", [])
                    txt = ""
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        txt = "".join(p.get("text", "") for p in parts)
                    logger.info(f"Gemini connection verified! Latency: {elapsed_ms:.1f}ms")
                    return ClaudeConnectionResult(
                        success=True,
                        model=target_model,
                        response=txt.strip() or "BEN operational via Gemini.",
                        latency_ms=round(elapsed_ms, 2),
                        char_count=char_count,
                    )
                else:
                    clean_err = redact_sensitive(r.text, self._api_key)
                    return ClaudeConnectionResult(
                        success=False,
                        model=target_model,
                        error=f"Gemini Error ({r.status_code}): {clean_err[:180]}",
                        latency_ms=round(elapsed_ms, 2),
                        char_count=char_count,
                    )
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                clean_err = redact_sensitive(str(e), self._api_key)
                return ClaudeConnectionResult(
                    success=False,
                    model=target_model,
                    error=f"Gemini connection failed: {clean_err}",
                    latency_ms=round(elapsed_ms, 2),
                    char_count=char_count,
                )

        try:
            logger.info(f"Testing Claude connection with model: [cyan]{self.default_model}[/cyan]...")
            response = self.client.messages.create(
                model=self.default_model,
                max_tokens=150,
                extra_body={"temperature": 0.3},
                system=settings.ben_system_instruction,
                messages=[{"role": "user", "content": prompt}],
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0

            text_output = ""
            if response and response.content:
                text_output = response.content[0].text if hasattr(response.content[0], "text") else str(response.content[0])

            logger.info(f"Claude connection verified! Latency: {elapsed_ms:.1f}ms")

            return ClaudeConnectionResult(
                success=True,
                model=self.default_model,
                response=text_output.strip() if text_output else "No content returned.",
                latency_ms=round(elapsed_ms, 2),
                char_count=char_count
            )

        except AuthenticationError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            clean_msg = redact_sensitive(str(e), self._api_key)
            error_msg = f"Anthropic Authentication Error (code 401): {clean_msg}"
            logger.error(f"[red]{error_msg}[/red]")
            return ClaudeConnectionResult(
                success=False,
                model=self.default_model,
                error=error_msg,
                latency_ms=round(elapsed_ms, 2),
                char_count=char_count
            )
        except RateLimitError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            clean_msg = redact_sensitive(str(e), self._api_key)
            error_msg = f"Anthropic Rate Limit Error (code 429): {clean_msg}"
            logger.error(f"[red]{error_msg}[/red]")
            return ClaudeConnectionResult(
                success=False,
                model=self.default_model,
                error=error_msg,
                latency_ms=round(elapsed_ms, 2),
                char_count=char_count
            )
        except APIError as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            clean_msg = redact_sensitive(str(e), self._api_key)
            code = getattr(e, 'status_code', 'unknown')
            error_msg = f"Anthropic API Error (code {code}): {clean_msg}"
            logger.error(f"[red]{error_msg}[/red]")
            return ClaudeConnectionResult(
                success=False,
                model=self.default_model,
                error=error_msg,
                latency_ms=round(elapsed_ms, 2),
                char_count=char_count
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            clean_msg = redact_sensitive(str(e), self._api_key)
            error_msg = f"Connection failed: {clean_msg}"
            logger.error(f"[red]{error_msg}[/red]")
            return ClaudeConnectionResult(
                success=False,
                model=self.default_model,
                error=error_msg,
                latency_ms=round(elapsed_ms, 2),
                char_count=char_count
            )

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        history_override: Optional[List[dict]] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """Generate a complete text response using Claude or Gemini."""
        if self.is_gemini:
            import requests
            target_model = self._gemini_model(model)
            sys_instruction = system_instruction or settings.ben_system_instruction
            contents = []
            if history_override:
                for m in history_override:
                    r = "model" if m.get("role") in ("assistant", "ben") else "user"
                    contents.append({"role": r, "parts": [{"text": m.get("content", "")}]})
            if not contents or contents[-1].get("role") != "user" or contents[-1]["parts"][0]["text"] != prompt:
                contents.append({"role": "user", "parts": [{"text": prompt}]})

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={self._api_key}"
            payload = {
                "systemInstruction": {"parts": [{"text": sys_instruction}]},
                "contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
            }
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 200:
                data = r.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    return "".join(p.get("text", "") for p in parts)
            clean_err = redact_sensitive(r.text, self._api_key)
            raise RuntimeError(f"Gemini API error (HTTP {r.status_code}): {clean_err[:180]}")

        target_model = model or self.default_model
        sys_instruction = system_instruction or settings.ben_system_instruction

        if history_override:
            messages = list(history_override)
            if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != prompt:
                messages.append({"role": "user", "content": prompt})
        else:
            messages = [{"role": "user", "content": prompt}]

        extra_body = {"temperature": temperature} if temperature is not None else None
        response = self.client.messages.create(
            model=target_model,
            max_tokens=max_tokens,
            extra_body=extra_body,
            system=sys_instruction,
            messages=messages,
        )

        if response and response.content:
            first_block = response.content[0]
            return first_block.text if hasattr(first_block, "text") else str(first_block)
        return ""

    def generate_stream(
        self,
        prompt: str,
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        history_override: Optional[List[dict]] = None,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """Stream chunks of response text using Anthropic or Gemini."""
        if self.is_gemini:
            import json, requests
            target_model = self._gemini_model(model)
            sys_instruction = system_instruction or settings.ben_system_instruction
            contents = []
            if history_override:
                for m in history_override:
                    r = "model" if m.get("role") in ("assistant", "ben") else "user"
                    contents.append({"role": r, "parts": [{"text": m.get("content", "")}]})
            if not contents or contents[-1].get("role") != "user" or contents[-1]["parts"][0]["text"] != prompt:
                contents.append({"role": "user", "parts": [{"text": prompt}]})

            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:streamGenerateContent?alt=sse&key={self._api_key}"
            payload = {
                "systemInstruction": {"parts": [{"text": sys_instruction}]},
                "contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
            }
            r = requests.post(url, json=payload, stream=True, timeout=30)
            if r.status_code != 200:
                clean_err = redact_sensitive(r.text, self._api_key)
                raise RuntimeError(f"Gemini stream error (HTTP {r.status_code}): {clean_err[:180]}")
            for line in r.iter_lines():
                if line and line.startswith(b"data: "):
                    try:
                        data = json.loads(line[6:])
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for p in parts:
                                if "text" in p and p["text"]:
                                    yield p["text"]
                    except Exception:
                        continue
            return
        target_model = model or self.default_model
        sys_instruction = system_instruction or settings.ben_system_instruction

        if history_override:
            # Build alternating message list with new user prompt
            messages = list(history_override)
            if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != prompt:
                messages.append({"role": "user", "content": prompt})
        else:
            messages = [{"role": "user", "content": prompt}]

        extra_body = {"temperature": temperature} if temperature is not None else None
        with self.client.messages.stream(
            model=target_model,
            system=sys_instruction,
            max_tokens=max_tokens,
            extra_body=extra_body,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text

    def list_models(self) -> List[str]:
        """Return official supported Anthropic Claude models."""
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ]


# Backward-compatible aliases
ClaudeClientManager = AnthropicClientManager
GeminiClientManager = AnthropicClientManager
