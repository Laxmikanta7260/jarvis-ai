from fastapi import FastAPI, UploadFile, File, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import requests
from gtts import gTTS
import os
import uuid
import io
import re
import json
import csv
import sqlite3
import logging
from contextlib import contextmanager
from typing import List, Dict, Optional, Any, Tuple
from collections import Counter

import pytesseract
from pdf2image import convert_from_bytes
from PyPDF2 import PdfReader
import chromadb
from docx import Document
from bs4 import BeautifulSoup

# -----------------------------
# Setup
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio")
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
DB_PATH = os.path.join(BASE_DIR, "jarvis.db")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
GEN_MODEL = os.getenv("GEN_MODEL", "llama3.2:1b")
EMBED_MODEL = os.getenv("EMBED_MODEL", "embeddinggemma")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "25"))
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "30"))

# Optional
# pytesseract.pytesseract.tesseract_cmd = r"D:\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = None
# Example:
# POPPLER_PATH = r"D:\poppler\Library\bin"

ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".csv", ".json", ".html", ".htm", ".md", ".txt",
    ".py", ".js", ".css", ".ts", ".java", ".c", ".cpp"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("jarvis")

app = FastAPI()
logger.info("RUNNING JARVIS FINAL FILE-KB VERSION")

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(CHROMA_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# -----------------------------
# ChromaDB
# -----------------------------
chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
collection = chroma_client.get_or_create_collection(name="jarvis_chunks")

# -----------------------------
# Pydantic models
# -----------------------------
class ChatRequest(BaseModel):
    question: str
    language: str = "en-US"
    kb_id: Optional[str] = None


class SaveMemoryRequest(BaseModel):
    key: str
    value: str


class DeleteMemoryRequest(BaseModel):
    key: str


# -----------------------------
# SQLite helpers
# -----------------------------
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                kb_id TEXT,
                kb_name TEXT,
                last_uploaded_file TEXT DEFAULT '',
                last_file_summary TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_bases (
                kb_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                total_chunks INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS kb_files (
                file_id TEXT PRIMARY KEY,
                kb_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                doc_ids_count INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                memory_key TEXT NOT NULL,
                memory_value TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, memory_key)
            )
        """)

    logger.info("SQLite initialized")


init_db()

# -----------------------------
# Knowledge base helpers
# -----------------------------
def create_knowledge_base(name: Optional[str] = None) -> str:
    kb_id = str(uuid.uuid4())
    kb_name = name or "My Knowledge Base"

    with get_db() as conn:
        conn.execute(
            "INSERT INTO knowledge_bases (kb_id, name, total_chunks) VALUES (?, ?, ?)",
            (kb_id, kb_name, 0)
        )

    return kb_id


def get_kb_data(kb_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        kb_row = conn.execute(
            "SELECT kb_id, name, total_chunks FROM knowledge_bases WHERE kb_id = ?",
            (kb_id,)
        ).fetchone()

        if not kb_row:
            return {}

        files = conn.execute("""
            SELECT file_id, kb_id, file_name, chunk_count, doc_ids_count, created_at
            FROM kb_files
            WHERE kb_id = ?
            ORDER BY created_at ASC
        """, (kb_id,)).fetchall()

    return {
        "kb_id": kb_row["kb_id"],
        "name": kb_row["name"],
        "total_chunks": kb_row["total_chunks"],
        "files": [dict(f) for f in files]
    }


def update_kb_total_chunks(kb_id: str):
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(chunk_count), 0) AS total FROM kb_files WHERE kb_id = ?",
            (kb_id,)
        ).fetchone()
        total = row["total"] if row else 0
        conn.execute(
            "UPDATE knowledge_bases SET total_chunks = ? WHERE kb_id = ?",
            (total, kb_id)
        )


def delete_kb_record(kb_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM kb_files WHERE kb_id = ?", (kb_id,))
        conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))


# -----------------------------
# Session helpers
# -----------------------------
def create_session(session_id: str) -> str:
    kb_id = create_knowledge_base("My Knowledge Base")
    kb = get_kb_data(kb_id)

    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sessions
            (session_id, kb_id, kb_name, last_uploaded_file, last_file_summary)
            VALUES (?, ?, ?, '', '')
        """, (session_id, kb_id, kb.get("name", "My Knowledge Base")))

    return session_id


def get_session_data(session_id: str) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("""
            SELECT session_id, kb_id, kb_name, last_uploaded_file, last_file_summary
            FROM sessions
            WHERE session_id = ?
        """, (session_id,)).fetchone()

    return dict(row) if row else None


