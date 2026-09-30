"""Shared retry policy for a connector's actual network call - transient failures (timeouts,
connection resets, 5xx handled by the caller re-raising as RequestException) get a few quick
retries; anything the caller already turned into a ConnectorError (bad auth, malformed
response) is a definite outcome, not retried. `tenacity` is already a project dependency
(used elsewhere for training-job resilience)."""
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

network_retry = retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
