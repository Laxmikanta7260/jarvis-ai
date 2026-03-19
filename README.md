# 🤖 Jarvis – AI Knowledge Assistant (RAG + LLM)

A **Retrieval-Augmented Generation (RAG)** based AI assistant that allows users to upload documents and ask questions.
It uses **embeddings, vector search (ChromaDB), and a local LLM (Ollama – Llama 3)** to generate accurate, context-aware answers with optional voice interaction.

---

## 🎯 Why This Project?

Traditional AI chatbots often **hallucinate** and give inaccurate answers.
This project solves that by using **RAG (Retrieval-Augmented Generation)** to ground responses in user-provided documents, making answers **reliable and context-aware**.

---

## ✨ Features

* 📄 Upload and process documents (PDF, TXT, etc.)
* 🔍 Semantic search using embeddings
* 🧠 Retrieval-Augmented Generation (RAG)
* ⚡ Local LLM inference using Ollama (Llama 3)
* 🧾 Context-aware answers from user documents
* 🎤 Voice input support
* 🔊 Text-to-speech output
* 🌐 Web interface using FastAPI

---

## 🧠 How It Works

1. User uploads a document
2. Text is extracted and split into chunks
3. Embeddings are generated for each chunk
4. Stored in ChromaDB vector database
5. User asks a question
6. Relevant chunks are retrieved
7. Context + query sent to LLM (Llama 3 via Ollama)
8. AI generates a grounded, accurate answer

---

## 🏗️ Architecture

```
User → FastAPI → Document Processing → Embeddings → ChromaDB  
     → Retriever → LLM (Ollama) → Response (Text/Voice)
```

---

## 🛠️ Tech Stack

* **Backend:** FastAPI (Python)
* **LLM:** Ollama (Llama 3)
* **Vector Database:** ChromaDB
* **Embeddings:** Sentence Transformers
* **Speech Recognition:** SpeechRecognition
* **Text-to-Speech:** pyttsx3

---

## ⚙️ Installation & Setup

```bash
git clone https://github.com/Laxmikanta7260/jarvis-ai.git
cd jarvis-ai
pip install -r requirements.txt
ollama run llama3
uvicorn main:app --reload
```

Open in browser:
http://127.0.0.1:8000

---

## 🧪 Usage

1. Open the app in your browser
2. Upload a document
3. Ask questions
4. Get context-aware answers

---

## 🚀 Future Improvements

* 🧠 Add conversation memory across sessions
* 📚 Multi-document querying
* 📌 Show source citations for answers
* 🌍 Deploy as a cloud-based app
* 🎨 Improve UI with React

---

## 👨‍💻 Author

**Laxmikanta**
GitHub: https://github.com/Laxmikanta7260
