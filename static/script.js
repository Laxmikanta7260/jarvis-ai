const $ = (id) => document.getElementById(id);

const els = {
    uploadBtn: $("uploadBtn"),
    fileInput: $("fileInput"),
    fileNameText: $("fileName"),
    statusText: $("status"),
    explanationBox: $("explanation"),
    audioPlayer: $("audioPlayer"),
    copyBtn: $("copyBtn"),
    downloadAudioBtn: $("downloadAudioBtn"),
    resultFileName: $("resultFileName"),

    sidebarKbName: $("sidebarKbName"),
    sidebarStatus: $("sidebarStatus"),
    fileCountInfo: $("fileCountInfo"),
    chunkInfo: $("chunkInfo"),
    chatCountInfo: $("chatCountInfo"),
    kbFileList: $("kbFileList"),

    chatBox: $("chatBox"),
    chatInput: $("chatInput"),
    sendBtn: $("sendBtn"),
    micBtn: $("micBtn"),
    autoPlayToggle: $("autoPlayToggle"),
    resetChatBtn: $("resetChatBtn"),
    resetKbBtn: $("resetKbBtn"),
    languageSelect: $("languageSelect"),
    typingIndicator: $("typingIndicator"),

    uploadZone: $("uploadZone"),
    activityLog: $("activityLog")
};

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
    recognition: null,
    isListening: false,
    finalTranscript: "",
    currentKbId: null,
    currentKbName: "My Knowledge Base",
    currentFileCount: 0,
    isUploading: false,
    isChatting: false,
    lastUploadedFile: "",
    lastFileSummary: ""
};

const DEFAULT_EXPLANATION =
    "Upload a file to add it into the knowledge base and see its explanation here...";

const ALLOWED_EXTENSIONS = [
    ".py", ".cpp", ".c", ".java", ".js", ".ts",
    ".txt", ".pdf", ".docx", ".csv", ".json",
    ".md", ".html", ".htm", ".css"
];

/* -----------------------------
   General Helpers
----------------------------- */
function setStatus(message, type = "") {
    const el = els.statusText;
    if (!el) return;

    el.textContent = message;
    el.style.borderColor = "";
    el.style.background = "";
    el.style.color = "";

    if (type === "success") {
        el.style.borderColor = "rgba(87, 242, 193, 0.28)";
        el.style.background = "rgba(87, 242, 193, 0.08)";
        el.style.color = "#d9fff2";
    } else if (type === "warning") {
        el.style.borderColor = "rgba(255, 196, 86, 0.28)";
        el.style.background = "rgba(255, 196, 86, 0.08)";
        el.style.color = "#fff1c9";
    } else if (type === "error") {
        el.style.borderColor = "rgba(255, 95, 122, 0.28)";
        el.style.background = "rgba(255, 95, 122, 0.08)";
        el.style.color = "#ffd9e0";
    }
}

function addActivity(message) {
    if (!els.activityLog) return;

    const item = document.createElement("div");
    item.className = "activity-item";
    item.textContent = `${new Date().toLocaleTimeString()} — ${message}`;
    els.activityLog.prepend(item);

    while (els.activityLog.children.length > 12) {
        els.activityLog.removeChild(els.activityLog.lastChild);
    }
}

function setTyping(show) {
    if (!els.typingIndicator) return;
    els.typingIndicator.classList.toggle("hidden", !show);
}

function autoResizeTextarea() {
    if (!els.chatInput) return;
    els.chatInput.style.height = "auto";
    els.chatInput.style.height = `${Math.min(els.chatInput.scrollHeight, 180)}px`;
}

function resetAudioUI() {
    if (els.audioPlayer) {
        els.audioPlayer.pause();
        els.audioPlayer.removeAttribute("src");
        els.audioPlayer.load();
    }

    if (els.downloadAudioBtn) {
        els.downloadAudioBtn.href = "#";
        els.downloadAudioBtn.style.display = "none";
    }
}

function clearChatBox() {
    if (!els.chatBox) return;
    els.chatBox.innerHTML = "";
}

function addWelcomeMessage(
    text = "Hello. Add one or more files to the knowledge base, then ask me questions across all of them."
) {
    addChatMessage("jarvis", text);
}

function parseJsonSafeText(text) {
    try {
        return JSON.parse(text);
    } catch {
        return null;
    }
}

async function parseJsonSafe(response) {
    try {
        return await response.json();
    } catch {
        return {};
    }
}

