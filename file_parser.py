import io
import re
import unicodedata
import zipfile
from pathlib import Path

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 30_000
MAX_PDF_PAGES = 30
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024


def extract_text(content: bytes, filename: str) -> str:
    """Doc tai lieu trong thread; giu dau cau va ngat doan cho Stylometry."""
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("PDF có mật khẩu. Hãy tải bản PDF đã bỏ mật khẩu.")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError(f"PDF tối đa {MAX_PDF_PAGES} trang. Hãy chia nhỏ tài liệu.")
            parts, length = [], 0
            for page in reader.pages:
                part = page.extract_text() or ""
                length += len(part)
                if length > MAX_TEXT_CHARS:
                    raise ValueError(f"Văn bản tối đa {MAX_TEXT_CHARS:,} ký tự. Hãy chia nhỏ tài liệu.")
                parts.append(part)
            raw_text = "\n".join(parts)
        elif extension == ".docx":
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph

            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise ValueError("Nội dung DOCX quá lớn sau giải nén. Hãy chia nhỏ tài liệu.")
            document = Document(io.BytesIO(content))
            parts = []
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    parts.append(block.text)
                elif isinstance(block, Table):
                    for row in block.rows:
                        parts.append("\t".join(cell.text for cell in row.cells))
            raw_text = "\n".join(parts)
        elif extension == ".txt":
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            try:
                raw_text = content.decode(encoding)
            except UnicodeDecodeError as exc:
                raise ValueError("Không đọc được mã hóa TXT. Hãy lưu file dạng UTF-8 rồi tải lại.") from exc
        else:
            raise ValueError("Chỉ chấp nhận file .pdf, .docx hoặc .txt.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Không đọc được tài liệu. File có thể bị hỏng hoặc không đúng định dạng.") from exc

    raw_text = unicodedata.normalize("NFC", raw_text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", raw_text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        raise ValueError("Không tìm thấy chữ trong file. PDF scan cần được OCR trước khi tải lên.")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise ValueError(f"Văn bản tối đa {MAX_TEXT_CHARS:,} ký tự. Hãy chia nhỏ tài liệu.")
    return cleaned


async def extract_text_from_upload(upload_file: UploadFile) -> str:
    filename = upload_file.filename or ""
    if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("Chỉ chấp nhận file .pdf, .docx hoặc .txt.")
    content = await upload_file.read(MAX_FILE_BYTES + 1)
    if not content:
        raise ValueError("File tải lên bị rỗng.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("Mỗi file tối đa 5 MB. Hãy chia nhỏ tài liệu.")
    return await run_in_threadpool(extract_text, content, filename)
