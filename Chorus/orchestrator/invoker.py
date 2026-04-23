from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
from urllib.parse import urlparse

import anyio
import httpx
import sniffio

from orchestrator.broadcast_completions import normalize_completion_url
from orchestrator.demo_agent import is_demo_completion_base, invoke_demo_completion
from orchestrator.models import CompletionResult, JobRecord, SlotRegistration

logger = logging.getLogger(__name__)

OLLAMA_MODEL = "qwen2.5:0.5b"


class AgentInvoker:
    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self.allowed_hosts = _parse_host_allowlist(
            os.getenv("ORC_ALLOWED_HOSTS", "").strip()
        )
        self.max_tokens = int(os.getenv("ORC_MAX_TOKENS", "512"))
        self.temperature = float(os.getenv("ORC_TEMPERATURE", "0.7"))
        self.allow_local = os.getenv("ORC_ALLOW_LOCALHOST", "1").strip() == "1"
        self.completion_model = OLLAMA_MODEL
        # Key by (url, async-library) so a semaphore created on one backend
        # (e.g. asyncio test variant) isn't reused by another (trio variant).
        self._per_peer_sem: dict[tuple[str, str], anyio.Semaphore] = {}
        self._peer_concurrency = max(1, int(os.getenv("ORC_PEER_CONCURRENCY", "2")))
        self._peer_wait_s = float(os.getenv("ORC_PEER_WAIT_S", "30"))

    def _get_peer_semaphore(self, url_key: str) -> anyio.Semaphore:
        try:
            backend = sniffio.current_async_library()
        except sniffio.AsyncLibraryNotFoundError:
            backend = "asyncio"
        key = (url_key, backend)
        # Dict ops are atomic - no awaits between get and set, so no lock needed.
        sem = self._per_peer_sem.get(key)
        if sem is None:
            sem = anyio.Semaphore(self._peer_concurrency)
            self._per_peer_sem[key] = sem
        return sem

    async def invoke(
        self,
        *,
        job: JobRecord,
        slot_id: str,
        registration: SlotRegistration,
        round_index: int,
        persona: str,
        context_text: str,
    ) -> CompletionResult:
        if is_demo_completion_base(str(registration.completion_base_url)):
            return await invoke_demo_completion(
                job=job,
                slot_id=slot_id,
                round_index=round_index,
                persona=persona,
                context_text=context_text,
            )

        target_url = normalize_completion_url(str(registration.completion_base_url))
        self._validate_target(target_url)

        peer_key = str(registration.completion_base_url).strip().rstrip("/")
        sem = self._get_peer_semaphore(peer_key)
        acquired = False
        try:
            with anyio.fail_after(self._peer_wait_s):
                await sem.acquire()
                acquired = True
        except TimeoutError:
            logger.warning("peer_busy: %s slot=%s job=%s", peer_key, slot_id, job.job_id)
            return CompletionResult(ok=False, error="peer_busy", latency_ms=0)

        try:
            headers = {
                "Content-Type": "application/json",
                "X-Chorus-Job-Id": job.job_id,
                "X-Chorus-Slot-Id": slot_id,
                "X-Chorus-Round": str(round_index),
            }
            if registration.bearer_token:
                headers["Authorization"] = f"Bearer {registration.bearer_token}"

            completion_model = (
                (registration.model_id or "").strip()
                or (job.spec.completion_model or "").strip()
                or self.completion_model
            )
            payload = {
                "model": completion_model,
                "messages": [
                    {"role": "system", "content": persona + "\n\nRespond in 2-3 sentences. Be direct and specific. Do not repeat the prompt, list instructions, or explain your reasoning process."},
                    {
                        "role": "user",
                        "content": (
                            "### Context\n"
                            f"{context_text}\n\n"
                            "### Prompt\n"
                            f"{job.spec.prompt}\n"
                        ),
                    },
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "user": f"{job.job_id}:{slot_id}",
            }

            start = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                    response = await client.post(target_url, headers=headers, json=payload)
                latency_ms = int((time.perf_counter() - start) * 1000)
                if response.status_code // 100 != 2:
                    return CompletionResult(
                        ok=False,
                        error=f"http_{response.status_code}",
                        latency_ms=latency_ms,
                    )
                data = response.json()
                text = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content")
                )
                finish_reason = data.get("choices", [{}])[0].get("finish_reason")
                return CompletionResult(
                    ok=text is not None,
                    text=text,
                    finish_reason=finish_reason,
                    error=None if text else "missing_content",
                    latency_ms=latency_ms,
                )
            except Exception as exc:  # noqa: BLE001
                latency_ms = int((time.perf_counter() - start) * 1000)
                logger.exception("invoker error for %s slot=%s: %s", peer_key, slot_id, exc)
                return CompletionResult(
                    ok=False,
                    error=str(exc),
                    latency_ms=latency_ms,
                )
        finally:
            if acquired:
                sem.release()

    def _validate_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http/https URLs are allowed")
        host = parsed.hostname
        if host is None:
            raise ValueError("missing host in completion URL")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise ValueError("host not in allowlist")
        for ip in _resolve_host_ips(host):
            if ip.is_multicast or ip.is_unspecified or ip.is_link_local:
                raise ValueError("host blocked by SSRF guard")
            if ip.is_loopback:
                if not self.allow_local:
                    raise ValueError("host blocked by SSRF guard")
                continue
            if ip.is_private and not self.allow_local:
                raise ValueError("host blocked by SSRF guard")
            if ip.is_reserved:
                raise ValueError("host blocked by SSRF guard")

def _parse_host_allowlist(raw: str) -> set[str]:
    if not raw:
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


def _resolve_host_ips(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"host {host!r} could not be resolved") from exc
    out: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            out.append(ipaddress.ip_address(addr.split("%", 1)[0]))
        except ValueError:
            continue
    if not out:
        raise ValueError(f"host {host!r} could not be resolved")
    return out