function isAllowedFile(fileName = "") {
    const lower = fileName.toLowerCase();
    return ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

function setUploadLoading(isLoading) {
    state.isUploading = isLoading;

    if (els.uploadBtn) {
        els.uploadBtn.disabled = isLoading;
        els.uploadBtn.textContent = isLoading ? "Adding..." : "Add File";
    }

    if (els.fileInput) {
        els.fileInput.disabled = isLoading;
    }
}

function setSendLoading(isLoading) {
    state.isChatting = isLoading;

    if (els.sendBtn) {
        els.sendBtn.disabled = isLoading;
        els.sendBtn.classList.toggle("loading", isLoading);
    }

    if (els.chatInput) els.chatInput.disabled = isLoading;
    if (els.micBtn) els.micBtn.disabled = isLoading;
}

async function apiFetch(url, options = {}) {
    const response = await fetch(url, {
        cache: "no-store",
        ...options
    });

    const data = await parseJsonSafe(response);
    return { response, data };
}

function escapeHtml(text = "") {
    return String(text)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}

function formatAnswerText(text = "") {
    return escapeHtml(text).replace(/\n/g, "<br>");
}

function setExplanation(text = "") {
    if (!els.explanationBox) return;
    els.explanationBox.textContent = text || DEFAULT_EXPLANATION;
}

function setLastAddedFile(fileName = "") {
    if (!els.resultFileName) return;
    els.resultFileName.textContent = fileName
        ? `Last added file: ${fileName}`
        : "Last added file: —";
}

function scrollChatToBottom() {
    if (!els.chatBox) return;
    requestAnimationFrame(() => {
        els.chatBox.scrollTop = els.chatBox.scrollHeight;
    });
}

/* -----------------------------
   Session / Sidebar
----------------------------- */
async function deleteKbFile(fileId, fileName) {
    if (!fileId || state.isUploading || state.isChatting) return;

    const confirmed = window.confirm(`Delete "${fileName}" from the knowledge base?`);
    if (!confirmed) return;

    setStatus(`Deleting ${fileName}...`, "warning");
    addActivity(`Deleting file: ${fileName}`);

    try {
        const { response, data } = await apiFetch(`/delete-file/${encodeURIComponent(fileId)}`, {
            method: "DELETE"
        });

        if (!response.ok) {
            throw new Error(data.error || `Delete failed with status ${response.status}`);
        }

        updateSessionUI({
            kb_id: data.kb_id,
            kb_name: state.currentKbName,
            file_count: data.file_count,
            files: data.files,
            total_chunks: data.total_chunks,
            chat_count: Number(els.chatCountInfo?.textContent || 0),
            last_uploaded_file: state.lastUploadedFile,
            last_file_summary: state.lastFileSummary
        });

        setStatus(data.message || "File deleted.", "success");
        addActivity(data.message || `${fileName} deleted.`);

        if (state.currentFileCount === 0) {
            state.lastUploadedFile = "";
            state.lastFileSummary = "";
            clearChatBox();
            addWelcomeMessage();
            setExplanation(DEFAULT_EXPLANATION);
            setLastAddedFile("");
            resetAudioUI();
        }
    } catch (error) {
        console.error(error);
        setStatus(error.message || "Failed to delete file.", "error");
        addActivity(`Delete failed for ${fileName}.`);
        addChatMessage("jarvis", `⚠️ Failed to delete ${fileName}. ${error.message || ""}`.trim());
    }
}

function renderKbFiles(files = []) {
    if (!els.kbFileList) return;

    els.kbFileList.innerHTML = "";

    if (!files.length) {
        els.kbFileList.innerHTML = `<div class="kb-empty">No files uploaded yet.</div>`;
        return;
    }

    files.forEach((file) => {
        const item = document.createElement("div");
        item.className = "kb-file-item";

        const left = document.createElement("div");
        left.className = "kb-file-main";

        const name = document.createElement("div");
        name.className = "kb-file-name";
        name.textContent = file.file_name || "Unnamed file";

        const meta = document.createElement("div");
        meta.className = "kb-file-meta";
        meta.textContent = `${file.chunk_count || 0} chunks`;

        left.appendChild(name);
        left.appendChild(meta);

        const right = document.createElement("div");
        right.className = "kb-file-actions";

        const deleteBtn = document.createElement("button");
        deleteBtn.type = "button";
        deleteBtn.className = "ghost-btn kb-delete-btn";
        deleteBtn.textContent = "Delete";
        deleteBtn.disabled = state.isUploading || state.isChatting;
        deleteBtn.addEventListener("click", () => {
            deleteKbFile(file.file_id, file.file_name || "Unnamed file");
        });

        right.appendChild(deleteBtn);

        item.appendChild(left);
        item.appendChild(right);
        els.kbFileList.appendChild(item);
    });
}

function updateSessionUI(data) {
    state.currentKbId = data.kb_id || null;
    state.currentKbName = data.kb_name || "My Knowledge Base";
    state.currentFileCount = data.file_count || 0;

    if (typeof data.last_uploaded_file === "string") {
        state.lastUploadedFile = data.last_uploaded_file;
    }

    if (typeof data.last_file_summary === "string") {
        state.lastFileSummary = data.last_file_summary;
    }

    if (els.sidebarKbName) {
        els.sidebarKbName.textContent = state.currentKbName;
    }

    if (els.sidebarStatus) {
        els.sidebarStatus.textContent =
            state.currentFileCount > 0
                ? `${state.currentFileCount} file(s) loaded in the knowledge base`
                : "No files added yet";
    }

    if (els.fileCountInfo) els.fileCountInfo.textContent = data.file_count ?? 0;
    if (els.chunkInfo) els.chunkInfo.textContent = data.total_chunks ?? 0;
    if (els.chatCountInfo) els.chatCountInfo.textContent = data.chat_count ?? 0;

    renderKbFiles(data.files || []);

    if (typeof data.last_uploaded_file === "string") {
        setLastAddedFile(state.lastUploadedFile);
    }

    if (typeof data.last_file_summary === "string") {
        setExplanation(state.lastFileSummary || DEFAULT_EXPLANATION);
    }
}

async function loadSessionInfo() {
    try {
        const { response, data } = await apiFetch("/session-info", { method: "GET" });

        if (!response.ok) {
            throw new Error(`Session info failed with status ${response.status}`);
        }

        updateSessionUI(data);
        return data;
    } catch (error) {
        console.error("Failed to load session info:", error);
        setStatus("Failed to load session info.", "error");
        addActivity("Failed to load session info.");
        return null;
    }
}

async function loadChatHistory() {
    try {
        const { response, data } = await apiFetch("/chat-history", { method: "GET" });

        if (!response.ok) {
            throw new Error(`Chat history failed with status ${response.status}`);
        }

        clearChatBox();

        const history = data.history || [];
        if (!history.length) {
            addWelcomeMessage();
            return;
        }

        history.forEach((msg) => {
            const sender = msg.role === "user" ? "user" : "jarvis";
            addChatMessage(sender, msg.content || "");
        });

        scrollChatToBottom();
    } catch (error) {
        console.error("Failed to load chat history:", error);
        clearChatBox();
        addWelcomeMessage();
        addActivity("Failed to restore previous chat.");
    }
}

/* -----------------------------
   Chat UI
----------------------------- */
function createAvatar(sender) {
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = sender === "user" ? "U" : "J";
    return avatar;
}

function createChatMessageElement(sender, text = "", useHtml = false) {
    const messageDiv = document.createElement("div");
    messageDiv.className = `chat-message ${sender}`;

    const avatar = createAvatar(sender);

    const messageStack = document.createElement("div");
    messageStack.className = "message-stack";

    const meta = document.createElement("div");
    meta.className = "message-meta";
    meta.textContent = sender === "user" ? "You" : "Jarvis";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    if (useHtml) {
        bubble.innerHTML = text;
    } else {
        bubble.textContent = text;
    }

    messageStack.appendChild(meta);
    messageStack.appendChild(bubble);
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(messageStack);

    if (els.chatBox) {
        els.chatBox.appendChild(messageDiv);
        scrollChatToBottom();
    }

    return { messageDiv, bubble, messageStack };
}

function renderInlineSources(container, sources = []) {
    if (!container || !sources.length) return;

    const wrapper = document.createElement("div");
    wrapper.className = "inline-sources";

    const title = document.createElement("div");
    title.className = "inline-sources-title";
    title.textContent = "Sources used";
    wrapper.appendChild(title);

    sources.forEach((src, index) => {
        const card = document.createElement("div");
        card.className = "inline-source-card";

        const meta = document.createElement("div");
        meta.className = "inline-source-meta";

        const labelText = src.label ? ` • ${src.label}` : "";
        const similarityText =
            src.similarity !== null && src.similarity !== undefined
                ? ` • similarity ${src.similarity}`
                : "";
        const rankText =
            src.rank_score !== null && src.rank_score !== undefined
                ? ` • rank ${src.rank_score}`
                : "";

        meta.textContent = `${index + 1}. ${src.file_name || "Unknown file"} • chunk ${src.chunk_index ?? "?"}${labelText}${similarityText}${rankText}`;

        const preview = document.createElement("div");
        preview.className = "inline-source-preview";
        preview.textContent = src.preview || "";

        card.appendChild(meta);
        card.appendChild(preview);
        wrapper.appendChild(card);
    });

    container.appendChild(wrapper);
}

function attachAudioToMessage(messageStack, audioUrl) {
    if (!messageStack || !audioUrl) return;

    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = audioUrl;
    audio.className = "message-audio";
    audio.style.marginTop = "10px";
    audio.style.width = "100%";
    messageStack.appendChild(audio);

    if (els.autoPlayToggle?.checked) {
        setTimeout(() => {
            audio.play().catch(() => {});
        }, 120);
    }
}

function addChatMessage(sender, text, audioUrl = null, sources = [], useHtml = false) {
    if (!els.chatBox) return;

    const { messageStack } = createChatMessageElement(sender, text || "", useHtml);

    if (sender === "jarvis" && audioUrl) {
        attachAudioToMessage(messageStack, audioUrl);
    }

    if (sender === "jarvis" && sources.length) {
        renderInlineSources(messageStack, sources);
    }
}

/* -----------------------------
   Upload
----------------------------- */
function bindFileSelection(file) {
    if (els.fileNameText) {
        els.fileNameText.textContent = file ? `Selected file: ${file.name}` : "No file selected";
    }

    if (!file) {
        setStatus("Ready.");
        return;
    }

    if (!isAllowedFile(file.name)) {
        setStatus(
            "Unsupported file type. Use PDF, TXT, PY, CPP, C, JAVA, JS, TS, DOCX, CSV, JSON, MD, HTML, or CSS.",
            "error"
        );
        addActivity(`Rejected file type: ${file.name}`);
        return;
    }

    setStatus("File selected. Ready to add.", "success");
    addActivity(`File selected: ${file.name}`);
}

async function uploadSelectedFile() {
    if (state.isUploading || state.isChatting) return;

    const file = els.fileInput?.files?.[0];
    if (!file) {
        setStatus("Please select a file first.", "warning");
        return;
    }

    if (!isAllowedFile(file.name)) {
        setStatus("Please upload a supported file type.", "error");
        return;
    }

    const previousFileCount = state.currentFileCount;

    const formData = new FormData();
    formData.append("file", file);

    setStatus("Jarvis is adding this file to the knowledge base, chunking it, and storing vectors...", "warning");
    setExplanation("Processing file and adding it to the knowledge base...");

    resetAudioUI();
    setUploadLoading(true);
    addActivity(`Adding ${file.name} to knowledge base...`);

    try {
        const response = await fetch("/upload", {
            method: "POST",
            body: formData
        });

        const data = await parseJsonSafe(response);

        if (!response.ok || data.error) {
            setExplanation(DEFAULT_EXPLANATION);
            setStatus(`Error: ${data.error || `Upload failed with status ${response.status}`}`, "error");
            addActivity("Upload failed.");
            addChatMessage("jarvis", `⚠️ Upload failed for ${file.name}. ${data.error || ""}`.trim());
            return;
        }

        state.lastUploadedFile = data.filename || "";
        state.lastFileSummary = data.explanation || "";

        setExplanation(data.explanation || "No explanation generated.");
        setLastAddedFile(data.filename || "");

        updateSessionUI({
            kb_id: data.kb_id,
            kb_name: data.kb_name,
            file_count: data.file_count,
            files: data.files,
            total_chunks: data.total_chunks,
            chat_count: Number(els.chatCountInfo?.textContent || 0),
            last_uploaded_file: data.filename || "",
            last_file_summary: data.explanation || ""
        });

        if (data.audio_url && els.audioPlayer && els.downloadAudioBtn) {
            els.audioPlayer.src = data.audio_url;
            els.audioPlayer.load();
            els.downloadAudioBtn.href = data.audio_url;
            els.downloadAudioBtn.style.display = "inline-flex";
        } else {
            resetAudioUI();
        }

        if (previousFileCount === 0 && data.file_count === 1) {
            clearChatBox();
            addWelcomeMessage(
                "The first file has been added. Keep uploading more files or ask me about the knowledge base."
            );
        } else {
            addChatMessage(
                "jarvis",
                `${data.filename} was added to the knowledge base. You now have ${data.file_count} file(s) available for retrieval.`
            );
        }

        if (data.audio_error) {
            setStatus("File added. Explanation is ready, but audio could not be generated.", "warning");
            addActivity("File added. Audio failed.");
        } else {
            setStatus("File added to the knowledge base successfully.", "success");
            addActivity(`Added file: ${data.filename}`);
        }

        addActivity(`Knowledge base now has ${data.file_count} file(s).`);
        addActivity(`Total chunks: ${data.total_chunks}`);

        if (typeof data.vectors_stored !== "undefined") {
            addActivity(`Stored ${data.vectors_stored} vectors in ${data.vector_db || "database"}.`);
        }

        if (els.fileInput) els.fileInput.value = "";
        if (els.fileNameText) els.fileNameText.textContent = "No file selected";
    } catch (error) {
        console.error(error);
        setExplanation(DEFAULT_EXPLANATION);
        setStatus("Something went wrong while adding the file.", "error");
        addActivity("Unexpected upload error.");
        addChatMessage("jarvis", "⚠️ Something went wrong while adding the file.");
    } finally {
        setUploadLoading(false);
        await loadSessionInfo();
    }
}

/* -----------------------------
   Chat
----------------------------- */
async function fallbackChat(question) {
    const { response, data } = await apiFetch("/chat", {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({
            question,
            language: els.languageSelect ? els.languageSelect.value : "en-US",
            kb_id: state.currentKbId
        })
    });

    if (!response.ok) {
        throw new Error(data.error || `Chat failed with status ${response.status}`);
    }

    addChatMessage(
        "jarvis",
        data.answer || "No response generated.",
        data.audio_url || null,
        data.sources || []
    );

    if (data.audio_error) {
        setStatus("Reply ready, but audio could not be generated.", "warning");
        addActivity("Fallback reply generated. Audio unavailable.");
    } else {
        setStatus("Reply ready (fallback mode).", "success");
        addActivity("Fallback reply generated successfully.");
    }

    if (typeof data.used_relevant_chunks !== "undefined") {
        addActivity(`Retrieval used ${data.used_relevant_chunks} chunk(s).`);
    }

    await loadSessionInfo();
}

async function sendMessage() {
    if (state.isChatting || state.isUploading) return;

    const question = els.chatInput?.value.trim() || "";
    if (!question) return;

    if (!state.currentKbId) {
        setStatus("Session not ready yet. Please refresh the page.", "warning");
        addActivity("Chat blocked: missing kb_id.");
        return;
    }

    if (state.currentFileCount === 0) {
        setStatus("Upload a file first.", "warning");
        addChatMessage("jarvis", "Please upload at least one file before asking questions.");
        addActivity("Chat blocked: no files in knowledge base.");
        return;
    }

    addChatMessage("user", question);
    addActivity("User question sent.");

    els.chatInput.value = "";
    autoResizeTextarea();

    setTyping(true);
    setSendLoading(true);
    setStatus("Jarvis is streaming a knowledge base answer...", "warning");

    let bubble = null;
    let messageStack = null;
    let accumulatedAnswer = "";
    let finished = false;

    try {
        const response = await fetch("/chat-stream", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                question,
                language: els.languageSelect ? els.languageSelect.value : "en-US",
                kb_id: state.currentKbId
            })
        });

        if (!response.ok || !response.body) {
            const fallbackData = await parseJsonSafe(response);
            throw new Error(fallbackData.error || `Streaming response unavailable. Status: ${response.status}`);
        }

        const created = createChatMessageElement("jarvis", "Thinking...");
        bubble = created.bubble;
        messageStack = created.messageStack;

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let buffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            let boundaryIndex;
            while ((boundaryIndex = buffer.indexOf("\n\n")) !== -1) {
                const rawEvent = buffer.slice(0, boundaryIndex).trim();
                buffer = buffer.slice(boundaryIndex + 2);

                if (!rawEvent.startsWith("data:")) continue;

                const dataLines = rawEvent
                    .split("\n")
                    .filter((line) => line.startsWith("data:"))
                    .map((line) => line.slice(5).trim());

                const payload = parseJsonSafeText(dataLines.join("\n"));
                if (!payload) continue;

                if (payload.type === "start") {
                    continue;
                }

                if (payload.type === "token") {
                    accumulatedAnswer += payload.token || "";
                    if (bubble) {
                        bubble.innerHTML = formatAnswerText(accumulatedAnswer || "Thinking...");
                        scrollChatToBottom();
                    }
                    continue;
                }

                if (payload.type === "done") {
                    finished = true;
                    setTyping(false);
                    setSendLoading(false);

                    const finalAnswer = payload.answer || accumulatedAnswer || "No response.";
                    if (bubble) {
                        bubble.innerHTML = formatAnswerText(finalAnswer);
                    }

                    if (payload.audio_url) {
                        attachAudioToMessage(messageStack, payload.audio_url);
                    }

                    if (payload.sources?.length) {
                        renderInlineSources(messageStack, payload.sources);
                    }

                    if (payload.audio_error) {
                        setStatus("Reply ready, but audio could not be generated.", "warning");
                        addActivity("Reply generated. Audio unavailable.");
                    } else {
                        setStatus(
                            els.languageSelect?.value === "hi-IN" ? "उत्तर तैयार है।" : "Reply ready.",
                            "success"
                        );
                        addActivity("Reply streamed successfully.");
                    }

                    if (typeof payload.used_relevant_chunks !== "undefined") {
                        addActivity(`Retrieval used ${payload.used_relevant_chunks} chunk(s).`);
                    }

                    await loadSessionInfo();
                    continue;
                }

                if (payload.type === "error") {
                    finished = true;
                    setTyping(false);
                    setSendLoading(false);

                    if (bubble) {
                        bubble.textContent = `Error: ${payload.message}`;
                    } else {
                        addChatMessage("jarvis", `Error: ${payload.message}`);
                    }

                    setStatus("Chat failed.", "error");
                    addActivity("Chat request failed.");
                }
            }
        }

        if (!finished) {
            throw new Error("Stream ended without a final completion event.");
        }
    } catch (error) {
        console.error(error);

        if (bubble?.parentElement?.parentElement) {
            bubble.parentElement.parentElement.remove();
        }

        try {
            await fallbackChat(question);
        } catch (fallbackError) {
            console.error(fallbackError);
            addChatMessage(
                "jarvis",
                `⚠️ Something went wrong while chatting. ${fallbackError.message || error.message || ""}`.trim()
            );
            setStatus(fallbackError.message || error.message || "Something went wrong while chatting.", "error");
            addActivity("Unexpected chat error.");
        } finally {
            setTyping(false);
            setSendLoading(false);
        }
        return;
    }

    setTyping(false);
    setSendLoading(false);
}

