from fastapi import HTTPException

from app.services.job_queue_service import JobQueueService


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
