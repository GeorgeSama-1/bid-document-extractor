from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Callable

from bid_knowledge.schemas.models import OCRBlock, OCRResult
from bid_knowledge.utils.io_utils import write_json


def _image_to_data_uri(image_path: str | Path) -> str:
    raw = Path(image_path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _extract_response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return content or ""


def _parse_text_to_blocks(text: str) -> list[OCRBlock]:
    if not text.strip():
        return []
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and isinstance(payload.get("blocks"), list):
            return [OCRBlock(**block) for block in payload["blocks"]]
        if isinstance(payload, list):
            return [OCRBlock(**block) for block in payload if isinstance(block, dict)]
    except json.JSONDecodeError:
        pass

    return [
        OCRBlock(text=line.strip(), bbox=[], confidence=None, block_type="ocr_text")
        for line in text.splitlines()
        if line.strip()
    ]


def ocr_page_image(
    image_path: str | Path,
    page_no: int,
    endpoint: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    request_timeout: int = 120,
    extra_payload: dict[str, Any] | None = None,
) -> OCRResult:
    endpoint = endpoint or os.getenv("OCR_ENDPOINT")
    model = model or os.getenv("OCR_MODEL")
    api_key = api_key or os.getenv("OCR_API_KEY")

    if not endpoint or not model:
        return OCRResult(
            page_no=page_no,
            image_path=str(image_path),
            blocks=[],
            raw_response=None,
            error="OCR endpoint or model is not configured.",
        )

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency-driven
        return OCRResult(
            page_no=page_no,
            image_path=str(image_path),
            blocks=[],
            raw_response=None,
            error=f"requests not installed: {exc}",
        )

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You are an OCR service. Return JSON: {\"blocks\":[{\"text\":\"...\",\"bbox\":[x1,y1,x2,y2],\"confidence\":0.99,\"block_type\":\"ocr_text\"}]}",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别图像中的文本，并返回上述 JSON 结构。"},
                    {"type": "image_url", "image_url": {"url": _image_to_data_uri(image_path)}},
                ],
            },
        ],
        "temperature": 0,
    }
    if extra_payload:
        payload.update(extra_payload)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=request_timeout)
        response.raise_for_status()
        raw_payload = response.json()
        text = _extract_response_text(raw_payload)
        blocks = _parse_text_to_blocks(text)
        return OCRResult(
            page_no=page_no,
            image_path=str(image_path),
            blocks=blocks,
            raw_response=raw_payload,
            error=None,
        )
    except Exception as exc:  # pragma: no cover - network-driven
        return OCRResult(
            page_no=page_no,
            image_path=str(image_path),
            blocks=[],
            raw_response=None,
            error=str(exc),
        )


def run_ocr(
    page_images: list[dict[str, Any]],
    endpoint: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    out_path: str | Path | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[OCRResult]:
    results: list[OCRResult] = []
    total_pages = len(page_images)
    for index, item in enumerate(page_images, start=1):
        results.append(
            ocr_page_image(
                image_path=item["image_path"],
                page_no=int(item["page_no"]),
                endpoint=endpoint,
                model=model,
                api_key=api_key,
            )
        )
        if progress_callback:
            progress_callback(index, total_pages)
    if out_path:
        write_json(out_path, results)
    return results