/* -----------------------------
   Reset Actions
----------------------------- */
async function resetChatOnly() {
    if (state.isUploading || state.isChatting) return;

    const confirmed = window.confirm("Reset chat history?");
    if (!confirmed) return;

    try {
        const { response, data } = await apiFetch("/reset-chat", { method: "POST" });

        if (!response.ok) {
            throw new Error(data.message || `Reset failed with status ${response.status}`);
        }

        clearChatBox();
        addWelcomeMessage(
            state.currentFileCount > 0
                ? "Chat history cleared. Your knowledge base is still loaded."
                : "Chat history cleared. Upload files and ask me questions."
        );

        if (els.chatInput) {
            els.chatInput.value = "";
            autoResizeTextarea();
        }

        resetAudioUI();
        await loadSessionInfo();

        setStatus(data.message || "Chat history reset.", "success");
        addActivity("Chat history reset.");
    } catch (error) {
        console.error(error);
        setStatus(error.message || "Failed to reset chat.", "error");
        addActivity("Chat reset failed.");
        addChatMessage("jarvis", `⚠️ ${error.message || "Failed to reset chat."}`);
    }
}

async function resetKnowledgeBase() {
    if (state.isUploading || state.isChatting) return;

    const confirmed = window.confirm("Reset the entire knowledge base?");
    if (!confirmed) return;

    try {
        const { response, data } = await apiFetch("/reset-kb", { method: "POST" });

        if (!response.ok) {
            throw new Error(data.message || `Reset KB failed with status ${response.status}`);
        }

        state.lastUploadedFile = data.last_uploaded_file || "";
        state.lastFileSummary = data.last_file_summary || "";

        clearChatBox();
        addWelcomeMessage();
        resetAudioUI();

        if (els.chatInput) {
            els.chatInput.value = "";
            autoResizeTextarea();
        }

        if (els.fileInput) els.fileInput.value = "";
        if (els.fileNameText) els.fileNameText.textContent = "No file selected";

        updateSessionUI(data);

        setExplanation(DEFAULT_EXPLANATION);
        setLastAddedFile("");

        setStatus(data.message || "Knowledge base reset.", "success");
        addActivity("Knowledge base reset.");
    } catch (error) {
        console.error(error);
        setStatus(error.message || "Failed to reset knowledge base.", "error");
        addActivity("Knowledge base reset failed.");
        addChatMessage("jarvis", `⚠️ ${error.message || "Failed to reset knowledge base."}`);
    }
}

