"""Unit tests for image OCR + PII detection."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_ocr_extracts_text(anyio_backend):
    """OCR extracts text from image bytes."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", True), \
         patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Hello World 123-45-6789"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.extract_text(b"fake_png_bytes")
        assert result == "Hello World 123-45-6789"


@pytest.mark.anyio
async def test_ocr_too_large_skipped(anyio_backend):
    """Images larger than max_size_mb are skipped."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=1)
    # 2MB image
    result = await analyzer.extract_text(b"x" * (2 * 1024 * 1024))
    assert result is None


@pytest.mark.anyio
async def test_ocr_with_pii(anyio_backend):
    """OCR text with SSN triggers PII detection."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", True), \
         patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Patient SSN: 123-45-6789"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is True
        assert result["ocr_pii_found"] is True
        assert "ssn" in result["ocr_pii_types"]


@pytest.mark.anyio
async def test_ocr_clean_text(anyio_backend):
    """OCR text without PII returns clean result."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", True), \
         patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Hello World"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is True
        assert result["ocr_pii_found"] is False
        assert result["ocr_pii_types"] == []


@pytest.mark.anyio
async def test_ocr_tesseract_missing_fail_open(anyio_backend):
    """Missing Tesseract returns graceful result."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", False):
        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is False
        assert result["ocr_pii_found"] is False


@pytest.mark.anyio
async def test_ocr_with_credit_card(anyio_backend):
    """Credit card in image triggers BLOCK-level PII."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", True), \
         patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Card: 4111-1111-1111-1111"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_pii_found"] is True
        assert "credit_card" in result["ocr_pii_types"]
        assert result["ocr_pii_block"] is True
