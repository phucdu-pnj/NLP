import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from file_parser import MAX_TEXT_CHARS, extract_text_from_upload
from nlp_engine import AuthorshipAnalyzer

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
# Khoi tao nhe; PhoBERT chi duoc nap khi phan tich o che do phobert.
analyzer = AuthorshipAnalyzer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Chi xu ly mot cap tai lieu cung luc tren may it RAM.
    app.state.analysis_lock = asyncio.Lock()
    yield


app = FastAPI(title="Hệ thống Đối chiếu Phong cách Viết", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def render_page(request: Request, status_code: int = 200, **values):
    context = {
        "result": None, "error": None, "ref_text": "", "target_text": "",
        "ref_filename": None, "target_filename": None,
        "analysis_mode": analyzer.mode, "max_text_chars": MAX_TEXT_CHARS,
    }
    context.update(values)
    return templates.TemplateResponse(
        request=request, name="index.html", context=context, status_code=status_code
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render_page(request)


@app.get("/health")
async def health():
    # Health check chi kiem tra web server; khong tai mo hinh hay goi mang.
    return {"status": "ok", "mode": analyzer.mode, "model_loaded": analyzer.model_loaded}


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    ref_text: str = Form(""),
    target_text: str = Form(""),
    ref_file: Optional[UploadFile] = File(None),
    target_file: Optional[UploadFile] = File(None),
):
    error, result = None, None
    ref_filename, target_filename = None, None
    status_code = 200
    lock = request.app.state.analysis_lock

    try:
        if lock.locked():
            return render_page(
                request, status_code=503,
                error="Máy chủ đang phân tích một tài liệu khác. Bạn vui lòng thử lại sau.",
                ref_text=ref_text[:MAX_TEXT_CHARS], target_text=target_text[:MAX_TEXT_CHARS],
            )
        async with lock:
            if ref_file and ref_file.filename:
                ref_text = await extract_text_from_upload(ref_file)
                ref_filename = ref_file.filename
            if target_file and target_file.filename:
                target_text = await extract_text_from_upload(target_file)
                target_filename = target_file.filename

            if len(ref_text) > MAX_TEXT_CHARS or len(target_text) > MAX_TEXT_CHARS:
                raise ValueError(f"Mỗi văn bản tối đa {MAX_TEXT_CHARS:,} ký tự. Hãy chia tài liệu thành phần nhỏ hơn.")
            if len(ref_text.split()) < 30 or len(target_text.split()) < 30:
                raise ValueError("Mỗi văn bản cần tối thiểu 30 từ (đếm theo khoảng trắng).")

            # Cong viec CPU chay o thread de /health van tra loi duoc.
            result = await run_in_threadpool(analyzer.compare_articles, ref_text, target_text)
    except ValueError as exc:
        error, status_code = str(exc), 400
    except RuntimeError as exc:
        error, status_code = str(exc), 503
    except Exception:
        logger.exception("Không thể phân tích tài liệu")
        error, status_code = "Không xử lý được tài liệu. Hãy kiểm tra file và thử lại.", 500
    finally:
        for upload in (ref_file, target_file):
            if upload is not None:
                await upload.close()

    return render_page(
        request, status_code=status_code, result=result, error=error,
        ref_text=ref_text[:MAX_TEXT_CHARS], target_text=target_text[:MAX_TEXT_CHARS],
        ref_filename=ref_filename, target_filename=target_filename,
    )
