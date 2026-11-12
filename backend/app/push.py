"""Threshold notifications over the Web Push protocol.

Scoring decides *when* an offer deserves an interruption (``should_notify``,
``total >= 85``); this module decides *how* it reaches the installed PWA.

The browser owns the subscription: it registers a service worker, subscribes
with the public VAPID key, and stores the endpoint plus its two encryption keys
in ``public.push_subscriptions``. The worker reads those rows with the
server-only key and pushes one encrypted message per device, signing every
request with the private VAPID key.

Nothing is sent while no device has opted in, so the pipeline behaves exactly as
before until the phone subscribes. A missing private key is the off switch, and
it must never be an error: a worker without notifications has to keep
collecting exactly as before.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

from app.scoring import should_notify

if TYPE_CHECKING:  # ``worker`` imports this module, so this stays type-only.
    from app.worker import ScoredJob

LOG = logging.getLogger("app.push")

# Keep a push for a day when the phone is offline; the offer is stored anyway.
DEFAULT_TTL = 86400
# A push service answers 404/410 once the browser dropped the subscription.
EXPIRED_STATUSES = frozenset({404, 410})

SENT = "sent"
EXPIRED = "expired"
FAILED = "failed"

# One push attempt: raises :class:`PushDeliveryError` when it cannot deliver.
Sender = Callable[[Mapping[str, Any], str], None]


class PushDeliveryError(RuntimeError):
    """One push could not be delivered, with the push service reply if any."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def expired(self) -> bool:
        return self.status in EXPIRED_STATUSES


@dataclass(frozen=True)
class PushConfig:
    """Credentials for signing. A missing private key means notifications off."""

    vapid_private_key: str
    vapid_subject: str = "mailto:noreply@localhost"
    ttl: int = DEFAULT_TTL

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "PushConfig | None":
        private_key = (env.get("VAPID_PRIVATE_KEY") or "").strip()
        if not private_key:
            return None
        subject = (env.get("VAPID_SUBJECT") or "").strip() or "mailto:noreply@localhost"
        return cls(vapid_private_key=private_key, vapid_subject=subject)


def subscription_info(row: Mapping[str, Any]) -> dict[str, Any]:
    """Reshape one stored row into the mapping ``pywebpush`` expects."""
    return {
        "endpoint": row["endpoint"],
        "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
    }


def _webpush_send(
    subscription: Mapping[str, Any],
    payload: str,
    config: PushConfig,
    timeout: float,
) -> None:
    """Sign and POST one message; converted failures carry the HTTP status.

    ``pywebpush`` is imported here so importing this module stays cheap and a
    dry run never pays for the crypto stack.
    """
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=subscription_info(subscription),
            data=payload,
            vapid_private_key=config.vapid_private_key,
            vapid_claims={"sub": config.vapid_subject},
            ttl=config.ttl,
            timeout=timeout,
        )
    except WebPushException as exc:
        raise PushDeliveryError(str(exc), status=getattr(exc, "status_code", None)) from exc


class WebPushNotifier:
    """Deliver one notification per subscribed device."""

    def __init__(
        self,
        config: PushConfig,
        *,
        sender: Sender | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self._sender = sender

    def send(
        self,
        subscription: Mapping[str, Any],
        title: str,
        message: str,
        *,
        click: str | None = None,
    ) -> str:
        """Push one message and report ``sent``, ``expired`` or ``failed``.

        Never raises: a notification must never break a collection run.
        """
        payload: dict[str, Any] = {"title": title, "message": message}
        if click:
            payload["click"] = click
        body = json.dumps(payload, ensure_ascii=False)

        try:
            self._deliver(subscription, body)
        except PushDeliveryError as exc:
            if exc.expired:
                LOG.info("push subscription is gone: %s", _tail(subscription))
                return EXPIRED
            LOG.warning("push rejected: %s", exc)
            return FAILED
        except Exception as exc:  # a notification must never break a run
            LOG.warning("push failed: %s", exc)
            return FAILED
        return SENT

    def _deliver(self, subscription: Mapping[str, Any], payload: str) -> None:
        if self._sender is not None:
            self._sender(subscription, payload)
            return
        _webpush_send(subscription, payload, self.config, self.timeout)


def _tail(subscription: Mapping[str, Any]) -> str:
    """A short, non-secret slice of the endpoint for logs."""
    endpoint = str(subscription.get("endpoint") or "")
    return endpoint[-12:] if endpoint else "(no endpoint)"


def _message_for(scored: "ScoredJob") -> tuple[str, str, str | None]:
    """Title, body and tap target for one offer."""
    job = scored.job
    title = f"{scored.breakdown.total}/100 · {job.company}"
    body = job.title
    if job.location:
        body = f"{job.title}\n{job.location}"
    click = job.sources[0].url if job.sources else None
    return title, body, click


@dataclass
class PushReport:
    """What one delivery pass did, for logging."""

    sent: int = 0
    skipped: int = 0
    failed: int = 0
    expired: int = 0


def deliver_push_notifications(
    jobs: Iterable["ScoredJob"],
    *,
    notifier: WebPushNotifier | None,
    writer: Any | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> PushReport:
    """Push every offer above the threshold that was not already delivered.

    ``notified_at`` is stamped only after at least one device accepted the push,
    so a failed delivery is retried on the next run instead of being lost.
    Stamping is best-effort: a bookkeeping failure must not swallow the
    remaining offers.
    """
    stamp = now or (clock or (lambda: datetime.now(timezone.utc)))()
    report = PushReport()
    # One read per pass, not per offer: the device list does not change mid-run.
    subscriptions: list[Mapping[str, Any]] | None = None

    for scored in jobs:
        if not should_notify(scored.breakdown.total):
            continue
        if notifier is None:
            report.skipped += 1
            continue
        if writer is not None and writer.notified_at(scored.job_id) is not None:
            report.skipped += 1
            continue
        if subscriptions is None:
            subscriptions = list(writer.push_subscriptions()) if writer is not None else []
        if not subscriptions:
            # No device opted in yet: nothing to ring, nothing to retry.
            report.skipped += 1
            continue

        title, body, click = _message_for(scored)
        delivered = False
        for subscription in list(subscriptions):
            outcome = notifier.send(subscription, title, body, click=click)
            if outcome == SENT:
                delivered = True
            elif outcome == EXPIRED:
                report.expired += 1
                subscriptions = [row for row in subscriptions if row is not subscription]
                _forget(writer, subscription)

        if not delivered:
            report.failed += 1
            continue

        report.sent += 1
        if writer is not None:
            try:
                writer.mark_notified(scored.job_id, stamp)
            except Exception as exc:  # the message is out; only bookkeeping failed
                LOG.warning("could not stamp %s as notified: %s", scored.job_id, exc)

    return report


def _forget(writer: Any | None, subscription: Mapping[str, Any]) -> None:
    """Drop a subscription the push service will keep rejecting."""
    if writer is None:
        return
    endpoint = subscription.get("endpoint")
    if not endpoint:
        return
    try:
        writer.delete_push_subscription(str(endpoint))
    except Exception as exc:  # a stale row must not break the pass
        LOG.warning("could not delete an expired subscription: %s", exc)