/* -----------------------------
   Speech Recognition
----------------------------- */
function initSpeechRecognition() {
    if (!SpeechRecognition) return;

    state.recognition = new SpeechRecognition();
    state.recognition.continuous = false;
    state.recognition.interimResults = true;
    state.recognition.maxAlternatives = 1;
    state.recognition.lang = els.languageSelect ? els.languageSelect.value : "en-US";

    state.recognition.onstart = () => {
        state.isListening = true;
        state.finalTranscript = "";

        if (els.micBtn) {
            els.micBtn.classList.add("listening");
            els.micBtn.textContent = "■";
            els.micBtn.title = "Stop listening";
        }

        setStatus(
            state.recognition.lang === "hi-IN" ? "सुन रहा हूँ... बोलिए" : "Listening... Speak now",
            "warning"
        );
        addActivity("Voice capture started.");
    };

    state.recognition.onresult = (event) => {
        let interimTranscript = "";

        for (let i = event.resultIndex; i < event.results.length; i++) {
            const piece = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                state.finalTranscript += `${piece} `;
            } else {
                interimTranscript += piece;
            }
        }

        if (els.chatInput) {
            els.chatInput.value = `${state.finalTranscript}${interimTranscript}`.trim();
            autoResizeTextarea();
        }
    };

    state.recognition.onerror = (event) => {
        state.isListening = false;

        if (els.micBtn) {
            els.micBtn.classList.remove("listening");
            els.micBtn.textContent = "🎤";
            els.micBtn.title = "Speak";
        }

        if (event.error === "not-allowed") {
            setStatus("Microphone permission was denied.", "error");
        } else if (event.error === "no-speech") {
            setStatus(
                els.languageSelect?.value === "hi-IN"
                    ? "कोई आवाज़ नहीं मिली। फिर से कोशिश करें।"
                    : "No speech detected. Please try again.",
                "warning"
            );
        } else if (event.error === "audio-capture") {
            setStatus("No microphone found.", "error");
        } else {
            setStatus(`Speech recognition error: ${event.error}`, "error");
        }

        addActivity(`Voice recognition error: ${event.error}`);
    };

    state.recognition.onend = async () => {
        state.isListening = false;

        if (els.micBtn) {
            els.micBtn.classList.remove("listening");
            els.micBtn.textContent = "🎤";
            els.micBtn.title = "Speak";
        }

        const spokenText = els.chatInput ? els.chatInput.value.trim() : "";

        if (spokenText) {
            setStatus(
                els.languageSelect?.value === "hi-IN"
                    ? "आवाज़ समझ ली गई। भेज रहा हूँ..."
                    : "Voice captured. Sending...",
                "warning"
            );
            addActivity("Voice input captured.");
            await sendMessage();
        } else {
            setStatus(
                els.languageSelect?.value === "hi-IN"
                    ? "वॉइस इनपुट तैयार है।"
                    : "Voice input ready.",
                "success"
            );
        }
    };
}

