"""Tests for threshold Web Push notifications.

Every collaborator is injected: a fake sender, a fake writer and a fake clock.
Nothing in this file opens a socket or touches a real push service.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

from app.models import JobSource, NormalizedJob
from app.push import (
    EXPIRED,
    FAILED,
    SENT,
    PushConfig,
    PushDeliveryError,
    WebPushNotifier,
    deliver_push_notifications,
    subscription_info,
)
from app.scoring import ScoreBreakdown
from app.worker import (
    RunResult,
    ScoredJob,
    SupabaseWriter,
    _send_notifications,
    build_notifier,
)

NOW = datetime(2026, 11, 12, 14, 30, tzinfo=timezone.utc)

SUBSCRIPTION: dict[str, str] = {
    "endpoint": "https://push.example.com/v1/abc",
    "p256dh": "p256dh-key",
    "auth": "auth-key",
}


def make_job(
    *,
    company: str = "BairesDev",
    title: str = "Desarrollador Junior React",
    location: str = "Buenos Aires, AR",
    url: str = "https://www.linkedin.com/jobs/view/4489030478",
) -> NormalizedJob:
    return NormalizedJob(
        fingerprint="fp-1",
        loose_key="lk-1",
        company=company,
        title=title,
        location=location,
        is_remote=True,
        description="",
        posted_at=None,
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        job_type=None,
        sources=[JobSource(source="linkedin", url=url)],
    )


def make_scored(total: int = 90, *, job_id: str = "job-1", **kwargs: Any) -> ScoredJob:
    breakdown = ScoreBreakdown(
        parts={
            "stack": 35,
            "seniority": 25,
            "experience": 15,
            "location": 8,
            "recency": 7,
        },
        total=total,
        matched_skills=("react",),
        seniority_level="junior",
        required_years=None,
    )
    return ScoredJob(job=make_job(**kwargs), breakdown=breakdown, job_id=job_id)


class FakeSender:
    """Records every push and answers with a fixed outcome."""

    def __init__(self, *, status: int | None = None) -> None:
        self.status = status
        self.calls: list[tuple[Mapping[str, Any], str]] = []

    def __call__(self, subscription: Mapping[str, Any], payload: str) -> None:
        self.calls.append((subscription, payload))
        if self.status is not None:
            raise PushDeliveryError("rejected", status=self.status)

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(payload) for _, payload in self.calls]


class FakeWriter:
    """Stands in for the database side of notification bookkeeping."""

    def __init__(
        self,
        *,
        already: Mapping[str, str] | None = None,
        fail_stamp: bool = False,
        subscriptions: list[dict[str, str]] | None = None,
    ) -> None:
        self.already = dict(already or {})
        self.fail_stamp = fail_stamp
        self.stamped: list[tuple[str, str]] = []
        self.rows = list(subscriptions if subscriptions is not None else [dict(SUBSCRIPTION)])
        self.deleted: list[str] = []

    def notified_at(self, job_id: str) -> str | None:
        return self.already.get(job_id)

    def mark_notified(self, job_id: str, when: datetime) -> None:
        if self.fail_stamp:
            raise RuntimeError("stamp failed")
        self.stamped.append((job_id, when.isoformat()))

    def push_subscriptions(self) -> list[dict[str, str]]:
        return list(self.rows)

    def delete_push_subscription(self, endpoint: str) -> None:
        self.deleted.append(endpoint)


def make_notifier(sender: FakeSender | None = None) -> WebPushNotifier:
    return WebPushNotifier(
        PushConfig(vapid_private_key="test-key"), sender=sender or FakeSender()
    )


class TestPushConfig:
    def test_a_missing_private_key_disables_notifications(self) -> None:
        assert PushConfig.from_env({}) is None
        assert PushConfig.from_env({"VAPID_PRIVATE_KEY": "   "}) is None

    def test_the_private_key_enables_notifications(self) -> None:
        config = PushConfig.from_env({"VAPID_PRIVATE_KEY": "abc"})
        assert config is not None
        assert config.vapid_private_key == "abc"

    def test_the_subject_defaults_when_unset(self) -> None:
        config = PushConfig.from_env({"VAPID_PRIVATE_KEY": "abc"})
        assert config is not None
        assert config.vapid_subject.startswith("mailto:")

    def test_a_configured_subject_is_kept(self) -> None:
        config = PushConfig.from_env(
            {"VAPID_PRIVATE_KEY": "abc", "VAPID_SUBJECT": "mailto:me@example.com"}
        )
        assert config is not None
        assert config.vapid_subject == "mailto:me@example.com"


class TestSubscriptionShape:
    def test_a_stored_row_becomes_a_pywebpush_mapping(self) -> None:
        assert subscription_info(SUBSCRIPTION) == {
            "endpoint": "https://push.example.com/v1/abc",
            "keys": {"p256dh": "p256dh-key", "auth": "auth-key"},
        }


class TestWebPushNotifier:
    def test_a_delivered_push_reports_sent(self) -> None:
        sender = FakeSender()
        notifier = make_notifier(sender)

        assert notifier.send(SUBSCRIPTION, "90/100 · BairesDev", "Junior React") == SENT
        assert len(sender.calls) == 1

    def test_the_payload_carries_title_message_and_click(self) -> None:
        sender = FakeSender()
        make_notifier(sender).send(
            SUBSCRIPTION, "90/100 · BairesDev", "Junior React", click="https://x.test/1"
        )

        payload = sender.payloads()[0]
        assert payload["title"] == "90/100 · BairesDev"
        assert payload["message"] == "Junior React"
        assert payload["click"] == "https://x.test/1"

    def test_non_ascii_text_survives_the_round_trip(self) -> None:
        sender = FakeSender()
        make_notifier(sender).send(SUBSCRIPTION, "Analista", "Córdoba · Señor")

        payload = sender.payloads()[0]
        assert payload["message"] == "Córdoba · Señor"

    def test_a_gone_subscription_reports_expired(self) -> None:
        notifier = make_notifier(FakeSender(status=410))
        assert notifier.send(SUBSCRIPTION, "t", "m") == EXPIRED

    def test_a_rejected_push_reports_failed(self) -> None:
        notifier = make_notifier(FakeSender(status=413))
        assert notifier.send(SUBSCRIPTION, "t", "m") == FAILED

    def test_a_broken_sender_reports_failed_without_raising(self) -> None:
        def explode(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("network down")

        notifier = WebPushNotifier(PushConfig(vapid_private_key="k"), sender=explode)
        assert notifier.send(SUBSCRIPTION, "t", "m") == FAILED


class TestDeliverPushNotifications:
    def test_only_offers_at_or_above_the_threshold_are_delivered(self) -> None:
        sender = FakeSender()
        report = deliver_push_notifications(
            [make_scored(85), make_scored(84, job_id="job-2")],
            notifier=make_notifier(sender),
            writer=FakeWriter(),
            now=NOW,
        )

        assert report.sent == 1
        assert len(sender.calls) == 1

    def test_the_message_names_the_score_company_and_title(self) -> None:
        sender = FakeSender()
        deliver_push_notifications(
            [make_scored(92, company="Mercado Libre", title="Frontend SSR")],
            notifier=make_notifier(sender),
            writer=FakeWriter(),
            now=NOW,
        )

        payload = sender.payloads()[0]
        assert "92" in payload["title"]
        assert "Mercado Libre" in payload["title"]
        assert "Frontend SSR" in payload["message"]
        assert payload["click"] == "https://www.linkedin.com/jobs/view/4489030478"

    def test_an_offer_already_notified_is_skipped(self) -> None:
        sender = FakeSender()
        writer = FakeWriter(already={"job-1": "2026-11-12T10:00:00+00:00"})

        report = deliver_push_notifications(
            [make_scored(95)], notifier=make_notifier(sender), writer=writer, now=NOW
        )

        assert report.sent == 0
        assert report.skipped == 1
        assert sender.calls == []

    def test_a_delivered_offer_is_stamped_so_the_next_run_skips_it(self) -> None:
        writer = FakeWriter()
        deliver_push_notifications(
            [make_scored(95)], notifier=make_notifier(), writer=writer, now=NOW
        )

        assert writer.stamped == [("job-1", NOW.isoformat())]

    def test_a_failed_delivery_is_not_stamped(self) -> None:
        writer = FakeWriter()
        report = deliver_push_notifications(
            [make_scored(95)],
            notifier=make_notifier(FakeSender(status=503)),
            writer=writer,
            now=NOW,
        )

        assert report.failed == 1
        assert writer.stamped == []

    def test_a_failed_stamp_does_not_stop_the_remaining_offers(self) -> None:
        sender = FakeSender()
        report = deliver_push_notifications(
            [make_scored(95, job_id="job-1"), make_scored(96, job_id="job-2")],
            notifier=make_notifier(sender),
            writer=FakeWriter(fail_stamp=True),
            now=NOW,
        )

        assert report.sent == 2
        assert len(sender.calls) == 2

    def test_without_a_notifier_nothing_is_sent(self) -> None:
        report = deliver_push_notifications(
            [make_scored(95)], notifier=None, writer=FakeWriter(), now=NOW
        )

        assert report.sent == 0
        assert report.skipped == 1

    def test_without_any_subscription_nothing_is_sent_or_stamped(self) -> None:
        sender = FakeSender()
        writer = FakeWriter(subscriptions=[])

        report = deliver_push_notifications(
            [make_scored(95)], notifier=make_notifier(sender), writer=writer, now=NOW
        )

        assert report.sent == 0
        assert report.skipped == 1
        assert sender.calls == []
        assert writer.stamped == []

    def test_every_subscribed_device_receives_the_offer(self) -> None:
        sender = FakeSender()
        second = dict(SUBSCRIPTION, endpoint="https://push.example.com/v1/def")
        writer = FakeWriter(subscriptions=[dict(SUBSCRIPTION), second])

        report = deliver_push_notifications(
            [make_scored(95)], notifier=make_notifier(sender), writer=writer, now=NOW
        )

        assert report.sent == 1
        assert len(sender.calls) == 2

    def test_an_expired_subscription_is_deleted_and_does_not_block_delivery(self) -> None:
        class ExpiringSender(FakeSender):
            def __call__(self, subscription: Mapping[str, Any], payload: str) -> None:
                if str(subscription["endpoint"]).endswith("abc"):
                    raise PushDeliveryError("gone", status=410)
                super().__call__(subscription, payload)

        second = dict(SUBSCRIPTION, endpoint="https://push.example.com/v1/def")
        writer = FakeWriter(subscriptions=[dict(SUBSCRIPTION), second])

        report = deliver_push_notifications(
            [make_scored(95)], notifier=make_notifier(ExpiringSender()), writer=writer, now=NOW
        )

        assert report.sent == 1
        assert report.expired == 1
        assert writer.deleted == ["https://push.example.com/v1/abc"]
        assert writer.stamped == [("job-1", NOW.isoformat())]

    def test_a_write_failure_while_forgetting_an_endpoint_is_swallowed(self) -> None:
        class BrokenDeleteWriter(FakeWriter):
            def delete_push_subscription(self, endpoint: str) -> None:
                raise RuntimeError("database is down")

        class ExpiringSender(FakeSender):
            def __call__(self, subscription: Mapping[str, Any], payload: str) -> None:
                raise PushDeliveryError("gone", status=404)

        report = deliver_push_notifications(
            [make_scored(95)],
            notifier=make_notifier(ExpiringSender()),
            writer=BrokenDeleteWriter(),
            now=NOW,
        )

        assert report.sent == 0
        assert report.failed == 1
        assert report.expired == 1


class TestWorkerWiring:
    """The joiner between ``run_collection`` and this module."""

    def test_a_missing_private_key_switches_notifications_off(self) -> None:
        assert build_notifier({}) is None
        assert build_notifier({"VAPID_PRIVATE_KEY": "   "}) is None

    def test_a_configured_private_key_builds_a_notifier(self) -> None:
        notifier = build_notifier({"VAPID_PRIVATE_KEY": "abc"})
        assert notifier is not None
        assert notifier.config.vapid_private_key == "abc"

    def test_a_broken_notification_pass_never_raises(self) -> None:
        class ExplodingWriter(FakeWriter):
            def notified_at(self, job_id: str) -> str | None:
                raise RuntimeError("database is down")

        result = RunResult(notify=[make_scored(90)])

        _send_notifications(result, notifier=make_notifier(), writer=ExplodingWriter())

    def test_the_pass_stamps_only_the_offers_it_sent(self) -> None:
        writer = FakeWriter()
        result = RunResult(notify=[make_scored(90, job_id="job-1")])

        _send_notifications(result, notifier=make_notifier(), writer=writer)

        assert [job_id for job_id, _ in writer.stamped] == ["job-1"]


class TestSubscriptionStorage:
    def make_writer(self, transport: FakeTransport) -> SupabaseWriter:
        return SupabaseWriter(
            "https://proj.supabase.co", "sb_secret_test", transport=transport
        )

    def test_subscriptions_are_read_with_the_server_key(self) -> None:
        body = b'[{"endpoint": "https://push.example.com/v1/abc", "p256dh": "k", "auth": "a"}]'
        transport = FakeTransport(200, body)
        writer = self.make_writer(transport)

        rows = writer.push_subscriptions()

        assert rows == [
            {"endpoint": "https://push.example.com/v1/abc", "p256dh": "k", "auth": "a"}
        ]
        call = transport.calls[-1]
        assert call["method"] == "GET"
        assert "/rest/v1/push_subscriptions" in call["url"]
        assert "p256dh" in call["url"]

    def test_an_empty_subscription_list_is_read_as_an_empty_list(self) -> None:
        writer = self.make_writer(FakeTransport(200, b"[]"))
        assert writer.push_subscriptions() == []

    def test_deleting_a_subscription_escapes_the_endpoint(self) -> None:
        transport = FakeTransport(204)
        writer = self.make_writer(transport)

        writer.delete_push_subscription("https://push.example.com/v1/a?b=c&d=e")

        call = transport.calls[0]
        assert call["method"] == "DELETE"
        assert "endpoint=eq.https%3A%2F%2Fpush.example.com%2Fv1%2Fa%3Fb%3Dc%26d%3De" in call["url"]


class FakeTransport:
    """Records every request and answers with a fixed status."""

    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self.body = body
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        *,
        timeout: float = 10.0,
    ) -> tuple[int, bytes]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body}
        )
        return self.status, self.body
