from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
import httpx
from redis import Redis
from rq import Queue
from rq.job import Job
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.generation_job import GenerationJob
from app.models.user import User
from app.schemas.ai import FusionRequestMetadata, MultiViewSplitRequest, ReferenceImageRequestMetadata, TextToImageRequest
from app.schemas.jobs import GenerationJobAccepted, GenerationJobStatusResponse
from app.services.ai_service import AIService
from app.services.cache_service import get_cache_service


ACTIVE_JOB_STATUSES = ("queued", "running", "uploading")
logger = logging.getLogger(__name__)


class JobQueueService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cache_service = get_cache_service()

    def ensure_can_enqueue(self, *, current_user: User) -> None:
        limit = self.settings.queue_root_max_active_jobs if current_user.role == "root" else self.settings.queue_user_max_active_jobs
        with SessionLocal() as session:
            active_count = session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(GenerationJob.user_id == current_user.id, GenerationJob.status.in_(ACTIVE_JOB_STATUSES))
            ) or 0
        if active_count >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="当前已有任务生成中，请等待完成后再提交。",
            )

    def enqueue_job(
        self,
        *,
        current_user: User,
        feature_key: str,
        model: str | None,
        prompt: str | None,
        request_payload: dict[str, Any],
    ) -> GenerationJobAccepted:
        self.ensure_can_enqueue(current_user=current_user)
        request_hash = self._build_request_hash(
            current_user_id=current_user.id,
            feature_key=feature_key,
            model=model,
            prompt=prompt,
            request_payload=request_payload,
        )
        existing_job_id = self._find_active_dedupe_job_id(current_user_id=current_user.id, request_hash=request_hash)
        if existing_job_id:
            return GenerationJobAccepted(
                job_id=existing_job_id,
                status="queued",
                feature=feature_key,
                message="相同任务已在队列中。",
            )

        queue = self._build_queue()
        now = datetime.now(timezone.utc)
        job_id = str(uuid4())
        dedupe_key = self.cache_service.job_dedupe_key(current_user.id, request_hash)
        dedupe_reserved = self.cache_service.set_if_absent(
            dedupe_key,
            {"job_id": job_id},
            ttl_seconds=self.settings.cache_job_dedupe_ttl_seconds,
        )
        if not dedupe_reserved:
            existing_job_id = self._find_active_dedupe_job_id(current_user_id=current_user.id, request_hash=request_hash)
            if existing_job_id:
                return GenerationJobAccepted(
                    job_id=existing_job_id,
                    status="queued",
                    feature=feature_key,
                    message="相同任务已在队列中。",
                )

        with SessionLocal() as session:
            job = GenerationJob(
                id=job_id,
                user_id=current_user.id,
                queue_name=self.settings.queue_name,
                rq_job_id=job_id,
                feature_key=feature_key,
                model=model,
                prompt=prompt,
                status="queued",
                request_json=self._dump_json(request_payload),
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.commit()
            session.refresh(job)

        self._cache_job_status(job)
        self.cache_service.set_json(
            dedupe_key,
            {"job_id": job_id},
            ttl_seconds=self.settings.cache_job_dedupe_ttl_seconds,
        )

        try:
            queue.enqueue(
                "app.tasks.ai_jobs.run_generation_job",
                job_id,
                job_id=job_id,
                job_timeout=self.settings.queue_job_timeout_seconds,
                result_ttl=self.settings.queue_result_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self.mark_failed(job_id, f"任务入队失败: {exc}")
            self.cache_service.delete(dedupe_key)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"任务队列不可用: {exc}",
            ) from exc
        logger.info("job_enqueued job_id=%s feature=%s model=%s", job_id, feature_key, model)

        return GenerationJobAccepted(
            job_id=job_id,
            status="queued",
            feature=feature_key,
            message="任务已进入队列。",
        )

    def get_job(self, *, job_id: str, current_user: User) -> GenerationJobStatusResponse:
        with SessionLocal() as session:
            owner_user_id = session.scalar(select(GenerationJob.user_id).where(GenerationJob.id == job_id))
            if owner_user_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在。")
            if current_user.role != "root" and owner_user_id != current_user.id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看该任务。")

        cached = self.cache_service.get_json(self.cache_service.job_status_key(job_id))
        if cached is not None:
            try:
                cached_response = GenerationJobStatusResponse.model_validate(cached)
                if cached_response.status in {"succeeded", "failed"}:
                    return cached_response
            except Exception:  # noqa: BLE001
                pass

        self._reconcile_with_rq(job_id)

        with SessionLocal() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在。")
            session.expunge(job)
        response = self._to_schema(job)
        self.cache_service.set_json(
            self.cache_service.job_status_key(job_id),
            response.model_dump(mode="json"),
            ttl_seconds=self.settings.cache_job_status_ttl_seconds,
        )
        return response

    def mark_running(self, job_id: str) -> None:
        self._update_job(job_id, status="running", started_at=datetime.now(timezone.utc), error_message=None)

    def mark_uploading(self, job_id: str) -> None:
        self._update_job(job_id, status="uploading")

    def mark_succeeded(self, job_id: str, result: dict[str, Any]) -> None:
        completed_at = datetime.now(timezone.utc)
        self._update_job(
            job_id,
            status="succeeded",
            result_json=self._dump_json(self._compact_result(result)),
            error_message=None,
            completed_at=completed_at,
        )
        logger.info("job_succeeded job_id=%s", job_id)

    def mark_failed(self, job_id: str, error_message: str) -> None:
        completed_at = datetime.now(timezone.utc)
        model = None
        feature_key = None
        with SessionLocal() as session:
            job = session.get(GenerationJob, job_id)
            if job is not None:
                model = job.model
                feature_key = job.feature_key
        self._update_job(
            job_id,
            status="failed",
            error_message=error_message[:4000],
            completed_at=completed_at,
        )
        logger.warning("job_failed job_id=%s feature=%s model=%s error=%s", job_id, feature_key, model, error_message[:4000])

    def run_job(self, job_id: str) -> None:
        with SessionLocal() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                raise RuntimeError(f"Job {job_id} not found.")
            user = session.get(User, job.user_id)
            if user is None:
                raise RuntimeError(f"User {job.user_id} not found for job {job_id}.")
            request_payload = self._load_json(job.request_json) or {}
            session.expunge(user)

        self.mark_running(job_id)
        try:
            result = asyncio.run(self._execute_job(job_id=job_id, feature_key=job.feature_key, request_payload=request_payload, current_user=user))
        except Exception as exc:  # noqa: BLE001
            self.mark_failed(job_id, self._format_exception_message(exc))
            raise
        try:
            self.mark_succeeded(job_id, result)
        except Exception as exc:  # noqa: BLE001
            self.mark_failed(job_id, self._format_exception_message(exc))
            raise

    async def _execute_job(
        self,
        *,
        job_id: str,
        feature_key: str,
        request_payload: dict[str, Any],
        current_user: User,
    ) -> dict[str, Any]:
        service = AIService()
        stage_callback = lambda stage: self._handle_stage_callback(job_id, stage)

        if feature_key == "text_to_image":
            request = TextToImageRequest.model_validate(request_payload["request"])
            result = await service.generate_text_to_image(request, current_user=current_user, stage_callback=stage_callback)
            return result.model_dump(mode="json")

        if feature_key == "multi_image_fusion":
            metadata = FusionRequestMetadata.model_validate(request_payload["metadata"])
            result = await service.fuse_images(
                files=[],
                metadata=metadata,
                current_user=current_user,
                source_image_urls=request_payload.get("source_image_urls") or None,
                stage_callback=stage_callback,
            )
            return result.model_dump(mode="json")

        if feature_key == "multi_view_split":
            payload = MultiViewSplitRequest.model_validate(request_payload["payload"])
            result = await service.split_multi_view_image(payload, current_user=current_user, stage_callback=stage_callback)
            return result.model_dump(mode="json")

        metadata = ReferenceImageRequestMetadata.model_validate(request_payload["metadata"])
        source_image_urls = request_payload.get("source_image_urls") or []

        if feature_key == "multi_view":
            result = await service.generate_multi_view(
                file=None,
                metadata=metadata,
                current_user=current_user,
                source_image_url=source_image_urls[0] if source_image_urls else None,
                stage_callback=stage_callback,
            )
            return result.model_dump(mode="json")

        if len(source_image_urls) > 1:
            result = await service.transform_reference_images(
                files=[],
                metadata=metadata,
                current_user=current_user,
                source_image_urls=source_image_urls,
                stage_callback=stage_callback,
            )
            return result.model_dump(mode="json")

        result = await service.transform_reference_image(
            file=None,
            metadata=metadata,
            current_user=current_user,
            source_image_url=source_image_urls[0] if source_image_urls else None,
            stage_callback=stage_callback,
        )
        return result.model_dump(mode="json")

    def _handle_stage_callback(self, job_id: str, stage: str) -> None:
        if stage == "uploading":
            self.mark_uploading(job_id)

    def _to_schema(self, job: GenerationJob) -> GenerationJobStatusResponse:
        normalized_error = self._format_exception_message(job.error_message) if job.error_message else None
        result = self._load_json(job.result_json)
        return GenerationJobStatusResponse(
            job_id=job.id,
            feature=job.feature_key,
            status=job.status,  # type: ignore[arg-type]
            model=job.model,
            prompt=job.prompt,
            message=self._status_message(job.status, error_message=normalized_error),
            error_message=normalized_error,
            result=result,
            created_at=self._normalize_datetime(job.created_at),
            started_at=self._normalize_datetime(job.started_at) if job.started_at else None,
            completed_at=self._normalize_datetime(job.completed_at) if job.completed_at else None,
        )

    def _update_job(self, job_id: str, **fields: Any) -> None:
        with SessionLocal() as session:
            job = session.get(GenerationJob, job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(job)
            session.expunge(job)
        self._cache_job_status(job)

    def _build_queue(self) -> Queue:
        try:
            redis = Redis.from_url(self.settings.queue_redis_url)
            redis.ping()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Redis unavailable at {self.settings.queue_redis_url}") from exc
        return Queue(name=self.settings.queue_name, connection=redis)

    def _reconcile_with_rq(self, job_id: str) -> None:
        with SessionLocal() as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status == "succeeded":
                return

        if job.status == "failed" and job.error_message:
            return

        try:
            redis = Redis.from_url(self.settings.queue_redis_url)
            rq_job = Job.fetch(job.rq_job_id, connection=redis)
        except Exception:  # noqa: BLE001
            return

        rq_status = rq_job.get_status(refresh=True)
        if rq_status == "failed":
            message = self._format_exception_message(rq_job.exc_info or RuntimeError("任务执行失败"))
            self._update_job(
                job.id,
                status="failed",
                error_message=message[:4000],
                completed_at=datetime.now(timezone.utc),
            )
        elif rq_status in {"finished", "stopped", "canceled"} and job.status != "succeeded":
            self._update_job(
                job.id,
                status="failed",
                error_message="任务已结束，但数据库结果状态未正确落库。",
                completed_at=datetime.now(timezone.utc),
            )

    def _cache_job_status(self, job: GenerationJob) -> None:
        response = self._to_schema(job)
        self.cache_service.set_json(
            self.cache_service.job_status_key(job.id),
            response.model_dump(mode="json"),
            ttl_seconds=self.settings.cache_job_status_ttl_seconds,
        )

    def _find_active_dedupe_job_id(self, *, current_user_id: str, request_hash: str) -> str | None:
        payload = self.cache_service.get_json(self.cache_service.job_dedupe_key(current_user_id, request_hash))
        if not isinstance(payload, dict):
            return None

        job_id = payload.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            return None

        cached_job = self.cache_service.get_json(self.cache_service.job_status_key(job_id))
        if isinstance(cached_job, dict):
            status_value = cached_job.get("status")
            if status_value in ACTIVE_JOB_STATUSES:
                return job_id

        with SessionLocal() as session:
            job_status = session.scalar(select(GenerationJob.status).where(GenerationJob.id == job_id))
        if job_status in ACTIVE_JOB_STATUSES:
            return job_id

        self.cache_service.delete(self.cache_service.job_dedupe_key(current_user_id, request_hash))
        return None

    def _build_request_hash(
        self,
        *,
        current_user_id: str,
        feature_key: str,
        model: str | None,
        prompt: str | None,
        request_payload: dict[str, Any],
    ) -> str:
        payload = {
            "user_id": current_user_id,
            "feature_key": feature_key,
            "model": model,
            "prompt": prompt,
            "request_payload": request_payload,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _dump_json(self, payload: dict[str, Any] | None) -> str | None:
        if payload is None:
            return None
        return json.dumps(payload, ensure_ascii=False)

    def _load_json(self, payload: str | None) -> dict[str, Any] | None:
        if not payload:
            return None
        loaded = json.loads(payload)
        return loaded if isinstance(loaded, dict) else None

    def _compact_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        compact = dict(payload)
        if "raw_response" in compact:
            compact["raw_response"] = None
        return compact

    def _format_exception_message(self, exc: object) -> str:
        if isinstance(exc, str):
            message = exc.strip()
            if not message:
                return "任务执行失败。"
            if "Data too long for column 'result_json'" in message:
                return "任务结果写入数据库失败：result_json 字段长度不足。"
            if "ReadTimeout" in message or "httpx.ReadTimeout" in message:
                return "上游模型服务请求超时，请稍后重试。"
            if "Traceback" in message:
                lines = [line.strip() for line in message.splitlines() if line.strip()]
                if lines:
                    for line in reversed(lines):
                        if "Error:" in line or "Exception:" in line or "Timeout" in line:
                            return line[:4000]
                    return "任务执行失败。"
            return message[:4000]
        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, str) and detail.strip():
                return detail[:4000]
            normalized_detail = self._format_upstream_error_detail(detail)
            if normalized_detail:
                return normalized_detail[:4000]
            return f"HTTP {exc.status_code} 错误。"
        if isinstance(exc, httpx.TimeoutException):
            return "上游模型服务请求超时，请稍后重试。"
        if isinstance(exc, Exception):
            message = str(exc).strip()
            if message:
                return message[:4000]
            return f"{exc.__class__.__name__} occurred."
        return "任务执行失败。"

    def _format_upstream_error_detail(self, detail: Any) -> str | None:
        if not isinstance(detail, dict):
            return None
        upstream_status = detail.get("upstream_status")
        upstream_response = detail.get("upstream_response")
        message = self._extract_upstream_error_text(upstream_response)
        normalized_message = message.lower() if message else ""

        if self._is_upstream_balance_error(upstream_status, message):
            return "当前所选 AI 服务余额不足，请前往对应中转平台充值后再试。"
        if "model_not_found" in normalized_message or ("模型" in message and "不存在" in message):
            return "当前所选模型不可用，请切换模型或联系管理员检查模型配置。"
        if "invalid token" in normalized_message or "unauthorized" in normalized_message:
            return "当前所选 AI 服务认证失败，请联系管理员检查对应中转的 API Key。"
        if "timeout" in normalized_message or "超时" in message:
            return "当前所选 AI 服务处理超时，请稍后重试，或切换其他中转/模型。"
        if upstream_status == 429:
            return "当前所选 AI 服务请求过于频繁，请稍后再试。"
        if upstream_status:
            return f"上游 AI 服务返回 HTTP {upstream_status} 错误。{message}" if message else f"上游 AI 服务返回 HTTP {upstream_status} 错误。"
        return message or None

    def _extract_upstream_error_text(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                parts = [str(error.get(key)).strip() for key in ("code", "message", "type") if error.get(key)]
                if parts:
                    return " ".join(parts)
            if isinstance(error, str):
                return error.strip()
            for key in ("message", "msg", "detail", "code"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = self._extract_upstream_error_text(value)
                    if nested:
                        return nested
        return ""

    def _is_upstream_balance_error(self, upstream_status: Any, message: str) -> bool:
        normalized = message.lower()
        balance_markers = (
            "insufficient_balance",
            "insufficient balance",
            "balance not enough",
            "quota",
            "余额不足",
            "积分不足",
            "账户余额",
            "额度不足",
        )
        return upstream_status == 402 or any(marker in normalized for marker in balance_markers)

    def _status_message(self, status_value: str, *, error_message: str | None) -> str:
        mapping = {
            "queued": "任务排队中。",
            "running": "任务执行中。",
            "uploading": "结果上传与落库中。",
            "succeeded": "任务已完成。",
            "failed": error_message or "任务执行失败。",
        }
        return mapping.get(status_value, "任务状态未知。")

    def _normalize_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