def update_session_meta(
    session_id: str,
    kb_id: Optional[str] = None,
    kb_name: Optional[str] = None,
    last_uploaded_file: Optional[str] = None,
    last_file_summary: Optional[str] = None
):
    fields = []
    values = []

    if kb_id is not None:
        fields.append("kb_id = ?")
        values.append(kb_id)
    if kb_name is not None:
        fields.append("kb_name = ?")
        values.append(kb_name)
    if last_uploaded_file is not None:
        fields.append("last_uploaded_file = ?")
        values.append(last_uploaded_file)
    if last_file_summary is not None:
        fields.append("last_file_summary = ?")
        values.append(last_file_summary)

    fields.append("updated_at = CURRENT_TIMESTAMP")

    with get_db() as conn:
        conn.execute(
            f"UPDATE sessions SET {', '.join(fields)} WHERE session_id = ?",
            (*values, session_id)
        )


def get_or_create_session_id(request: Request, response: Response) -> str:
    session_id = request.cookies.get("session_id")

    if not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie("session_id", session_id, httponly=True)

    session = get_session_data(session_id)
    if not session:
        create_session(session_id)

    return session_id


def resolve_kb_id(session_id: str, requested_kb_id: Optional[str] = None) -> str:
    session = get_session_data(session_id)
    if not session:
        create_session(session_id)
        session = get_session_data(session_id)

    if requested_kb_id:
        kb = get_kb_data(requested_kb_id)
        if kb:
            update_session_meta(
                session_id,
                kb_id=requested_kb_id,
                kb_name=kb.get("name", "My Knowledge Base")
            )
            return requested_kb_id

    kb_id = session.get("kb_id")
    kb = get_kb_data(kb_id) if kb_id else {}

    if kb:
        return kb_id

    new_kb_id = create_knowledge_base("My Knowledge Base")
    new_kb = get_kb_data(new_kb_id)
    update_session_meta(
        session_id,
        kb_id=new_kb_id,
        kb_name=new_kb.get("name", "My Knowledge Base")
    )
    return new_kb_id


# -----------------------------
# Chat history helpers
# -----------------------------
def add_chat_message(session_id: str, role: str, content: str):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO chat_messages (session_id, role, content)
            VALUES (?, ?, ?)
        """, (session_id, role, content))


def get_chat_history(session_id: str, max_turns: int = 20) -> List[Dict[str, str]]:
    limit_rows = max_turns * 2
    with get_db() as conn:
        rows = conn.execute("""
            SELECT role, content
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (session_id, limit_rows)).fetchall()

    return [dict(r) for r in reversed(rows)]


def clear_chat_history(session_id: str):
    with get_db() as conn:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))


def format_chat_history(chat_history: List[Dict[str, str]], max_turns: int = 8) -> str:
    recent_history = chat_history[-max_turns:]
    lines = []

    for msg in recent_history:
        role = msg.get("role", "assistant")
        content = msg.get("content", "").strip()
        if not content:
            continue

        if role == "user":
            lines.append(f"User: {content}")
        else:
            lines.append(f"Jarvis: {content}")

    return "\n".join(lines)


# -----------------------------
# User memory helpers
# -----------------------------
def save_memory(session_id: str, key: str, value: str):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO user_memory (session_id, memory_key, memory_value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id, memory_key)
            DO UPDATE SET
                memory_value = excluded.memory_value,
                updated_at = CURRENT_TIMESTAMP
        """, (session_id, key.strip(), value.strip()))


def delete_memory(session_id: str, key: str):
    with get_db() as conn:
        conn.execute("""
            DELETE FROM user_memory
            WHERE session_id = ? AND memory_key = ?
        """, (session_id, key.strip()))


def list_memory(session_id: str) -> List[Dict[str, str]]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT memory_key, memory_value, updated_at
            FROM user_memory
            WHERE session_id = ?
            ORDER BY updated_at DESC
        """, (session_id,)).fetchall()

    return [
        {
            "key": row["memory_key"],
            "value": row["memory_value"],
            "updated_at": row["updated_at"]
        }
        for row in rows
    ]


def memory_to_text(session_id: str, max_items: int = 12) -> str:
    items = list_memory(session_id)[:max_items]
    if not items:
        return "No saved long-term memory."

    return "\n".join(f"- {item['key']}: {item['value']}" for item in items)


