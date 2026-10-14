"""The public error envelope shared by views and HTTP boundary protection."""

from django.http import JsonResponse


def api_error(code: str, message: str, status: int, request_id: str) -> JsonResponse:
    return JsonResponse(
        {"error": {"code": code, "message": message, "request_id": request_id}},
        status=status,
    )
