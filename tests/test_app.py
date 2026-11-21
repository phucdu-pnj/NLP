import io
import os
from pathlib import Path
import subprocess
import sys
import threading
import unicodedata
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

os.environ["ANALYSIS_MODE"] = "light"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

import main
from file_parser import MAX_FILE_BYTES, MAX_TEXT_CHARS, extract_text
from nlp_engine import AuthorshipAnalyzer

TEXT = (
    "Tôi là một sinh viên và tôi đang tìm hiểu về phong cách viết. "
    "Trong bài báo này, tác giả trình bày các kết quả nghiên cứu, "
    "nhưng vẫn cần kiểm tra dữ liệu để giải thích những khác biệt. "
    "Vì vậy, chúng tôi sẽ đọc lại nội dung và đối chiếu với tài liệu gốc."
)
OTHER = "Dữ liệu quan sát được thu thập mỗi ngày. " * 8


def pdf_bytes(text=None, password=None, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=595, height=842)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
            })
            stream = DecodedStreamObject()
            stream.set_data(("BT /F1 12 Tf 50 700 Td (" + text + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def post_text(self, first=TEXT, second=TEXT):
        return self.client.post("/analyze", data={"ref_text": first, "target_text": second})

    def upload(self, name, content, mime="application/octet-stream"):
        return self.client.post(
            "/analyze", data={"target_text": TEXT},
            files={"ref_file": (name, content, mime)},
        )

    def test_home_css_and_health(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Chế độ thống kê phong cách", response.text)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)
        self.assertEqual(self.client.get("/health").json(), {
            "status": "ok", "mode": "light", "model_loaded": False,
        })

    def test_identical_text_and_absent_phobert_score(self):
        response = self.post_text()
        self.assertEqual(response.status_code, 200)
        self.assertIn("100.0%", response.text)
        self.assertIn("Không sử dụng trong chế độ thống kê", response.text)
        self.assertNotIn("None%", response.text)

    def test_different_text_has_bounded_score(self):
        result = main.analyzer.compare_articles(TEXT, OTHER)
        self.assertTrue(0 <= result["final_score"] < 100)
        self.assertIsNone(result["bert_score"])

    def test_unicode_normalization(self):
        result = main.analyzer.compare_articles(TEXT, unicodedata.normalize("NFD", TEXT))
        self.assertEqual(result["final_score"], 100)

    def test_function_word_phrase(self):
        result = main.analyzer.extract_stylometry("Tuy nhiên, tuy nhiên, vì vậy.")
        self.assertIn(("tuy nhiên", 2), result["metrics"]["top_func_words"])

    def test_short_input_and_lock_released(self):
        response = self.post_text("Quá ngắn", TEXT)
        self.assertEqual(response.status_code, 400)
        self.assertIn("tối thiểu 30", response.text)
        self.assertEqual(self.post_text().status_code, 200)

    def test_empty_input(self):
        self.assertEqual(self.post_text("", "").status_code, 400)

    def test_punctuation_only(self):
        self.assertEqual(self.post_text("... " * 40, TEXT).status_code, 400)

    def test_overlong_text(self):
        response = self.post_text("x " * MAX_TEXT_CHARS, TEXT)
        self.assertEqual(response.status_code, 400)
        self.assertIn("30,000", response.text)

    def test_html_is_escaped(self):
        response = self.post_text(TEXT + " <script>alert(1)</script>")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>alert(1)</script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)

    def test_txt_encodings_and_uppercase_extension(self):
        for encoding in ("utf-8", "utf-8-sig", "utf-16"):
            with self.subTest(encoding=encoding):
                response = self.upload("mau.TXT", TEXT.encode(encoding), "text/plain")
                self.assertEqual(response.status_code, 200)
                self.assertIn("100.0%", response.text)

    def test_file_overrides_text(self):
        response = self.client.post(
            "/analyze", data={"ref_text": "Quá ngắn", "target_text": TEXT},
            files={"ref_file": ("mau.txt", TEXT.encode(), "text/plain")},
        )
        self.assertEqual(response.status_code, 200)

    def test_unselected_file_inputs(self):
        response = self.client.post(
            "/analyze", data={"ref_text": TEXT, "target_text": TEXT},
            files={"ref_file": ("", b""), "target_file": ("", b"")},
        )
        self.assertEqual(response.status_code, 200)

    def test_both_uploaded_files(self):
        response = self.client.post("/analyze", files={
            "ref_file": ("mau.txt", TEXT.encode(), "text/plain"),
            "target_file": ("kiem_tra.txt", TEXT.encode(), "text/plain"),
        })
        self.assertEqual(response.status_code, 200)

    def test_docx_paragraph_and_table(self):
        doc = Document()
        doc.add_paragraph(TEXT)
        table = doc.add_table(rows=1, cols=1)
        table.cell(0, 0).text = "Nội dung trong bảng"
        buffer = io.BytesIO()
        doc.save(buffer)
        data = buffer.getvalue()
        self.assertIn("Nội dung trong bảng", extract_text(data, "mau.docx"))
        self.assertEqual(self.upload("mau.docx", data).status_code, 200)

    def test_pdf_with_text(self):
        data = pdf_bytes("This is a readable article for testing. " * 8)
        self.assertIn("readable", extract_text(data, "mau.pdf"))
        self.assertEqual(self.upload("mau.pdf", data).status_code, 200)

    def test_blank_pdf(self):
        response = self.upload("blank.pdf", pdf_bytes())
        self.assertEqual(response.status_code, 400)
        self.assertIn("OCR", response.text)

    def test_encrypted_pdf(self):
        response = self.upload("locked.pdf", pdf_bytes(password="secret"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("mật khẩu", response.text)

    def test_too_many_pdf_pages(self):
        response = self.upload("long.pdf", pdf_bytes(pages=31))
        self.assertEqual(response.status_code, 400)
        self.assertIn("30 trang", response.text)

    def test_bad_files(self):
        for name, data, expected in (
            ("empty.txt", b"", "rỗng"),
            ("bad.exe", b"bad", "Chỉ chấp nhận"),
            ("bad.pdf", b"bad pdf", "Không đọc được"),
            ("bad.docx", b"bad zip", "Không đọc được"),
            ("bad.txt", b"\x80\x81", "UTF-8"),
            ("large.txt", b"a" * (MAX_FILE_BYTES + 1), "5 MB"),
        ):
            with self.subTest(name=name):
                response = self.upload(name, data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected, response.text)

    def test_health_responds_during_analysis_and_second_analysis_is_busy(self):
        started, finish = threading.Event(), threading.Event()
        real_compare = main.analyzer.compare_articles

        def slow_compare(*args):
            started.set()
            if not finish.wait(timeout=5):
                raise RuntimeError("Test timeout")
            return real_compare(*args)

        with patch.object(main.analyzer, "compare_articles", side_effect=slow_compare):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.post_text)
                try:
                    self.assertTrue(started.wait(timeout=3))
                    self.assertEqual(self.client.get("/health").status_code, 200)
                    self.assertEqual(self.post_text().status_code, 503)
                finally:
                    finish.set()
                self.assertEqual(future.result(timeout=3).status_code, 200)

    def test_model_failure_is_visible(self):
        with patch.object(main.analyzer, "compare_articles", side_effect=RuntimeError("Không nạp được PhoBERT")):
            response = self.post_text()
        self.assertEqual(response.status_code, 503)
        self.assertIn("Không nạp được PhoBERT", response.text)
        self.assertNotIn("100.0%", response.text)

    def test_no_heavy_imports_and_other_working_directory(self):
        code = (
            "import sys; from fastapi.testclient import TestClient; import main; "
            "from nlp_engine import AuthorshipAnalyzer; "
            "assert not AuthorshipAnalyzer(mode='phobert').model_loaded; "
            "assert not {'torch','transformers','underthesea','numpy','sklearn'} & set(sys.modules); "
            "client=TestClient(main.app); assert client.get('/').status_code == 200; "
            "assert client.get('/static/style.css').status_code == 200"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT.parent, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
