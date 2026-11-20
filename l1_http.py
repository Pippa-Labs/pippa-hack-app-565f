"""Shared JSON GET for every level 1 module: one seam, one timeout, one error map."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from pippa_agentapp_sdk import AgentAppError, RateLimitedError, ServiceUnavailableError

USER_AGENT = "pippa-agent-app-pocket-londoner/0.1 (https://github.com/Pippa-Labs)"
DEFAULT_TIMEOUT = 6.0


# --- Errors the app raises about its own subject ---
class NotFound(AgentAppError):
    """The service had nothing for what was asked."""

    user_message = "I could not find that in London's open data."
    retryable = False


class NeedsInput(AgentAppError):
    """The message did not say enough to answer."""

    user_message = "I need a little more detail to answer that."
    retryable = False


class Blocked(AgentAppError):
    """The service refused this app outright (for example a Cloudflare 403)."""

    user_message = "That live data is unavailable right now."
    retryable = False


def refuse(error_class: type[AgentAppError], user_message: str, internal: str) -> AgentAppError:
    """Build an error whose user-facing sentence names the thing that went wrong."""
    error = error_class(internal)
    error.user_message = user_message
    return error


# --- Fetch seam (tests replace this one function) ---
def fetch_bytes(url: str, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    """Every outbound request in the app goes through here."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# --- JSON GET with errors mapped to SDK types ---
def get_json(url: str, service: str, timeout: float = DEFAULT_TIMEOUT):
    """GET a URL and decode JSON, turning every failure into an AgentAppError."""
    try:
        raw = fetch_bytes(url, timeout)
    except urllib.error.HTTPError as exc:  # before URLError: HTTPError is a URLError
        if exc.code in (300, 400, 404, 422):  # TfL answers 300 for an ambiguous place
            raise NotFound(f"{service} answered {exc.code}") from None
        if exc.code in (401, 403):
            raise refuse(
                Blocked, f"{service} live data is unavailable right now.", f"{service} answered {exc.code}"
            ) from None
        if exc.code == 429:
            raise RateLimitedError(
                f"{service} rate limited", retry_after_seconds=_retry_after(exc)
            ) from None
        raise ServiceUnavailableError(f"{service} answered {exc.code}") from None
    except TimeoutError:  # not a URLError, so it needs its own clause
        raise ServiceUnavailableError(f"{service} did not answer in time") from None
    except urllib.error.URLError as exc:
        raise ServiceUnavailableError(f"could not reach {service}: {exc.reason}") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise ServiceUnavailableError(f"{service} sent something that is not JSON") from None


def _retry_after(exc: urllib.error.HTTPError) -> int | None:
    """Seconds from a Retry-After header, if the service sent a number."""
    value = (exc.headers or {}).get("Retry-After", "")
    return int(value) if str(value).isdigit() else None
