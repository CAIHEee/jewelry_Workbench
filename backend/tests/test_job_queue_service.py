import asyncio
from datetime import datetime, timezone

from fastapi import HTTPException

from app.api.v1.routes import ai_jobs
from app.models.generation_job import GenerationJob
from app.models.user import User
from app.services.job_queue_service import JobQueueService


def test_job_status_exposes_qwen_prompt_stage() -> None:
    service = JobQueueService()
    job = GenerationJob(
        id="job-qwen-stage",
        user_id="user-1",
        queue_name="test",
        rq_job_id="rq-job-qwen-stage",
        feature_key="multi_view",
        model="gpt-image-2-all-apiyi",
        prompt="生成多视图",
        status="running",
        result_json='{"stage":"qwen_prompt"}',
        created_at=datetime.now(timezone.utc),
    )

    response = service._to_schema(job)

    assert response.status == "running"
    assert response.stage == "qwen_prompt"
    assert response.message == "反推模型正在分析原图并生成提示词。"
    assert response.result is None


def test_job_error_formats_upstream_insufficient_balance_code() -> None:
    service = JobQueueService()
    error = HTTPException(
        status_code=502,
        detail={
            "upstream_status": 400,
            "upstream_response": {
                "error": {
                    "code": "insufficient_balance",
                    "message": "Your account balance is not enough.",
                }
            },
        },
    )

    assert service._format_exception_message(error) == "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。"


def test_job_error_formats_upstream_payment_status_as_balance_error() -> None:
    service = JobQueueService()
    error = HTTPException(
        status_code=502,
        detail={
            "upstream_status": 402,
            "upstream_response": {"message": "Payment required"},
        },
    )

    assert service._format_exception_message(error) == "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。"


def test_job_error_formats_upstream_chinese_credit_message_as_balance_error() -> None:
    service = JobQueueService()
    error = HTTPException(
        status_code=502,
        detail={
            "upstream_status": 500,
            "upstream_response": {"error": {"message": "积分不足，请充值后重试"}},
        },
    )

    assert service._format_exception_message(error) == "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。"


def test_reference_job_preserves_sketch_to_realistic_feature(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_enqueue_job(self, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return {
            "job_id": "job-image-edit",
            "status": "queued",
            "feature": kwargs["feature_key"],
            "message": "queued",
        }

    monkeypatch.setattr(ai_jobs.job_service, "ensure_can_enqueue", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_jobs.job_service, "enqueue_job", fake_enqueue_job.__get__(ai_jobs.job_service))

    result = asyncio.run(
        ai_jobs.enqueue_reference_image_transform(
            image=None,
            prompt="生成多视图",
            model="gpt-image-2-all-apiyi",
            feature="sketch_to_realistic",
            source_image_url="local://source.png",
            source_image_name="source.png",
            negative_prompt=None,
            strength=0.75,
            image_size="1K",
            batch_size=1,
            current_user=User(id="user-1", username="tester", role="user"),
        )
    )

    assert result["feature"] == "sketch_to_realistic"
    assert captured["feature_key"] == "sketch_to_realistic"
    assert captured["request_payload"]["metadata"]["feature"] == "sketch_to_realistic"


def test_reference_job_rejects_unsupported_feature() -> None:
    try:
        asyncio.run(
            ai_jobs.enqueue_reference_image_transform(
                image=None,
                prompt="生成多视图",
                model="gpt-image-2-all-apiyi",
                feature="multi_view",
                source_image_url="local://source.png",
                source_image_name="source.png",
                negative_prompt=None,
                strength=0.75,
                image_size="1K",
                batch_size=1,
                current_user=User(id="user-1", username="tester", role="user"),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Unsupported reference image transform feature."
    else:
        raise AssertionError("Expected unsupported feature to be rejected.")
