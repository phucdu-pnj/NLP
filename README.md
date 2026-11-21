# Sửa ứng dụng đối chiếu tác giả cho Render Free

Bản sửa dựa trên repository `phucdu-pnj/authorship-detector`, commit
`33209d1623f5eac217adb64c7d98ccc142cf2af9`, và log lỗi được cung cấp.

**Mặc định dùng thống kê phong cách nhẹ, không chạy PhoBERT.** Render Free
có 512 MB RAM. Chế độ nhẹ giữ việc nhập hai văn bản, tải PDF/DOCX/TXT,
so sánh dấu câu, hư từ, độ dài câu và độ phong phú từ vựng. Cách tách đơn vị
văn bản của chế độ nhẹ đơn giản hơn Underthesea; điểm không tương đương
điểm lai PhoBERT của phiên bản cũ.

Điểm là độ tương đồng của các đặc trưng đang xét, không phải xác suất cùng tác giả.
Không dùng kết quả này làm bằng chứng xác định người viết. Trọng số và ngưỡng
của nhánh PhoBERT cũ chưa được kiểm định trên tập dữ liệu có nhãn.

## 1. Lỗi trong bản cũ

| Vấn đề | Bằng chứng / cách sửa |
| --- | --- |
| Build dừng ở `Cannot import 'maturin'` | Log dùng Python 3.14.10; `underthesea==6.8.4` kéo `underthesea-core==1.0.4`, không có wheel Python 3.14. Bản Free bỏ phụ thuộc này; ghim Python 3.11.18 cho cấu hình thống nhất và nhánh PhoBERT tùy chọn. |
| Thiếu thư viện đọc tài liệu | `file_parser.py` import `pypdf` và `docx` nhưng requirements cũ không có `pypdf` và `python-docx`. Đã thêm cả hai. |
| Tải PhoBERT khi import `main.py` | Dễ làm chậm khởi động hoặc hết RAM. Bản Free không import các thư viện AI nặng. Nhánh PhoBERT chỉ tải mô hình khi thực sự phân tích. |
| Đường dẫn giao diện phụ thuộc thư mục chạy | Đã dùng đường dẫn tính từ vị trí `main.py`; giữ lại và cập nhật HTML/CSS của repository. |
| Phân tích nặng trong hàm async | Chuyển công việc đồng bộ sang thread; thêm `/health`; mỗi lần chỉ phân tích một cặp tài liệu. |
| Lỗi file gây thông báo khó hiểu | Có thông báo cho file rỗng, sai định dạng, TXT không đúng mã hóa, PDF có mật khẩu/không có chữ; đọc thêm nội dung bảng DOCX. |

Không cần cài thủ công `maturin` để chạy bản Free này.

## 2. Cập nhật repository GitHub hiện có

1. Giải nén bộ mã.
2. Tải **nội dung bên trong thư mục đã giải nén** vào thư mục gốc repository
   `authorship-detector`, cùng vị trí `main.py` cũ. Giữ nguyên các thư mục
   `templates`, `static` và `tests`.
3. Ghi đè các file trùng tên, thêm các file mới và commit lên nhánh `main`.

Tên bắt buộc là `main.py` và `requirements.txt`, không phải `main(2).py`
hay `requirements(1).txt`. Trong repository đã kiểm tra, tên hiện tại vốn đúng;
tên có số là tên bản đính kèm tải xuống.

Các file chạy ứng dụng:

| File | Công dụng |
| --- | --- |
| `main.py` | Web FastAPI và các route |
| `nlp_engine.py` | So sánh thống kê, tùy chọn PhoBERT |
| `file_parser.py` | Đọc TXT/PDF/DOCX |
| `requirements.txt` | Thư viện cho Render Free |
| `.python-version` | Python 3.11.18 |
| `render.yaml` | Cấu hình tạo service bằng Blueprint |
| `templates/index.html` | Giao diện |
| `static/style.css` | CSS |

## 3. Sửa cấu hình service Render đang có

Vào service đang bị lỗi, chọn **Settings** và kiểm tra:

| Mục | Giá trị |
| --- | --- |
| Service type | Web Service |
| Runtime / Language | Python 3 |
| Branch | `main` |
| Root Directory | Để trống nếu `main.py` ở gốc repository |
| Build Command | `python -m pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health Check Path | `/health` |
| Instance type | Free |

Trong **Environment**, thêm hoặc sửa:

| Key | Value |
| --- | --- |
| `PYTHON_VERSION` | `3.11.18` |
| `ANALYSIS_MODE` | `light` |
| `PYTHONUNBUFFERED` | `1` |

`PYTHON_VERSION` trong Environment được ưu tiên hơn `.python-version`.
Nếu đang đặt giá trị 3.14 ở đó, phải sửa lại. Không cần tự tạo biến `PORT`;
lệnh khởi động dùng cổng Render cấp.

Cuối cùng chọn **Manual Deploy → Clear build cache & deploy**.
Việc xóa build cache giúp bỏ các thư viện AI nặng và môi trường build cũ.
Thêm `render.yaml` vào GitHub không tự ghi đè cấu hình một service đã tạo
thủ công; với service hiện tại vẫn phải kiểm tra bảng cấu hình ở trên.

## 4. Kiểm tra sau khi deploy

- Log phải dùng Python 3.11.18 và hoàn tất bước cài requirements.
- Log khởi động có `Application startup complete` và Uvicorn lắng nghe trên cổng Render.
- Mở trang chủ, dán hai văn bản từ 30 từ trở lên rồi bấm phân tích.
- Mở đường dẫn `/health` của website. Ở chế độ nhẹ, JSON dự kiến là:

```json
{"status":"ok","mode":"light","model_loaded":false}
```

`model_loaded: false` là bình thường trong chế độ nhẹ. `/health` chỉ phản ánh
web server đang hoạt động, không chứng minh mô hình PhoBERT đã sẵn sàng.

Giới hạn: mỗi file 5 MB, mỗi văn bản 30.000 ký tự, PDF 30 trang.
PDF scan cần OCR trước. File được ưu tiên nếu vừa chọn file vừa dán nội dung.
TXT hỗ trợ UTF-8, UTF-8 có BOM và UTF-16 có BOM.
Giới hạn file/trang giúp giảm tải nhưng không đảm bảo mọi PDF phức tạp đều vừa RAM;
nếu một PDF gây hết RAM, hãy chuyển sang TXT hoặc chia thành tài liệu nhỏ.

## 5. Chạy trên máy cá nhân

Cài Python 3.11, mở terminal trong thư mục chứa `main.py`:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Linux:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Mở `http://127.0.0.1:8000`.

## 6. Tùy chọn PhoBERT cho máy đủ RAM

Chỉ bật khi có tài nguyên phù hợp; tải chậm khi cần không làm giảm lượng RAM
của mô hình. Nên thử từ 2 GB RAM và đo thực tế; dung lượng cần còn phụ thuộc
độ dài tài liệu và thư viện. Gói trả phí 512 MB cũng không giải quyết giới hạn này.

- Dùng Python 3.11 trên Linux/Windows x86_64.
- Đổi Build Command thành `python -m pip install -r requirements-phobert.txt`.
- Đặt `ANALYSIS_MODE=phobert`, `OMP_NUM_THREADS=1`, `MKL_NUM_THREADS=1`,
  `TOKENIZERS_PARALLELISM=false`.
- Giữ `--workers 1` để không tạo nhiều bản mô hình trong RAM.
- Nhánh này dùng Torch CPU, Underthesea và `vinai/phobert-base-v2` như dự án gốc.
  Lần phân tích đầu cần kết nối Hugging Face để tải mô hình.

Không đổi chỉ `ANALYSIS_MODE` sang `phobert` trên Render Free.
Nếu tải mô hình thất bại, ứng dụng hiển thị lỗi; không âm thầm thay bằng điểm giả.
Nhánh PhoBERT được giữ trong mã nhưng chưa chạy thử tải và suy luận mô hình
trong lần sửa này. Phần đã kiểm thử thực tế là chế độ `light` cho Render Free.

## 7. Kiểm thử

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m pip check
```

Đã chạy 23 bài kiểm thử thành công trên Python 3.11.18, gồm trang chủ/CSS,
phân tích văn bản, Unicode tiếng Việt, TXT/PDF/DOCX, file lỗi, giới hạn đầu vào,
không import các thư viện AI trong chế độ nhẹ, đường dẫn khi đổi thư mục chạy,
và health check vẫn trả lời trong lúc đang phân tích.

Đã khởi động Uvicorn thật trên cổng 10000: trang chủ, CSS, `/health`, POST
văn bản và tải PDF/DOCX đều trả HTTP 200. Sau thử tài liệu mẫu và 10 lần
phân tích cặp văn bản 30.000 ký tự, tiến trình ghi nhận VmHWM 73.372 KiB
(khoảng 72 MiB). Đây là số đo trong lần thử, không phải cam kết bộ nhớ cho mọi file.

Đây là bản sửa mã nguồn đã kiểm thử trong môi trường Linux riêng.
Chưa thay đổi repository GitHub hoặc service Render của bạn từ xa.

## Tài liệu đối chiếu

- https://render.com/docs/python-version
- https://render.com/docs/deploy-fastapi
- https://render.com/docs/compute-plans
- https://render.com/docs/deploys
- https://pypi.org/project/underthesea-core/1.0.4/
- https://www.python.org/downloads/release/python-31118/
- https://pytorch.org/get-started/previous-versions/