# -----------------------------
# Cleaning / Processing
# -----------------------------
def clean_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", " ", text)
    text = text.replace("\u00a0", " ")

    text = re.sub(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
        " ",
        text,
    )

    text = re.sub(r"[-_=~*#]{3,}", "\n", text)
    text = re.sub(r"(?m)^[^\w\s]{4,}\s*$", "", text)
    text = re.sub(r"(?im)^\s*page\s+\d+(\s+of\s+\d+)?\s*$", "", text)
    text = re.sub(r"(?im)^\s*\d+\s*$", "", text)
    text = re.sub(r"(?<=\w)-\n(?=\w)", "", text)
    text = re.sub(r"(?<!\n)\n(?=[a-z])", " ", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    cleaned_lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue

        alpha_num_count = sum(ch.isalnum() for ch in stripped)
        if len(stripped) > 8 and alpha_num_count / max(len(stripped), 1) < 0.2:
            continue

        cleaned_lines.append(stripped)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def remove_repeated_lines(text: str) -> str:
    lines = text.splitlines()
    freq = {}

    for line in lines:
        normalized = line.strip().lower()
        if normalized:
            freq[normalized] = freq.get(normalized, 0) + 1

    cleaned = []
    for line in lines:
        normalized = line.strip().lower()
        if normalized and freq.get(normalized, 0) > 6 and len(normalized) < 120:
            continue
        cleaned.append(line)

    return "\n".join(cleaned).strip()


def remove_common_pdf_noise(page_texts: List[str]) -> str:
    if not page_texts:
        return ""

    per_page_lines: List[List[str]] = []
    freq: Counter = Counter()

    for text in page_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        per_page_lines.append(lines)
        seen = set()
        for line in lines:
            key = re.sub(r"\s+", " ", line).strip().lower()
            if key and len(key) < 140:
                seen.add(key)
        freq.update(seen)

    threshold = max(2, int(len(page_texts) * 0.5))
    cleaned_pages = []

    for lines in per_page_lines:
        kept = []
        for line in lines:
            key = re.sub(r"\s+", " ", line).strip().lower()
            if key and len(key) < 140 and freq.get(key, 0) >= threshold:
                continue
            kept.append(line)
        cleaned_pages.append("\n".join(kept).strip())

    return "\n\n".join(page for page in cleaned_pages if page).strip()


def smart_trim(text: str, max_chars: int = 18000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Content trimmed because file was too large.]"


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 150) -> List[str]:
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)

            if len(para) <= chunk_size:
                current = para
            else:
                start = 0
                while start < len(para):
                    end = start + chunk_size
                    piece = para[start:end].strip()
                    if piece:
                        chunks.append(piece)
                    start += max(chunk_size - overlap, 1)
                current = ""

    if current:
        chunks.append(current)

    return chunks[:300]


def classify_question(question: str) -> str:
    q = question.lower()

    if any(word in q for word in ["summary", "summarize", "overview", "main idea"]):
        return "summary"
    if any(word in q for word in ["function", "code", "logic", "algorithm", "line", "bug", "error"]):
        return "code"
    if any(word in q for word in ["compare", "difference", "differences", "similarities"]):
        return "comparison"
    if any(word in q for word in ["why", "how"]):
        return "explanation"
    if any(word in q for word in ["who", "what", "when", "where", "which"]):
        return "specific"

    return "specific"


def keyword_overlap_score(query: str, text: str) -> float:
    q_words = set(re.findall(r"\w+", query.lower()))
    t_words = set(re.findall(r"\w+", text.lower()))
    if not q_words:
        return 0.0
    return len(q_words & t_words) / len(q_words)


def rerank_sources(query: str, sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    q_lower = query.lower()
    is_code_question = any(word in q_lower for word in ["function", "class", "bug", "error", "code", "api", "route"])

    for src in sources:
        sim = src.get("similarity") or 0.0
        overlap = keyword_overlap_score(query, src.get("text", ""))
        file_name = (src.get("file_name") or "").lower()
        bonus = 0.0

        if file_name and file_name in q_lower:
            bonus += 0.15

        if is_code_question and file_name.endswith((".py", ".js", ".java", ".cpp", ".c", ".ts", ".css", ".html")):
            bonus += 0.10

        src["rank_score"] = round((sim * 0.65) + (overlap * 0.25) + bonus, 4)

    return sorted(sources, key=lambda x: x.get("rank_score", 0), reverse=True)


def looks_useful(text: str) -> bool:
    text = text.strip()
    if len(text) < 40:
        return False

    alpha = sum(ch.isalpha() for ch in text)
    ratio = alpha / max(len(text), 1)
    return ratio > 0.25


def text_to_audio_file(text: str, lang: str = "en") -> Optional[str]:
    safe_text = text.strip()
    if not safe_text:
        return None

    safe_text = smart_trim(safe_text, max_chars=2500)

    audio_file = f"{uuid.uuid4().hex}.mp3"
    audio_path = os.path.join(AUDIO_DIR, audio_file)
    gTTS(text=safe_text, lang=lang).save(audio_path)
    return f"/audio/{audio_file}"


# -----------------------------
# Ollama
# -----------------------------
def safe_model_call(prompt: str) -> str:
    try:
        ai_res = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": GEN_MODEL,
                "prompt": prompt,
                "stream": False
            },
            timeout=300
        )
        ai_res.raise_for_status()
        return ai_res.json().get("response", "").strip()
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama generation failed: {str(e)}")


