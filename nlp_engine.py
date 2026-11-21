"""Thong ke nhe mac dinh; PhoBERT la tuy chon can cai dependencies rieng."""
import logging
import math
import os
import re
import threading
import unicodedata
from collections import Counter

logger = logging.getLogger(__name__)

VIETNAMESE_FUNCTION_WORDS = [
    "thì", "là", "mà", "của", "và", "nhưng", "tuy_nhiên", "do_đó",
    "vì_vậy", "bởi_vì", "để", "cho", "với", "trong", "ngoài", "trên",
    "dưới", "các", "những", "một", "này", "đó", "nên", "nếu", "khi",
    "đã", "đang", "sẽ", "cũng", "vẫn", "đều", "rất", "quá", "lắm",
]
PUNCTUATIONS = [',', ';', ':', '-', '(', ')', '"', '?', '!']
FUNCTION_PHRASES = [word.replace("_", " ") for word in VIETNAMESE_FUNCTION_WORDS if "_" in word]


def cosine_similarity(left, right) -> float:
    denominator = math.sqrt(sum(float(x) ** 2 for x in left) * sum(float(x) ** 2 for x in right))
    if denominator == 0:
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(left, right)) / denominator


class AuthorshipAnalyzer:
    def __init__(self, model_name: str = "vinai/phobert-base-v2", mode: str | None = None):
        self.mode = (mode or os.getenv("ANALYSIS_MODE", "light")).lower().strip()
        if self.mode not in {"light", "phobert"}:
            raise ValueError("ANALYSIS_MODE phải là 'light' hoặc 'phobert'.")
        self.model_name = model_name
        self.model = None
        self.tokenizer = None
        self._torch = None
        self._word_tokenize = None
        self._model_lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self.model is not None

    def _load_phobert(self):
        if self.model_loaded:
            return
        with self._model_lock:
            if self.model_loaded:
                return
            try:
                import torch
                from transformers import AutoModel, AutoTokenizer
                from underthesea import word_tokenize

                torch.set_num_threads(1)
                tokenizer = AutoTokenizer.from_pretrained(self.model_name, use_fast=False)
                model = AutoModel.from_pretrained(self.model_name).to("cpu")
                model.eval()
            except Exception as exc:
                logger.exception("Không nạp được PhoBERT")
                raise RuntimeError(
                    "Không nạp được PhoBERT. Quản trị viên cần kiểm tra thư viện, kết nối tải mô hình "
                    "và RAM; Render Free hãy đặt ANALYSIS_MODE=light."
                ) from exc
            self._torch = torch
            self._word_tokenize = word_tokenize
            self.tokenizer = tokenizer
            self.model = model

    def extract_stylometry(self, text: str) -> dict:
        text = unicodedata.normalize("NFC", text)
        sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
        if self.mode == "phobert":
            self._load_phobert()
            segmented_text = self._word_tokenize(text, format="text").lower()
            tokens = segmented_text.split()
        else:
            # Ban Free tach don vi theo khoang trang/dau cau, chi ghep cum hu tu.
            # Day khong phai bo tach tu tieng Viet cua Underthesea.
            normalized = text.lower()
            for phrase in FUNCTION_PHRASES:
                pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, phrase.split())) + r"(?!\w)"
                normalized = re.sub(pattern, phrase.replace(" ", "_"), normalized)
            tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)

        if not tokens:
            raise ValueError("Văn bản cần có chữ hoặc số để phân tích.")
        counts = Counter(tokens)
        total_words, total_chars = len(tokens), max(len(text), 1)
        total_sents = max(len(sentences), 1)
        avg_word_len = sum(len(word.replace("_", "")) for word in tokens) / total_words
        avg_sent_len = total_words / total_sents
        ttr = len(counts) / total_words
        vector = [avg_word_len / 10.0, avg_sent_len / 50.0, ttr]
        vector.extend(text.count(punctuation) / total_chars for punctuation in PUNCTUATIONS)
        vector.extend(counts[word] / total_words for word in VIETNAMESE_FUNCTION_WORDS)
        func_counts = {word.replace("_", " "): counts[word] for word in VIETNAMESE_FUNCTION_WORDS if counts[word]}
        return {
            "vector": vector,
            "metrics": {
                "total_words": total_words, "total_sentences": total_sents,
                "avg_word_len": round(avg_word_len, 2), "avg_sent_len": round(avg_sent_len, 2),
                "ttr": round(ttr * 100, 1),
                "top_func_words": sorted(func_counts.items(), key=lambda item: item[1], reverse=True)[:5],
            },
        }

    def extract_phobert_embedding(self, text: str, max_len: int = 256, stride: int = 128):
        self._load_phobert()
        import numpy as np

        torch = self._torch
        if not 3 <= max_len <= 256 or not 1 <= stride <= max_len - 2:
            raise ValueError("Cấu hình cửa sổ PhoBERT không hợp lệ.")
        segmented_text = self._word_tokenize(unicodedata.normalize("NFC", text), format="text")
        input_ids = self.tokenizer(
            segmented_text, return_tensors="pt", truncation=False, add_special_tokens=False
        )["input_ids"][0]
        if len(input_ids) == 0:
            return np.zeros(self.model.config.hidden_size, dtype=np.float32)

        # Xu ly tung cua so, khong luu toan bo tensor cua bai bao trong RAM.
        chunk_size = max_len - 2
        total_embedding, count = None, 0
        with torch.inference_mode():
            for start in range(0, len(input_ids), stride):
                chunk = torch.cat([
                    torch.tensor([self.tokenizer.cls_token_id]),
                    input_ids[start:start + chunk_size],
                    torch.tensor([self.tokenizer.sep_token_id]),
                ]).unsqueeze(0)
                outputs = self.model(input_ids=chunk, attention_mask=torch.ones_like(chunk))
                embedding = outputs.last_hidden_state.mean(dim=1).squeeze(0).cpu().numpy()
                total_embedding = embedding.copy() if total_embedding is None else total_embedding + embedding
                count += 1
                if start + chunk_size >= len(input_ids):
                    break
        return total_embedding / count

    def compare_articles(self, ref_text: str, target_text: str) -> dict:
        ref_style = self.extract_stylometry(ref_text)
        target_style = self.extract_stylometry(target_text)
        style_score = max(0.0, min(1.0, cosine_similarity(ref_style["vector"], target_style["vector"])))
        bert_score = None
        if self.mode == "phobert":
            ref_bert = self.extract_phobert_embedding(ref_text)
            target_bert = self.extract_phobert_embedding(target_text)
            bert_sim = cosine_similarity(ref_bert, target_bert)
            # Giu cong thuc ban goc; trong so/nguong chua duoc hieu chinh bang du lieu gan nhan.
            bert_score = max(0.0, min(1.0, (bert_sim - 0.60) / 0.40))
            final_score = round((0.55 * style_score + 0.45 * bert_score) * 100, 1)
        else:
            final_score = round(style_score * 100, 1)

        # Diem tuong dong khong phai xac suat cung tac gia.
        if self.mode == "light":
            verdict, status = "ĐỐI CHIẾU THỐNG KÊ PHONG CÁCH", "warning"
            explanation = (
                "Điểm dựa trên độ dài câu, độ phong phú từ vựng, dấu câu và hư từ. "
                "Chế độ này không sử dụng PhoBERT. Điểm cao chỉ cho thấy các đặc trưng thống kê gần nhau."
            )
        elif final_score >= 72.0:
            verdict, status = "ĐỘ TƯƠNG ĐỒNG CAO", "match"
            explanation = "Các đặc trưng phong cách và biểu diễn ngữ cảnh có độ tương đồng cao theo công thức thử nghiệm."
        elif final_score >= 52.0:
            verdict, status = "CẦN ĐỐI CHIẾU THÊM", "warning"
            explanation = "Có khác biệt về các đặc trưng phong cách hoặc biểu diễn ngữ cảnh."
        else:
            verdict, status = "ĐỘ TƯƠNG ĐỒNG THẤP", "mismatch"
            explanation = "Các đặc trưng đang xét có độ tương đồng thấp theo công thức thử nghiệm."

        return {
            "final_score": final_score, "style_score": round(style_score * 100, 1),
            "bert_score": round(bert_score * 100, 1) if bert_score is not None else None,
            "verdict": verdict, "status": status, "explanation": explanation,
            "mode": self.mode,
            "ref_metrics": ref_style["metrics"], "target_metrics": target_style["metrics"],
        }