/* -----------------------------
   Event Bindings
----------------------------- */
function bindUploadZone() {
    if (!els.uploadZone) return;

    els.uploadZone.addEventListener("dragover", (event) => {
        event.preventDefault();
        els.uploadZone.style.borderColor = "rgba(94, 231, 255, 0.7)";
        els.uploadZone.style.boxShadow = "0 18px 40px rgba(56, 189, 248, 0.18)";
    });

    els.uploadZone.addEventListener("dragleave", () => {
        els.uploadZone.style.borderColor = "";
        els.uploadZone.style.boxShadow = "";
    });

    els.uploadZone.addEventListener("drop", (event) => {
        event.preventDefault();
        els.uploadZone.style.borderColor = "";
        els.uploadZone.style.boxShadow = "";

        const droppedFiles = event.dataTransfer?.files;
        if (!droppedFiles?.length || state.isUploading || state.isChatting) return;

        if (els.fileInput) {
            els.fileInput.files = droppedFiles;
        }

        bindFileSelection(droppedFiles[0]);
    });
}

function bindFileInput() {
    if (!els.fileInput) return;

    els.fileInput.addEventListener("change", () => {
        bindFileSelection(els.fileInput.files?.[0] || null);
    });
}

function bindUploadButton() {
    if (!els.uploadBtn) return;
    els.uploadBtn.addEventListener("click", uploadSelectedFile);
}