def stream_model_call(prompt: str):
    try:
        with requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": GEN_MODEL,
                "prompt": prompt,
                "stream": True
            },
            stream=True,
            timeout=300
        ) as response:
            response.raise_for_status()

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                chunk = data.get("response", "")
                if chunk:
                    yield chunk

                if data.get("done", False):
                    break
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama streaming failed: {str(e)}")


def embed_texts(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    try:
        res = requests.post(
            f"{OLLAMA_BASE_URL}/api/embed",
            json={
                "model": EMBED_MODEL,
                "input": texts
            },
            timeout=300
        )
        res.raise_for_status()

        data = res.json()
        embeddings = data.get("embeddings", [])

        if not embeddings:
            raise ValueError("No embeddings returned from Ollama.")

        return embeddings
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama embedding failed: {str(e)}")


# -----------------------------
# Chroma helpers
# -----------------------------
def store_chunks_in_chroma(kb_id: str, file_id: str, file_name: str, chunks: List[str]) -> List[str]:
    if not chunks:
        return []

    embeddings = embed_texts(chunks)

    ids = [f"{kb_id}_{file_id}_{i}_{uuid.uuid4().hex[:8]}" for i in range(len(chunks))]
    metadatas = [
        {
            "kb_id": kb_id,
            "file_id": file_id,
            "file_name": file_name,
            "chunk_index": i + 1
        }
        for i in range(len(chunks))
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
        metadatas=metadatas
    )

    return ids


def delete_kb_docs(kb_id: str):
    try:
        results = collection.get(where={"kb_id": kb_id})
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
    except Exception as e:
        logger.error("Failed deleting KB docs: %s", e)


def delete_file_docs(kb_id: str, file_id: str):
    try:
        results = collection.get(where={"$and": [{"kb_id": kb_id}, {"file_id": file_id}]})
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
    except Exception as e:
        logger.error("Failed deleting file docs: %s", e)
        raise


def get_relevant_chunks_chroma(kb_id: str, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
    query_embedding = embed_texts([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"kb_id": kb_id},
        include=["documents", "metadatas", "distances"]
    )

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    final_sources = []

    for i, doc in enumerate(documents):
        metadata = metadatas[i] if i < len(metadatas) else {}
        distance = distances[i] if i < len(distances) else None

        preview = doc.strip().replace("\n", " ")
        preview = preview[:220] + "..." if len(preview) > 220 else preview

        similarity = None
        if distance is not None:
            try:
                similarity = round(1 - float(distance), 4)
            except Exception:
                similarity = None

        final_sources.append({
            "text": doc,
            "preview": preview,
            "file_name": metadata.get("file_name", "Uploaded file"),
            "file_id": metadata.get("file_id", ""),
            "chunk_index": metadata.get("chunk_index", i + 1),
            "similarity": similarity
        })

    return final_sources


# -----------------------------
# Summary
# -----------------------------
def build_file_summary(file_name: str, file_content: str) -> str:
    trimmed_content = smart_trim(file_content, max_chars=8000)

    prompt = f"""
You are Jarvis, an intelligent assistant.

Your task:
- Explain the uploaded file simply and clearly.
- Focus only on what is actually present in the file.
- Do not invent missing details.
- If the file is code, explain purpose, key logic, inputs, outputs, and important functions.
- If the file is a document, explain main topic, sections, and useful takeaways.
- Ignore junk text, OCR noise, repeated headers, IDs, page numbers, or broken symbols.

File name:
{file_name}

File content:
{trimmed_content}

Give:
1. What this file is
2. Main points
3. Important details
4. Simple explanation
""".strip()

    return safe_model_call(prompt)


# -----------------------------
# File reading
# -----------------------------
def try_decode_bytes(data: bytes) -> str:
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252"]
    for enc in encodings:
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode this file.")


def extract_text_from_pdf(data: bytes) -> str:
    extracted_pages: List[str] = []

    try:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                extracted_pages.append(text)
    except Exception as e:
        logger.warning("PyPDF2 extraction failed: %s", e)

    extracted_joined = remove_common_pdf_noise(extracted_pages)
    extracted_joined = clean_text(extracted_joined)

    if len(extracted_joined.strip()) > 120:
        return extracted_joined

    try:
        if POPPLER_PATH:
            images = convert_from_bytes(data, poppler_path=POPPLER_PATH)
        else:
            images = convert_from_bytes(data)

        ocr_pages: List[str] = []

        for i, img in enumerate(images[:MAX_OCR_PAGES]):
            try:
                gray = img.convert("L")
                page_text = pytesseract.image_to_string(gray, config="--oem 3 --psm 6")
                page_text = clean_text(page_text)
                if page_text and looks_useful(page_text):
                    ocr_pages.append(page_text)
            except Exception as page_error:
                logger.warning("OCR failed on page %s: %s", i + 1, page_error)

        ocr_text = remove_common_pdf_noise(ocr_pages)
        ocr_text = remove_repeated_lines(clean_text(ocr_text))

        if ocr_text:
            return ocr_text

    except Exception as e:
        logger.warning("OCR conversion failed: %s", e)
        raise ValueError(
            "Could not read this PDF. It may be scanned, protected, or Poppler/Tesseract may be missing."
        )

    raise ValueError("No readable text found in the PDF.")


def extract_text_from_docx(data: bytes) -> str:
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    return "\n".join(parts).strip()


def extract_text_from_csv(data: bytes) -> str:
    text = try_decode_bytes(data)
    reader = csv.reader(io.StringIO(text))
    rows = []
    for row in reader:
        cleaned = [cell.strip() for cell in row if cell and cell.strip()]
        if cleaned:
            rows.append(" | ".join(cleaned))
    return "\n".join(rows).strip()


def extract_text_from_json(data: bytes) -> str:
    text = try_decode_bytes(data)
    obj = json.loads(text)
    return json.dumps(obj, indent=2, ensure_ascii=False)


def extract_text_from_html(data: bytes) -> str:
    text = try_decode_bytes(data)
    soup = BeautifulSoup(text, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup.get_text(separator="\n").strip()


def validate_file(file: UploadFile, data: bytes):
    filename = file.filename or "uploaded_file"
    ext = os.path.splitext(filename.lower())[1]

    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {ext}")

    if not data:
        raise ValueError("Uploaded file is empty.")

    if len(data) > MAX_UPLOAD_SIZE_BYTES:
        raise ValueError(f"File too large. Max allowed size is {MAX_UPLOAD_SIZE_MB} MB.")


def extract_text(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    data = file.file.read()

    validate_file(file, data)

    if filename.endswith(".pdf"):
        text = extract_text_from_pdf(data)
        text = clean_text(text)
        text = remove_repeated_lines(text)
        return text.strip()

    if filename.endswith(".docx"):
        text = extract_text_from_docx(data)
        text = clean_text(text)
        return text.strip()

    if filename.endswith(".csv"):
        text = extract_text_from_csv(data)
        text = clean_text(text)
        return text.strip()

    if filename.endswith(".json"):
        text = extract_text_from_json(data)
        text = clean_text(text)
        return text.strip()

    if filename.endswith(".html") or filename.endswith(".htm"):
        text = extract_text_from_html(data)
        text = clean_text(text)
        return text.strip()

    if filename.endswith((".md", ".txt", ".py", ".js", ".css", ".ts", ".java", ".c", ".cpp")):
        text = try_decode_bytes(data)
        text = clean_text(text)
        return text.strip()

    text = try_decode_bytes(data)
    text = clean_text(text)
    return text.strip()


# -----------------------------
# Prompt builder
# -----------------------------
def build_labeled_context(sources: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, src in enumerate(sources, start=1):
        src["source_label"] = f"Source {i}"
        blocks.append(
            f"[{src['source_label']}] File: {src['file_name']} | Chunk: {src['chunk_index']}\n{src['text']}"
        )
    return "\n\n---\n\n".join(blocks)


def build_chat_prompt(
    kb_name: str,
    chat_history: List[Dict[str, str]],
    relevant_sources: List[Dict[str, Any]],
    user_question: str,
    long_term_memory_text: str
) -> str:
    relevant_text = build_labeled_context(relevant_sources)
    relevant_text = smart_trim(relevant_text, max_chars=6500)
    history_text = format_chat_history(chat_history, max_turns=8)
    question_type = classify_question(user_question)

    prompt = f"""
You are Jarvis, a careful AI assistant.

You are answering questions from a multi-file knowledge base.

Rules:
- Answer the CURRENT question only.
- Use only the retrieved sections for factual claims.
- Use recent conversation and saved memory only as support, not as primary evidence.
- Mention uncertainty clearly if the retrieved evidence is partial or weak.
- If multiple files contribute to the answer, combine them clearly.
- If files differ or conflict, say so clearly.
- Do not invent details not present in the retrieved sections.
- When you use a fact, cite it inline like [Source 1] or [Source 2].
- Use only the provided source labels.
- End with a short Sources section mapping source labels to file names.
- If the answer is not clearly present, say: "I could not find that clearly in the uploaded knowledge base."
- Keep the answer focused, natural, and useful.

Knowledge base:
{kb_name}

Question type:
{question_type}

Saved memory:
{long_term_memory_text}

Recent conversation:
{history_text}

Retrieved sections:
{relevant_text}

User question:
{user_question}

Answer directly.
""".strip()

    return prompt


def prepare_grounded_sources(query: str, kb_id: str, final_k: int = 4) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    retrieved = get_relevant_chunks_chroma(kb_id=kb_id, query=query, top_k=8)
    reranked = rerank_sources(query, retrieved)
    selected = reranked[:final_k]

    display_sources = []
    for i, src in enumerate(selected, start=1):
        display_sources.append({
            "label": f"Source {i}",
            "file_name": src["file_name"],
            "file_id": src["file_id"],
            "chunk_index": src["chunk_index"],
            "similarity": src["similarity"],
            "rank_score": src.get("rank_score"),
            "preview": src["preview"]
        })

    return selected, display_sources


# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def home(request: Request):
    session_id = request.cookies.get("session_id")
    response = templates.TemplateResponse("index.html", {"request": request})

    if not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie("session_id", session_id, httponly=True)
        create_session(session_id)
    elif not get_session_data(session_id):
        create_session(session_id)

    return response


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gen_model": GEN_MODEL,
        "embed_model": EMBED_MODEL,
        "vector_db": "ChromaDB",
        "database": "SQLite"
    }


@app.post("/upload")
def upload_file(request: Request, response: Response, file: UploadFile = File(...)):
    session_id = get_or_create_session_id(request, response)
    kb_id = resolve_kb_id(session_id)

    try:
        content = extract_text(file)
    except Exception as e:
        return JSONResponse({"error": f"File reading failed: {str(e)}"}, status_code=400)

    if not content.strip():
        return JSONResponse({"error": "No readable content found."}, status_code=400)

    chunks = chunk_text(content)
    if not chunks:
        return JSONResponse({"error": "Could not create useful chunks from the file."}, status_code=400)

    file_id = str(uuid.uuid4())

    try:
        doc_ids = store_chunks_in_chroma(
            kb_id=kb_id,
            file_id=file_id,
            file_name=file.filename or "uploaded_file",
            chunks=chunks
        )
    except Exception as e:
        return JSONResponse({"error": f"Vector storage failed: {str(e)}"}, status_code=500)

    with get_db() as conn:
        conn.execute("""
            INSERT INTO kb_files (file_id, kb_id, file_name, chunk_count, doc_ids_count)
            VALUES (?, ?, ?, ?, ?)
        """, (
            file_id,
            kb_id,
            file.filename or "uploaded_file",
            len(chunks),
            len(doc_ids)
        ))

    update_kb_total_chunks(kb_id)

    try:
        explanation = build_file_summary(file.filename or "uploaded_file", content)
    except Exception as e:
        return JSONResponse({"error": f"AI generation failed: {str(e)}"}, status_code=500)

    update_session_meta(
        session_id,
        last_uploaded_file=file.filename or "uploaded_file",
        last_file_summary=explanation
    )

    add_chat_message(
        session_id,
        "assistant",
        f"I added {file.filename or 'uploaded_file'} to the knowledge base. {explanation}"
    )

    audio_url = None
    audio_error = None
    try:
        audio_url = text_to_audio_file(explanation, "en")
    except Exception as e:
        audio_error = str(e)
        logger.warning("Upload audio generation failed: %s", audio_error)

    kb = get_kb_data(kb_id)

    return {
        "filename": file.filename or "uploaded_file",
        "file_id": file_id,
        "kb_id": kb_id,
        "kb_name": kb.get("name", "My Knowledge Base"),
        "file_count": len(kb.get("files", [])),
        "total_chunks": kb.get("total_chunks", 0),
        "files": kb.get("files", []),
        "explanation": explanation,
        "audio_url": audio_url,
        "audio_error": audio_error,
        "chunks_created": len(chunks),
        "vectors_stored": len(doc_ids),
        "vector_db": "ChromaDB"
    }


@app.post("/chat")
def chat(request: Request, response: Response, chat: ChatRequest):
    session_id = get_or_create_session_id(request, response)
    kb_id = resolve_kb_id(session_id, chat.kb_id)
    kb = get_kb_data(kb_id)

    if not chat.question.strip():
        return JSONResponse({"error": "Question is required."}, status_code=400)

    if not kb.get("files"):
        return JSONResponse({"error": "Upload at least one file first."}, status_code=400)

    try:
        relevant_sources, display_sources = prepare_grounded_sources(chat.question, kb_id, final_k=4)
    except Exception as e:
        return JSONResponse({"error": f"Semantic retrieval failed: {str(e)}"}, status_code=500)

    prompt = build_chat_prompt(
        kb_name=kb.get("name", "My Knowledge Base"),
        chat_history=get_chat_history(session_id),
        relevant_sources=relevant_sources,
        user_question=chat.question,
        long_term_memory_text=memory_to_text(session_id)
    )

    try:
        answer = safe_model_call(prompt)
    except Exception as e:
        return JSONResponse({"error": f"AI generation failed: {str(e)}"}, status_code=500)

    add_chat_message(session_id, "user", chat.question)
    add_chat_message(session_id, "assistant", answer)

    lang = "hi" if chat.language == "hi-IN" else "en"

    audio_url = None
    audio_error = None
    try:
        audio_url = text_to_audio_file(answer, lang)
    except Exception as e:
        audio_error = str(e)
        logger.warning("Chat audio generation failed: %s", audio_error)

    return {
        "answer": answer,
        "audio_url": audio_url,
        "audio_error": audio_error,
        "sources": display_sources,
        "used_relevant_chunks": len(relevant_sources),
        "retrieval_mode": "chromadb_multifile_kb_reranked"
    }


@app.post("/chat-stream")
def chat_stream(request: Request, response: Response, chat: ChatRequest):
    session_id = get_or_create_session_id(request, response)
    kb_id = resolve_kb_id(session_id, chat.kb_id)
    kb = get_kb_data(kb_id)

    if not chat.question.strip():
        def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Question is required.'})}\n\n"
        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    if not kb.get("files"):
        def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Upload at least one file first.'})}\n\n"
        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    try:
        relevant_sources, display_sources = prepare_grounded_sources(chat.question, kb_id, final_k=4)
    except Exception as e:
        def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': f'Semantic retrieval failed: {str(e)}'})}\n\n"
        return StreamingResponse(
            error_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    prompt = build_chat_prompt(
        kb_name=kb.get("name", "My Knowledge Base"),
        chat_history=get_chat_history(session_id),
        relevant_sources=relevant_sources,
        user_question=chat.question,
        long_term_memory_text=memory_to_text(session_id)
    )

    lang = "hi" if chat.language == "hi-IN" else "en"

    def event_generator():
        full_answer = ""

        try:
            yield f"data: {json.dumps({'type': 'start'})}\n\n"

            for token in stream_model_call(prompt):
                full_answer += token
                yield f"data: {json.dumps({'type': 'token', 'token': token})}\n\n"

            audio_url = None
            audio_error = None

            try:
                audio_url = text_to_audio_file(full_answer, lang)
            except Exception as e:
                audio_error = str(e)
                logger.warning("Streaming chat audio generation failed: %s", audio_error)

            add_chat_message(session_id, "user", chat.question)
            add_chat_message(session_id, "assistant", full_answer)

            yield f"data: {json.dumps({
                'type': 'done',
                'answer': full_answer,
                'sources': display_sources,
                'audio_url': audio_url,
                'audio_error': audio_error,
                'used_relevant_chunks': len(relevant_sources),
                'retrieval_mode': 'chromadb_multifile_kb_reranked'
            })}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': f'AI generation failed: {str(e)}'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/chat-history")
def chat_history_route(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    return {"history": get_chat_history(session_id, max_turns=50)}


@app.get("/session-info")
def session_info(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    session = get_session_data(session_id)
    kb_id = resolve_kb_id(session_id)
    kb = get_kb_data(kb_id)

    return {
        "session_id": session_id,
        "kb_id": kb_id,
        "kb_name": kb.get("name", "My Knowledge Base"),
        "file_count": len(kb.get("files", [])),
        "files": kb.get("files", []),
        "total_chunks": kb.get("total_chunks", 0),
        "last_uploaded_file": session.get("last_uploaded_file", "") if session else "",
        "last_file_summary": session.get("last_file_summary", "") if session else "",
        "chat_count": len(get_chat_history(session_id, max_turns=100)),
        "memory_count": len(list_memory(session_id)),
        "vector_db": "ChromaDB",
        "database": "SQLite"
    }


@app.delete("/delete-file/{file_id}")
def delete_file_route(file_id: str, request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    kb_id = resolve_kb_id(session_id)

    with get_db() as conn:
        row = conn.execute("""
            SELECT file_id, file_name
            FROM kb_files
            WHERE file_id = ? AND kb_id = ?
        """, (file_id, kb_id)).fetchone()

        if not row:
            return JSONResponse({"error": "File not found in this knowledge base."}, status_code=404)

    try:
        delete_file_docs(kb_id, file_id)
    except Exception as e:
        return JSONResponse({"error": f"Vector deletion failed: {str(e)}"}, status_code=500)

    with get_db() as conn:
        conn.execute("DELETE FROM kb_files WHERE file_id = ? AND kb_id = ?", (file_id, kb_id))

    update_kb_total_chunks(kb_id)
    kb = get_kb_data(kb_id)

    return {
        "message": f"{row['file_name']} deleted.",
        "deleted_file_id": file_id,
        "kb_id": kb_id,
        "file_count": len(kb.get("files", [])),
        "files": kb.get("files", []),
        "total_chunks": kb.get("total_chunks", 0)
    }


@app.post("/reset-chat")
def reset_chat(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    clear_chat_history(session_id)
    return {"message": "Chat history reset. Knowledge base preserved."}


@app.post("/reset-kb")
def reset_kb(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    session = get_session_data(session_id)
    old_kb_id = session.get("kb_id") if session else None

    if old_kb_id:
        delete_kb_docs(old_kb_id)
        delete_kb_record(old_kb_id)

    new_kb_id = create_knowledge_base("My Knowledge Base")
    new_kb = get_kb_data(new_kb_id)

    clear_chat_history(session_id)
    update_session_meta(
        session_id,
        kb_id=new_kb_id,
        kb_name=new_kb.get("name", "My Knowledge Base"),
        last_uploaded_file="",
        last_file_summary=""
    )

    return {
        "message": "Knowledge base reset.",
        "kb_id": new_kb_id,
        "kb_name": new_kb.get("name", "My Knowledge Base"),
        "file_count": 0,
        "files": [],
        "total_chunks": 0,
        "last_uploaded_file": "",
        "last_file_summary": "",
        "chat_count": 0,
        "memory_count": len(list_memory(session_id)),
        "vector_db": "ChromaDB",
        "database": "SQLite"
    }


@app.get("/memory")
def list_memory_route(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    return {"items": list_memory(session_id)}


@app.delete("/memory")
def delete_memory_route(request: Request, response: Response, payload: DeleteMemoryRequest):
    session_id = get_or_create_session_id(request, response)
    key = payload.key.strip()

    if not key:
        return JSONResponse({"error": "Memory key is required."}, status_code=400)

    delete_memory(session_id, key)
    return {"message": "Memory deleted.", "key": key}


@app.post("/memory/save")
def save_memory_route(request: Request, response: Response, payload: SaveMemoryRequest):
    session_id = get_or_create_session_id(request, response)

    key = payload.key.strip()
    value = payload.value.strip()

    if not key or not value:
        return JSONResponse({"error": "Both key and value are required."}, status_code=400)

    save_memory(session_id, key, value)
    return {"message": "Memory saved.", "key": key, "value": value}


@app.get("/memory/list")
def list_memory_route(request: Request, response: Response):
    session_id = get_or_create_session_id(request, response)
    return {"memory": list_memory(session_id)}


@app.post("/memory/delete")
def delete_memory_route(request: Request, response: Response, payload: DeleteMemoryRequest):
    session_id = get_or_create_session_id(request, response)

    key = payload.key.strip()
    if not key:
        return JSONResponse({"error": "Key is required."}, status_code=400)

    delete_memory(session_id, key)
    return {"message": "Memory deleted.", "key": key}