function bindCopyButton() {
    if (!els.copyBtn) return;

    els.copyBtn.addEventListener("click", async () => {
        const text = els.explanationBox?.textContent.trim() || "";

        if (!text || text.includes("Upload a file")) {
            setStatus("No explanation to copy yet.", "warning");
            return;
        }

        try {
            await navigator.clipboard.writeText(text);
            setStatus("Explanation copied.", "success");
            addActivity("Explanation copied to clipboard.");
        } catch (error) {
            console.error(error);
            setStatus("Copy failed.", "error");
        }
    });
}

function bindChatInput() {
    if (!els.chatInput) return;

    els.chatInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });

    els.chatInput.addEventListener("input", autoResizeTextarea);
}

function bindSendButton() {
    if (!els.sendBtn) return;
    els.sendBtn.addEventListener("click", sendMessage);
}

function bindMicButton() {
    if (!els.micBtn) return;

    els.micBtn.addEventListener("click", () => {
        if (state.isChatting || state.isUploading) return;

        if (!state.recognition) {
            setStatus("Voice input is not supported in this browser. Try Chrome.", "warning");
            return;
        }

        if (!state.currentKbId) {
            setStatus("Session not ready yet. Please refresh the page.", "warning");
            addActivity("Voice chat blocked: missing kb_id.");
            return;
        }

        if (state.currentFileCount === 0) {
            setStatus("Upload a file first before using voice chat.", "warning");
            addActivity("Voice chat blocked: no files in knowledge base.");
            return;
        }

        if (state.isListening) {
            state.recognition.stop();
            return;
        }

        state.recognition.lang = els.languageSelect ? els.languageSelect.value : "en-US";
        if (els.chatInput) {
            els.chatInput.value = "";
            autoResizeTextarea();
        }
        state.recognition.start();
    });
}

function bindLanguageSelect() {
    if (!els.languageSelect) return;

    els.languageSelect.addEventListener("change", () => {
        if (state.recognition) {
            state.recognition.lang = els.languageSelect.value;
        }
        addActivity(`Voice language set to ${els.languageSelect.value}.`);
    });
}

function bindResetButtons() {
    if (els.resetChatBtn) {
        els.resetChatBtn.addEventListener("click", resetChatOnly);
    }

    if (els.resetKbBtn) {
        els.resetKbBtn.addEventListener("click", resetKnowledgeBase);
    }
}

/* -----------------------------
   Init
----------------------------- */
window.addEventListener("load", async () => {
    initSpeechRecognition();
    bindUploadZone();
    bindFileInput();
    bindUploadButton();
    bindCopyButton();
    bindChatInput();
    bindSendButton();
    bindMicButton();
    bindLanguageSelect();
    bindResetButtons();

    const session = await loadSessionInfo();
    autoResizeTextarea();

    if (!state.currentKbId) {
        setStatus("Session not ready. Refresh if this continues.", "warning");
        addActivity("System loaded, but kb_id is missing.");
        clearChatBox();
        addWelcomeMessage();
        return;
    }

    if (session?.last_file_summary) {
        setExplanation(session.last_file_summary);
    } else {
        setExplanation(DEFAULT_EXPLANATION);
    }

    if (session?.last_uploaded_file) {
        setLastAddedFile(session.last_uploaded_file);
    } else {
        setLastAddedFile("");
    }

    await loadChatHistory();

    if (state.currentFileCount === 0) {
        setStatus("Ready. You can upload files now.", "success");
        addActivity("System ready. Waiting for files.");
    } else {
        setStatus("Knowledge base loaded.", "success");
        addActivity("Previous knowledge base restored.");
    }
});