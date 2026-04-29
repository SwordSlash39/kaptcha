import sys
import json
import base64
import uuid
import ctypes
import datetime
import concurrent.futures
from pathlib import Path
from litellm import completion

import webbrowser
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTreeView, QTextEdit, QPushButton, 
                             QSplitter, QLabel, QFileDialog, QDialog, 
                             QFormLayout, QDoubleSpinBox, QSpinBox,
                             QTabWidget, QListWidget, QListWidgetItem,
                             QMenu, QInputDialog, QMessageBox)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QObject, pyqtSlot, QUrl
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor, QFileSystemModel
from PyQt6.QtWebChannel import QWebChannel

# for tools
from tool_handler import execute_tool

import os
os.environ["OPENROUTER_API_KEY"] = "..."

MODEL = "openai/moonshotai/kimi-k2.6:nitro" 
TOKEN_LIMIT = 50_000

with open("tools.json", "r") as f:
    tools = json.load(f)

# Modern Pathlib directory creation
Path("vault").mkdir(exist_ok=True)
Path("chats").mkdir(exist_ok=True)

# --- HTML TEMPLATES FOR RENDERING ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script>
        window.MathJax = {
          tex: { inlineMath: [['$', '$'],['\\\\(', '\\\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']], processEscapes: true }
        };
    </script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
    <style>
        :root { --bg: #09090b; --panel: #18181b; --text: #e4e4e7; --user-bg: #3b82f6; --tool: #1e1b4b; --tool-border: #6366f1; --border: #27272a; }
        body { background-color: var(--bg); color: var(--text); font-family: 'Segoe UI', -apple-system, sans-serif; padding: 20px; font-size: 14.5px; margin: 0; display: flex; flex-direction: column; gap: 20px; }
        #container { display: flex; flex-direction: column; gap: 16px; padding-bottom: 20px; }
        
        .message-wrapper { display: flex; flex-direction: column; width: 100%; }
        .message { max-width: 80%; padding: 12px 18px; border-radius: 12px; line-height: 1.6; word-wrap: break-word; box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        
        .user-wrapper { align-items: flex-end; }
        .user-wrapper .message { background-color: var(--user-bg); color: #ffffff; border-bottom-right-radius: 4px; }
        
        .assistant-wrapper { align-items: flex-start; }
        .assistant-wrapper .message { background-color: var(--panel); border: 1px solid var(--border); border-bottom-left-radius: 4px; }
        
        .tool-wrapper { align-items: center; }
        .tool-wrapper .message { max-width: 95%; background-color: var(--tool); border: 1px dashed var(--tool-border); font-family: 'Consolas', monospace; font-size: 13px; color: #a5b4fc; padding: 10px; }
        .tool-group-item { background: rgba(0,0,0,0.25); border: 1px solid var(--tool-border); padding: 10px; border-radius: 6px; margin-top: 8px; }
        .tool-group-item pre { background-color: rgba(0,0,0,0.4); border: none; padding: 8px; margin-top: 5px; }
        details > summary { cursor: pointer; outline: none; font-weight: bold; color: #818cf8; font-size: 14px; user-select: none; }
        
        .role-label { font-size: 11px; font-weight: 800; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.8; }
        .user-wrapper .role-label { color: #dbeafe; }
        .assistant-wrapper .role-label { color: #86efac; }
        
        .delete-btn { font-size: 11px; cursor: pointer; color: #fca5a5; text-decoration: none; font-weight: normal; margin-left: 15px; }
        .delete-btn:hover { color: #f87171; text-decoration: underline; }

        pre { background-color: #000000; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid var(--border); }
        code { font-family: 'Consolas', monospace; color: #38bdf8; }
        a { color: inherit; text-decoration: underline; font-weight: bold; }
        img.chat-img { max-width: 300px; max-height: 300px; border-radius: 8px; margin-top: 8px; display: block; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }
        
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #27272a; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #3f3f46; }
    </style>
</head>
<body>
    <div id="container"></div>
    <script>
        // Modern Marked.js Extension for MathJax Hook
        const mathExtension = {
          name: 'math',
          level: 'inline',
          start(src) { return src.match(/\\$|\\\\\\[|\\\\\\(/)?.index; },
          tokenizer(src, tokens) {
            const blockRule = /^(\\$\\$|\\\\\\[)([\\s\\S]+?)(\\$\\$|\\\\\\])/;
            const inlineRule = /^(\\$|\\\\\\()([\\s\\S]+?)(\\$|\\\\\\))/;
            let match = blockRule.exec(src);
            if (match) return { type: 'math', raw: match[0], text: match[2], displayMode: true };
            match = inlineRule.exec(src);
            if (match) return { type: 'math', raw: match[0], text: match[2], displayMode: false };
          },
          renderer(token) {
            return token.displayMode ? `\\\\[${token.text}\\\\]` : `\\\\(${token.text}\\\\)`;
          }
        };
        marked.use({ extensions:[mathExtension], breaks: true, gfm: true });

        // QWebChannel Setup for Python communication
        let backend = null;
        if (typeof QWebChannel !== "undefined") {
            new QWebChannel(qt.webChannelTransport, function (channel) {
                backend = channel.objects.backend;
            });
        }

        function requestDelete(id) {
            if (backend) backend.requestDelete(id);
            removeMessageDOM(id);
        }

        let toolGroupContainer = null;

        function renderMessages(msgs) {
            const container = document.getElementById('container');
            container.innerHTML = "";
            toolGroupContainer = null;
            msgs.forEach(msg => appendMessage(msg, false));
            window.scrollTo({top: document.body.scrollHeight, behavior: 'auto'});
            if (window.MathJax) MathJax.typesetPromise();
        }

        function appendMessage(msg, doTypeset = true) {
            const container = document.getElementById('container');
            
            if (msg.role === 'tool') {
                if (!toolGroupContainer) {
                    let wrapper = document.createElement('div');
                    wrapper.className = 'message-wrapper tool-wrapper';
                    wrapper.innerHTML = `<div class="message" style="width: 100%;">
                        <details open><summary>🛠️ Tools Executed</summary><div class="tool-list"></div></details>
                    </div>`;
                    container.appendChild(wrapper);
                    toolGroupContainer = wrapper.querySelector('.tool-list');
                }
                
                const parsed = marked.parse(msg.content);
                let delBtn = `<a href="javascript:void(0)" onclick="requestDelete('${msg.__id__}')" class="delete-btn" style="float: right;">🗑️ Delete</a>`;
                
                let item = document.createElement('div');
                item.className = 'tool-group-item';
                item.id = 'msg-' + msg.__id__;
                item.innerHTML = `<div style="margin-bottom: 5px;">${delBtn} <span style="color:#a5b4fc; font-weight:bold;">Output:</span></div>${parsed}`;
                toolGroupContainer.appendChild(item);
                
                let summary = toolGroupContainer.parentElement.querySelector('summary');
                summary.innerHTML = `🛠️ ${toolGroupContainer.children.length} Tool(s) Executed`;
                
            } else {
                toolGroupContainer = null; // Reset for next messages
                
                const htmlContent = marked.parse(msg.content);
                let roleDisplay = msg.role === 'user' ? 'YOU' : msg.role.toUpperCase();
                let delHtml = `<a href="javascript:void(0)" onclick="requestDelete('${msg.__id__}')" class="delete-btn" style="float: right;">🗑️ Delete</a>`;
                
                let wrapper = document.createElement('div');
                wrapper.className = `message-wrapper ${msg.role}-wrapper`;
                wrapper.id = 'msg-' + msg.__id__;
                wrapper.innerHTML = `<div class="message">
                                       <div class="role-label">${roleDisplay} ${delHtml}</div>
                                       <div>${htmlContent}</div>
                                     </div>`;
                container.appendChild(wrapper);
            }
            
            if (doTypeset) {
                window.scrollTo({top: document.body.scrollHeight, behavior: 'auto'});
                if (window.MathJax) MathJax.typesetPromise();
            }
        }

        function removeMessageDOM(msg_id) {
            let el = document.getElementById('msg-' + msg_id);
            if (el) {
                let parent = el.parentElement;
                el.remove();
                if (parent.className === 'tool-list' && parent.children.length === 0) {
                    parent.parentElement.parentElement.parentElement.remove();
                    toolGroupContainer = null;
                } else if (parent.className === 'tool-list') {
                    let summary = parent.parentElement.querySelector('summary');
                    summary.innerHTML = `🛠️ ${parent.children.length} Tool(s) Executed`;
                }
            }
        }
    </script>
</body>
</html>
"""

HTML_VIEWER_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script>
        window.MathJax = { tex: { inlineMath: [['$', '$'],['\\\\(', '\\\\)']], displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']], processEscapes: true } };
    </script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/marked@12.0.0/marked.min.js"></script>
    <style>
        body { background-color: #09090b; color: #e4e4e7; font-family: 'Segoe UI', sans-serif; padding: 20px; line-height: 1.6; }
        pre { background-color: #000000; padding: 12px; border-radius: 8px; overflow-x: auto; border: 1px solid #27272a; }
        code { font-family: 'Consolas', monospace; color: #38bdf8; }
        a { color: #38bdf8; }
    </style>
</head>
<body>
    <div id="content"></div>
    <script>
        const mathExtension = {
          name: 'math',
          level: 'inline',
          start(src) { return src.match(/\\$|\\\\\\[|\\\\\\(/)?.index; },
          tokenizer(src, tokens) {
            const blockRule = /^(\\$\\$|\\\\\\[)([\\s\\S]+?)(\\$\\$|\\\\\\])/;
            const inlineRule = /^(\\$|\\\\\\()([\\s\\S]+?)(\\$|\\\\\\))/;
            let match = blockRule.exec(src);
            if (match) return { type: 'math', raw: match[0], text: match[2], displayMode: true };
            match = inlineRule.exec(src);
            if (match) return { type: 'math', raw: match[0], text: match[2], displayMode: false };
          },
          renderer(token) {
            return token.displayMode ? `\\\\[${token.text}\\\\]` : `\\\\(${token.text}\\\\)`;
          }
        };
        marked.use({ extensions: [mathExtension], breaks: true, gfm: true });
        
        function setContent(content) {
            document.getElementById('content').innerHTML = marked.parse(content);
            if (window.MathJax) MathJax.typesetPromise();
        }
    </script>
</body>
</html>
"""

# --- QWEBCHANNEL BACKEND ---
class Backend(QObject):
    delete_requested = pyqtSignal(str)
    
    @pyqtSlot(str)
    def requestDelete(self, msg_id):
        self.delete_requested.emit(msg_id)

# --- UTILITY ---
def create_app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#3b82f6"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("#09090b"))
    painter.drawEllipse(22, 22, 20, 20)
    painter.end()
    return QIcon(pixmap)

def set_dark_titlebar(window):
    if os.name == 'nt':
        try:
            hwnd = int(window.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception: pass

# --- NON-BLOCKING FILE READER ---
class FileReaderThread(QThread):
    content_loaded = pyqtSignal(str)
    
    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f: 
                content = f.read()
            self.content_loaded.emit(content)
        except Exception: pass

# 1. Intercept clicks and open them in a new window
class MarkdownPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, nav_type, isMainFrame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.host() == "vault":
                # Convert the dummy URL back to your local path
                rel_path = url.path().lstrip('/')
                local_file = Path("vault") / rel_path
                
                # Open a new window!
                if local_file.exists() and local_file.suffix == '.md':
                    self.viewer = MarkdownViewerWindow(str(local_file))
                    self.viewer.show()
            else:
                # Open regular internet links in Chrome/Edge/Safari
                webbrowser.open(url.toString())
            return False
        return super().acceptNavigationRequest(url, nav_type, isMainFrame)

# 2. Update Viewer to use the page & dummy base URL
class MarkdownViewerWindow(QDialog):
    def __init__(self, filepath, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Vault Viewer - {Path(filepath).name}")
        self.resize(900, 700)
        self.setStyleSheet("background-color: #09090b; color: #e4e4e7;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.filepath = filepath
        self.content = None
        self.page_loaded = False

        self.webview = QWebEngineView()
        self.webview.setPage(MarkdownPage(self)) # <-- Apply our interceptor
        self.webview.loadFinished.connect(self.on_page_loaded)
        layout.addWidget(self.webview)
        
        # <-- THE TRICK: A dummy http base URL allows CDNs and structures relative links cleanly
        self.webview.setHtml(HTML_VIEWER_TEMPLATE, QUrl("http://vault/"))
        
        self.reader = FileReaderThread(self.filepath)
        self.reader.content_loaded.connect(self.on_content_loaded)
        self.reader.start()

    # (Keep your existing on_page_loaded, on_content_loaded, and try_render methods exactly as they were)
    def on_page_loaded(self, ok):
        if ok:
            self.page_loaded = True
            self.try_render()

    def on_content_loaded(self, content):
        self.content = content
        self.try_render()

    def try_render(self):
        if self.page_loaded and self.content is not None:
            self.webview.page().runJavaScript(f"setContent({json.dumps(self.content)});")

class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LLM Settings")
        self.resize(350, 200)
        self.setStyleSheet("""
            QDialog { background-color: #18181b; color: #e4e4e7; font-family: 'Segoe UI'; }
            QLabel { color: #a1a1aa; font-weight: bold; }
            QSpinBox, QDoubleSpinBox { background-color: #09090b; color: #e4e4e7; border: 1px solid #27272a; border-radius: 5px; padding: 5px; }
            QPushButton { background-color: #3b82f6; color: #ffffff; border-radius: 5px; padding: 8px; font-weight: bold; border: none;}
            QPushButton:hover { background-color: #60a5fa; }
        """)
        self.settings = current_settings
        layout = QFormLayout(self)
        
        self.temp_input = QDoubleSpinBox()
        self.temp_input.setRange(0.0, 2.0)
        self.temp_input.setSingleStep(0.1)
        self.temp_input.setValue(self.settings.get("temperature", 0.7))
        layout.addRow("Temperature:", self.temp_input)
        
        self.tokens_input = QSpinBox()
        self.tokens_input.setRange(100, 128000)
        self.tokens_input.setSingleStep(500)
        self.tokens_input.setValue(self.settings.get("max_tokens", 4000))
        layout.addRow("Max Tokens:", self.tokens_input)
        
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.clicked.connect(self.save_and_close)
        layout.addRow(self.save_btn)
        
    def save_and_close(self):
        self.settings["temperature"] = self.temp_input.value()
        self.settings["max_tokens"] = self.tokens_input.value()
        with open("settings.json", "w") as f:
            json.dump(self.settings, f)
        self.accept()

# --- AGENT WORKER THREAD ---
class AgentWorker(QThread):
    new_message = pyqtSignal(str, str, str) # role, content, msg_id
    status_update = pyqtSignal(str)
    token_update = pyqtSignal(int) # <-- NEW Signal
    
    def __init__(self, messages, settings):
        super().__init__()
        self.messages = messages
        self.settings = settings

        self._is_running = True

    def stop(self):              
        self._is_running = False

    def run(self):
        self.status_update.emit("Thinking...")
        try:
            # SANITIZATION: Clean up orphaned tools
            assistant_tool_call_ids = set()
            for m in self.messages:
                if m.get("role") == "assistant" and "tool_calls" in m:
                    for tc in m["tool_calls"]:
                        assistant_tool_call_ids.add(tc["id"])

            valid_tool_responses = set()
            for m in self.messages:
                if m.get("role") == "tool" and m.get("tool_call_id") in assistant_tool_call_ids:
                    valid_tool_responses.add(m["tool_call_id"])

            api_messages =[]
            for m in self.messages:
                api_msg = {k: v for k, v in m.items() if k != "__id__"}
                
                if api_msg.get("role") == "tool" and api_msg.get("tool_call_id") not in assistant_tool_call_ids:
                    continue 

                if api_msg.get("role") == "assistant" and "tool_calls" in api_msg:
                    valid_calls = [tc for tc in api_msg["tool_calls"] if tc["id"] in valid_tool_responses]
                    if valid_calls:
                        api_msg["tool_calls"] = valid_calls
                    else:
                        del api_msg["tool_calls"]
                        if not api_msg.get("content"): continue
                            
                api_messages.append(api_msg)

            # Execution
            while self._is_running:
                # --- NEW: PRUNE OLD TOOL IMAGES TO SAVE TOKENS ---
                tool_image_indices =[]
                # Find all tool messages that contain image payloads
                for i, m in enumerate(api_messages):
                    if m.get("role") == "tool" and isinstance(m.get("content"), list):
                        has_img = any(isinstance(part, dict) and part.get("type") == "image_url" for part in m["content"])
                        if has_img:
                            tool_image_indices.append(i)
                
                # Strip all images EXCEPT the last 2 tool calls
                if len(tool_image_indices) > 2:
                    for i in tool_image_indices[:-2]:
                        clean_content =[]
                        for part in api_messages[i]["content"]:
                            if isinstance(part, dict) and part.get("type") == "image_url":
                                continue  # Drop the image safely
                            clean_content.append(part)
                        
                        clean_content.append({"type": "text", "text": "[Older screenshots removed to save context memory]"})
                        api_messages[i]["content"] = clean_content
                        
                        # Sync this change back to self.messages so the .json save file doesn't become huge
                        t_id = api_messages[i].get("tool_call_id")
                        if t_id:
                            for sm in self.messages:
                                if sm.get("tool_call_id") == t_id:
                                    sm["content"] = clean_content
                                    break
                # -------------------------------------------------

                response = completion(
                    model=MODEL,
                    api_base="https://openrouter.ai/api/v1",
                    api_key=os.environ["OPENROUTER_API_KEY"],
                    messages=api_messages,
                    tools=tools,
                    temperature=self.settings["temperature"],
                    max_tokens=self.settings["max_tokens"]
                )

                if not self._is_running: 
                    break
                
                # --- NEW: Token Extraction ---
                total_tokens = 0
                if hasattr(response, 'usage') and response.usage:
                    total_tokens = response.usage.total_tokens
                    self.token_update.emit(total_tokens)
                
                msg = response.choices[0].message
                finish_reason = getattr(response.choices[0], "finish_reason", "stop")
                assistant_id = str(uuid.uuid4())
                assistant_msg = {"role": "assistant", "__id__": assistant_id}
                
                if msg.content:
                    assistant_msg["content"] = msg.content
                    self.new_message.emit("assistant", msg.content, assistant_id)
                
                if msg.tool_calls:
                    assistant_msg["tool_calls"] =[{"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in msg.tool_calls]
                    self.messages.append(assistant_msg)
                    
                    api_msg_to_add = {k: v for k, v in assistant_msg.items() if k != "__id__"}
                    api_messages.append(api_msg_to_add)
                    
                    self.status_update.emit(f"Executing {len(msg.tool_calls)} tool(s)...")

                    # PARALLEL TOOL EXECUTION
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future_to_tc = {executor.submit(execute_tool, tc.function.name, tc.function.arguments): tc for tc in msg.tool_calls}
                        
                        results_dict = {}
                        for future in concurrent.futures.as_completed(future_to_tc):
                            tc = future_to_tc[future]
                            try:
                                output = future.result()
                            except Exception as e:
                                output = f"Error: {str(e)}"
                            results_dict[tc.id] = output

                    # Process results in strict original order
                    for tc in msg.tool_calls:
                        t_name = tc.function.name
                        output = results_dict[tc.id]
                        t_id = str(uuid.uuid4())
                        
                        try:
                            parsed_out = json.loads(output)
                            if isinstance(parsed_out, dict) and "__kaptcha_multimodal__" in parsed_out:
                                text_part = parsed_out["text"]
                                api_content =[{"type": "text", "text": text_part}]
                                
                                if "images_b64" in parsed_out:
                                    for b64 in parsed_out["images_b64"]:
                                        api_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                                elif "image_b64" in parsed_out:
                                    api_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{parsed_out['image_b64']}"}})

                                t_msg = { "role": "tool", "tool_call_id": tc.id, "name": t_name, "content": api_content, "__id__": t_id }
                                display_out = text_part[:250] + "\n\n...[OUTPUT TRUNCATED]" if len(text_part) > 250 else text_part
                            else:
                                raise ValueError("Standard text JSON")
                        except Exception:
                            t_msg = { "role": "tool", "tool_call_id": tc.id, "name": t_name, "content": output, "__id__": t_id }
                            display_out = output[:250] + "\n\n...[OUTPUT TRUNCATED]" if len(output) > 250 else output

                        self.messages.append(t_msg)
                        
                        api_t_msg = {k: v for k, v in t_msg.items() if k != "__id__"}
                        api_messages.append(api_t_msg)
                        
                        raw_tool_out = f"**Tool Name:** `{t_name}`\n```text\n{display_out}\n```"
                        if isinstance(t_msg["content"], list):
                            raw_tool_out += "\n\n**Visual Context Captured:**<br>"
                            for item in t_msg["content"]:
                                if item.get("type") == "image_url":
                                    img_url = item["image_url"]["url"]
                                    raw_tool_out += f"<img class='chat-img' src='{img_url}' style='display:inline-block; margin:5px; max-width: 280px;'>"
                            
                        self.new_message.emit("tool", raw_tool_out, t_id)
                    
                    # --- NEW: MEMORY CONDENSATION TRIGGER ---
                    # We trigger at 110k to leave 18k tokens of breathing room to generate the summary
                    if total_tokens > TOKEN_LIMIT:
                        self.status_update.emit("Context Limit Reached: Summarizing Memory...")
                        notify_id = str(uuid.uuid4())
                        self.new_message.emit("assistant", "🔄 **Context limit approaching (>110k tokens).** Extracting memory into a detailed summary and swapping to a fresh instance...", notify_id)
                        
                        # Strip images from history before asking it to summarize to save prompt space
                        clean_history =[]
                        for m in api_messages:
                            if m.get("role") == "system": continue
                            clean_m = {"role": m["role"]}
                            if "content" in m and isinstance(m["content"], list):
                                clean_m["content"] = " ".join([p["text"] for p in m["content"] if p.get("type") == "text"])
                            elif "content" in m:
                                clean_m["content"] = m["content"]
                            if "tool_calls" in m: clean_m["tool_calls"] = m["tool_calls"]
                            if "name" in m: clean_m["name"] = m["name"]
                            clean_history.append(clean_m)
                            
                        summary_prompt = (
                            "You are an AI tasked with summarizing a highly complex agent conversation that is hitting its memory token limit.\n"
                            "Provide a detailed summary (under 3000 words) of the entire conversation. Include:\n"
                            "1. The original user request and primary goal.\n"
                            "2. All key facts, data, variables, and context discovered so far.\n"
                            "3. The exact reasoning, logic, and 'thinking' of the agent.\n"
                            "4. A clear explanation of what the agent was *just doing* (the latest tool calls and current screen state) so a new fresh instance can seamlessly take over.\n\n"
                            f"CONVERSATION HISTORY:\n{json.dumps(clean_history, indent=2)}"
                        )
                        
                        summary_res = completion(
                            model=MODEL,
                            api_base="https://openrouter.ai/api/v1",
                            api_key=os.environ["OPENROUTER_API_KEY"],
                            messages=[{"role": "user", "content": summary_prompt}],
                            max_tokens=4000
                        )
                        summary_text = summary_res.choices[0].message.content
                        
                        # Save to status.md (Root Directory, outside vault)
                        with open("status.md", "w", encoding="utf-8") as f:
                            f.write(summary_text)
                            
                        self.new_message.emit("assistant", "✅ **Memory successfully condensed and saved to `status.md`.** Fresh instance resuming task...", str(uuid.uuid4()))
                        
                        # Build New Context
                        sys_prompt = self.messages[0]
                        
                        # Isolate the latest assistant tool call block + its tool results
                        last_interaction =[]
                        for m in reversed(self.messages):
                            last_interaction.insert(0, m)
                            if m.get("role") == "assistant" and "tool_calls" in m:
                                break
                                
                        new_msgs = [sys_prompt]
                        new_msgs.append({
                            "role": "user",
                            "content": f"[SYSTEM ALERT: MEMORY CONDENSATION TRIGGERED]\nYour context was getting too large, so your previous memories were wiped and compressed. Here is a detailed summary of everything you have done, thought, and discovered so far:\n\n{summary_text}\n\nReview the latest tool result below and continue the exact task seamlessly.",
                            "__id__": str(uuid.uuid4())
                        })
                        new_msgs.extend(last_interaction)
                        
                        # Overwrite active context
                        self.messages.clear()
                        self.messages.extend(new_msgs)
                        
                        # Rebuild API context for the next loop iteration
                        api_messages =[{k: v for k, v in m.items() if k != "__id__"} for m in self.messages]
                        
                        self.token_update.emit(0)
                        self.status_update.emit("Thinking...")
                        continue # Skip to the top of the loop with the fresh new brain!
                    # -----------------------------------------------

                    self.status_update.emit("Thinking...")
                else:
                    # --- CATCH SILENT TOKEN LIMITS / COMPLETION ---
                    if not msg.content:
                        if finish_reason == "length":
                            warning = "⚠️ **Agent stopped abruptly: Memory/Token limit reached.** Please start a New Chat."
                            assistant_msg["content"] = warning
                            self.new_message.emit("assistant", warning, assistant_id)
                        else:
                            fallback = "*(Task completed)*"
                            assistant_msg["content"] = fallback
                            self.new_message.emit("assistant", fallback, assistant_id)
                            
                    self.messages.append(assistant_msg)
                    break

        except Exception as e:
            err_id = str(uuid.uuid4())
            self.new_message.emit("tool", f"Error: {str(e)}", err_id)
            
        self.status_update.emit("READY")

# --- UI COMPONENTS ---
class DropTextEdit(QTextEdit):
    files_dropped = pyqtSignal(list)
    send_triggered = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setPlaceholderText("Type a message... (Ctrl + Enter to send)")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.send_triggered.emit()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dropEvent(self, event):
        paths =[url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile().lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.mov', '.avi'))]
        if paths: self.files_dropped.emit(paths)
        event.acceptProposedAction()

class AttachmentThumbnail(QWidget):
    remove_clicked = pyqtSignal(str)
    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.setFixedSize(70, 70)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if file_path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            self.img_label.setText("Video")
            self.img_label.setStyleSheet("background-color: #27272a; border-radius: 8px; color: #a1a1aa; font-weight: bold; font-size: 11px;")
        else:
            self.img_label.setStyleSheet("background-color: #27272a; border-radius: 8px; border: 1px solid #3f3f46;")
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                self.img_label.setPixmap(pixmap.scaled(70, 70, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self.img_label)
        self.close_btn = QPushButton("✕", self)
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet("QPushButton { background-color: #ef4444; color: #ffffff; border-radius: 10px; font-weight: bold; font-size: 10px; padding: 0; border: none; } QPushButton:hover { background-color: #f87171; }")
        self.close_btn.move(50, -2) 
        self.close_btn.clicked.connect(lambda: self.remove_clicked.emit(self.file_path))

# --- MAIN GUI WINDOW ---
class KaptchaApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kaptcha")
        self.resize(1300, 850)
        set_dark_titlebar(self)
        
        self.attached_files =[]
        self.app_settings = {"temperature": 0.7, "max_tokens": 4000}
        self.load_settings()

        self.worker = None
        self.chat_loaded = False
        
        # Setup QWebChannel Backend
        self.backend = Backend()
        self.backend.delete_requested.connect(self.handle_delete)
        self.channel = QWebChannel()
        self.channel.registerObject("backend", self.backend)

        self.apply_production_theme()
        self.setup_ui()
        self.new_chat()

    def get_system_prompt(self):
        return {"role": "system", "content": """
            You are Kaptcha, a highly autonomous Desktop agent running locally.
            ### TOOL USAGE
            You are STRONGLY encouraged to use your tools. Do not rely entirely on your training data; actively read and write to verify and store facts. 
            You MUST check the current date using `current_datetime` tool BEFORE USING ANY OTHER TOOL.
            
            ### WEB AND BROWSER STRATEGY
            You have two different ways to interact with the web:
            1. **Fast Information Gathering (`scrape_url`)**: Use this for reading articles, documentation, or extracting text.
            2. **Interactive Browsing (`browser_*` tools)**: Use this for complex tasks, logins, or dynamic UIs.
            
            ### BROWSER AUTOMATION LOOP & STRICT RULES
            1. **ALWAYS GET VIEW FIRST**: Before you run ANY browser tools INCLUDING `browser_navigate`, you MUST run `browser_get_view` to see the current page state, interactive elements, and their IDs. Do not attempt to interact blindly. THIS IS A MANDATORY STEP.
            2. **MANDATORY PRIMARY INTERACTION (ID CLICKING)**: You MUST use `browser_click` on the yellow [ID] tags for all web interactions (links, buttons, inputs, drop-downs, if they point to an element you would like to click on). NEVER use the cyan grid for standard web elements. If it has an ID, use `browser_click`.
            3. **TYPING IN INPUTS**: To type, use `browser_click` on the input field's [ID] to focus it, THEN use `browser_type`.
            4. **GRID HOVERING (ABSOLUTE LAST RESORT)**: ONLY use `browser_hover_grid` if the target completely lacks an ID badge (e.g., video game canvases, interactive maps, or broken UI elements). If you are forced to use the grid:
                - STEP 1: Call `browser_hover_grid` to place your virtual cursor (e.g., 'C14').
                - STEP 2: You MUST call `browser_get_view` immediately after hovering. The system will physically block you from clicking if you try to skip this step.
                - STEP 3: Visually verify the bright red/white bullseye cursor in the returned image. If it is exactly on your target, call `browser_click_hovered`. If it is off-center, restart at Step 1 with an adjacent cell.
            5. **REFRESH VIEW**: Always call `browser_get_view` after taking an action that changes the page (e.g., navigating, clicking a link, pressing Enter) so you can see the updated screen.
                - You will get 3 pictures:
                    - a clean image of the browser (with a RED DOT to mark your cursor location)
                    - an image marking location and number of all elements on the screen
                    - a grid with markings (ON EACH SQUARE) that you can use your hover tool on
                - you MUST check if your cursor is on the right position by comparing the red dot's location against where you would like to click
            6. **GAMES/POPUPS**: If you need to navigate a game or close a popup overlay, use `browser_press_key` (e.g., 'Escape').
            7. **SCROLLING**: If you need to scroll down (e.g. on an email or on a website with a long list unable to fit on the screen), you need to FIRST select (using either `browser_click` or hovering then clicking) the part you want to scroll, THEN scroll.
            8. **WAITING FOR LOADS**: If a page is actively loading, displaying a progress bar, or you are waiting for an opponent/game to start, use the \browser_wait` tool to pause for a few seconds. Do NOT repeatedly call `browser_get_view` while something is loading, as it wastes memory.`
                  
            ### THE /VAULT/ DIRECTORY (Selective Memory)
            You maintain a permanent memory in `./vault/`. 
            - **Selective Saving**: DO NOT save everything you see. If you are just "checking" a site or looking up a quick fact, do not record it.
            - **What to Save**: Only save information that is:
                1. Explicitly requested by the user to be remembered.
                2. Critical for future tasks (e.g., a specific account ID, a recurring schedule, or a complex project requirement).
                3. High-value data that would be difficult to find again.
            - **Storage Strategy**: 
                1. **FILE FIRST**: Create/update the specific content file in a subdirectory (e.g., `./vault/projects/`). 
                2. **INDEX SECOND**: Update `./vault/vault.md` with a link and a brief 1-sentence summary.
            - **Keep it Clean**: The Vault is for high-signal data only. Keep it organized and avoid "junk" entries.
                
            ### FORMATTING RULES
            - **Mathematics**: Use LaTeX. Use `\\[` and `\\]` for blocks, and `\\(` and `\\)` for inline.
            
            ### EXCLUSIVE ACTION RULES
            - Do NOT give your final answer to the prompt until you have verified that ALL tools calls are done.
            - **No Concurrent Browser Actions**: Never run `browser_navigate`, `browser_hover_grid`, or `browser_click` at the same time as `browser_get_view`. Do them in separate sequential turns.
            - **The Loop**: Call tool -> Wait for result -> Provide final response or next tool call.
            - **Constraint**: Never say "File saved" unless the tool has actually finished successfully.
        """, "__id__": str(uuid.uuid4())}

    def load_settings(self):
        if Path("settings.json").exists():
            try:
                with open("settings.json", "r") as f: self.app_settings.update(json.load(f))
            except Exception: pass

    def apply_production_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #09090b; }
            QWidget { color: #e4e4e7; font-family: 'Segoe UI', sans-serif; font-size: 14px; }
            QSplitter::handle { background-color: #27272a; width: 1px; margin: 10px 0px; }
            
            QTabWidget::pane { border: 1px solid #27272a; border-radius: 8px; border-top-left-radius: 0px; background-color: #18181b; }
            QTabBar::tab { background: #09090b; color: #a1a1aa; padding: 10px 20px; border: 1px solid transparent; font-weight: bold; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #18181b; color: #e4e4e7; border: 1px solid #27272a; border-bottom: 1px solid #18181b; }
            
            /* Chat List Styling (Keeps the floating rounded corners) */
            QListWidget { background-color: transparent; border: none; padding: 5px; outline: 0; font-size: 14px;}
            QListWidget::item { padding: 8px; border-radius: 5px; margin-bottom: 2px; }
            QListWidget::item:hover { background-color: #27272a; }
            QListWidget::item:selected { background-color: #3b82f6; color: #ffffff; font-weight: bold; }
            
            /* Vault Tree Styling (Fixes the split gap) */
            QTreeView { background-color: transparent; border: none; padding: 5px; outline: 0; font-size: 14px; show-decoration-selected: 1; }
            QTreeView::item { padding: 6px; }
            QTreeView::item:hover, QTreeView::branch:hover { background-color: #27272a; }
            QTreeView::item:selected, QTreeView::branch:selected { background-color: #3b82f6; color: #ffffff; font-weight: bold; }
            
            QLabel#headerLabel { font-weight: 800; color: #a1a1aa; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 5px; }
            
            QScrollBar:vertical { background: #09090b; width: 10px; margin: 0px; border-radius: 5px; }
            QScrollBar::handle:vertical { background: #27272a; min-height: 20px; border-radius: 5px; }
            QScrollBar::handle:vertical:hover { background: #3f3f46; }
            
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; border: none; background: none; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
            
            QWidget#inputContainer { background-color: #18181b; border: 1px solid #27272a; border-radius: 12px; margin-top: 10px; }
            QTextEdit { background-color: transparent; border: none; color: #e4e4e7; padding: 10px; font-size: 15px; }
            
            QPushButton#sendBtn { background-color: #3b82f6; color: #ffffff; border-radius: 8px; padding: 10px; font-weight: 800; font-size: 14px; border: none; }
            QPushButton#sendBtn:hover { background-color: #60a5fa; }
            QPushButton#attachBtn, QPushButton#settingsBtn, QPushButton#newChatBtn { background-color: #27272a; color: #e4e4e7; border-radius: 8px; padding: 10px 16px; font-weight: bold; font-size: 13px; border: none; }
            QPushButton#attachBtn:hover, QPushButton#settingsBtn:hover, QPushButton#newChatBtn:hover { background-color: #3f3f46; }
        """)

    def setup_ui(self):
        main_widget = QWidget()
        layout = QHBoxLayout(main_widget)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- LEFT PANEL ---
        left_widget = QTabWidget()
        
        self.vault_tab = QWidget()
        vault_layout = QVBoxLayout(self.vault_tab)
        vault_layout.setContentsMargins(0, 5, 0, 0)
        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(str(Path.cwd() / "vault"))
        self.tree_view = QTreeView()
        self.tree_view.setModel(self.file_model)
        self.tree_view.setRootIndex(self.file_model.index(str(Path.cwd() / "vault")))
        self.tree_view.setHeaderHidden(True)
        self.tree_view.hideColumn(1); self.tree_view.hideColumn(2); self.tree_view.hideColumn(3)
        self.tree_view.doubleClicked.connect(self.on_vault_file_double_clicked)
        vault_layout.addWidget(self.tree_view)
        left_widget.addTab(self.vault_tab, "📁 Vault")
        
        self.chats_tab = QWidget()
        chats_layout = QVBoxLayout(self.chats_tab)
        chats_layout.setContentsMargins(0, 5, 0, 0)
        self.chat_list_widget = QListWidget()
        self.chat_list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list_widget.customContextMenuRequested.connect(self.show_chat_context_menu)
        self.chat_list_widget.itemClicked.connect(self.on_chat_selected)
        chats_layout.addWidget(self.chat_list_widget)
        left_widget.addTab(self.chats_tab, "💬 Chats")

        # --- RIGHT PANEL ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        
        top_bar = QHBoxLayout()
        self.status_label = QLabel("READY")
        self.status_label.setObjectName("headerLabel")
        top_bar.addWidget(self.status_label)
        
        # --- NEW: Token Counter UI ---
        self.token_label = QLabel("Tokens: 0")
        self.token_label.setObjectName("headerLabel")
        self.token_label.setStyleSheet("color: #8b5cf6;") # Purple text
        top_bar.addWidget(self.token_label)
        # -----------------------------
        
        top_bar.addStretch()
        
        self.new_chat_btn = QPushButton("➕ New Chat")
        self.new_chat_btn.setObjectName("newChatBtn")
        self.new_chat_btn.clicked.connect(self.new_chat)
        top_bar.addWidget(self.new_chat_btn)
        
        self.settings_btn = QPushButton("⚙️ Settings")
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.clicked.connect(self.open_settings)
        top_bar.addWidget(self.settings_btn)
        right_layout.addLayout(top_bar)
        
        self.chat_view = QWebEngineView()
        self.chat_view.page().setWebChannel(self.channel)
        self.chat_view.loadFinished.connect(self.on_chat_load_finished)
        self.chat_view.setHtml(HTML_TEMPLATE, QUrl("qrc:/"))
        right_layout.addWidget(self.chat_view, stretch=1)

        self.input_container = QWidget()
        self.input_container.setObjectName("inputContainer")
        input_layout = QVBoxLayout(self.input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(5)

        self.attachment_widget = QWidget()
        self.attachment_layout = QHBoxLayout(self.attachment_widget)
        self.attachment_layout.setContentsMargins(5, 0, 5, 0)
        self.attachment_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.attachment_widget.hide()
        input_layout.addWidget(self.attachment_widget)

        self.input_field = DropTextEdit()
        self.input_field.setMaximumHeight(90)
        self.input_field.files_dropped.connect(self.add_attachments)
        self.input_field.send_triggered.connect(self.send_user_message)
        input_layout.addWidget(self.input_field)

        bottom_tools = QHBoxLayout()
        bottom_tools.setContentsMargins(5, 0, 5, 5)
        self.attach_btn = QPushButton("📎 Attach")
        self.attach_btn.setObjectName("attachBtn")
        self.attach_btn.clicked.connect(self.open_file_dialog)
        bottom_tools.addWidget(self.attach_btn)
        bottom_tools.addStretch()
        self.send_btn = QPushButton("Send ✈️")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setFixedSize(100, 40)
        self.send_btn.clicked.connect(self.handle_send_btn)
        bottom_tools.addWidget(self.send_btn)
        
        input_layout.addLayout(bottom_tools)
        right_layout.addWidget(self.input_container)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([300, 1000])
        layout.addWidget(splitter)
        self.setCentralWidget(main_widget)

    def handle_send_btn(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.send_btn.setText("Stopping...")
            self.send_btn.setEnabled(False)
        else:
            self.send_user_message()

    # --- CHAT LOGIC ---
    def new_chat(self):
        self.current_chat_id = "chat_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_chat_title = "New Chat"
        self.messages = [self.get_system_prompt()]
        self.ui_messages =[]
        if self.chat_loaded: self.refresh_ui()

        self.update_token_counter(0)

        self.save_current_chat()
        self.load_chat_list()

    def load_chat_list(self):
        self.chat_list_widget.clear()
        try:
            files = sorted(Path("chats").glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
            for f in files:
                try:
                    with open(f, "r") as file:
                        data = json.load(file)
                        title = data.get("title", f.stem)
                        item = QListWidgetItem(title)
                        item.setData(Qt.ItemDataRole.UserRole, f.name)
                        self.chat_list_widget.addItem(item)
                except Exception: pass
        except Exception: pass

    def save_current_chat(self):
        filepath = Path("chats") / f"{self.current_chat_id}.json"
        data = { "title": self.current_chat_title, "messages": self.messages, "ui_messages": self.ui_messages }
        with open(filepath, "w") as f: json.dump(data, f)
        self.load_chat_list()

    def on_chat_selected(self, item):
        if self.worker and self.worker.isRunning(): return
        filename = item.data(Qt.ItemDataRole.UserRole)
        filepath = Path("chats") / filename
        try:
            with open(filepath, "r") as f: data = json.load(f)
            self.current_chat_id = filepath.stem
            self.current_chat_title = data.get("title", "Chat")
            
            self.messages = data.get("messages",[self.get_system_prompt()])
            self.ui_messages = data.get("ui_messages",[])
            
            # Retrofit IDs & convert old Base64 saves to raw content strings
            for i, m in enumerate(self.messages):
                if "__id__" not in m:
                    m["__id__"] = str(uuid.uuid4())
                    if i > 0 and (i - 1) < len(self.ui_messages):
                        self.ui_messages[i - 1]["__id__"] = m["__id__"]
                        
            for um in self.ui_messages:
                if "b64" in um:
                    try:
                        um["content"] = base64.b64decode(um["b64"]).decode('utf-8')
                        del um["b64"]
                    except:
                        um["content"] = ""

            self.refresh_ui()
        except Exception: pass

    def show_chat_context_menu(self, pos):
        item = self.chat_list_widget.itemAt(pos)
        if not item: return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #18181b; color: #e4e4e7; border: 1px solid #27272a; border-radius: 4px; padding: 4px; }
            QMenu::item { padding: 6px 24px 6px 24px; border-radius: 4px; }
            QMenu::item:selected { background-color: #27272a; }
        """)
        rename_action = menu.addAction("✏️ Rename")
        delete_action = menu.addAction("🗑️ Delete")
        action = menu.exec(self.chat_list_widget.mapToGlobal(pos))
        if action == rename_action: self.rename_chat(item)
        elif action == delete_action: self.delete_chat(item)

    def rename_chat(self, item):
        new_name, ok = QInputDialog.getText(self, "Rename Chat", "Enter new chat name:", text=item.text())
        if ok and new_name.strip():
            filename = item.data(Qt.ItemDataRole.UserRole)
            filepath = Path("chats") / filename
            try:
                with open(filepath, "r") as f: data = json.load(f)
                data["title"] = new_name.strip()
                with open(filepath, "w") as f: json.dump(data, f)
                if self.current_chat_id == filepath.stem:
                    self.current_chat_title = new_name.strip()
                self.load_chat_list()
            except Exception: pass

    def delete_chat(self, item):
        reply = QMessageBox.question(self, "Delete Chat", "Are you sure you want to delete this chat?", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            filename = item.data(Qt.ItemDataRole.UserRole)
            filepath = Path("chats") / filename
            try:
                filepath.unlink()
                if self.current_chat_id == filepath.stem: self.new_chat()
                else: self.load_chat_list()
            except Exception: pass

    def on_chat_load_finished(self, ok):
        if ok:
            self.chat_loaded = True
            self.refresh_ui()

    def refresh_ui(self):
        if not self.chat_loaded: return
        # Direct JS injection using JSON (No Base64 needed)
        self.chat_view.page().runJavaScript(f"renderMessages({json.dumps(self.ui_messages)});")

    def handle_delete(self, msg_id):
        if self.worker and self.worker.isRunning(): return
        self.messages = [m for m in self.messages if m.get("__id__") != msg_id]
        self.ui_messages =[um for um in self.ui_messages if um.get("__id__") != msg_id]
        self.save_current_chat()
        # No refresh_ui() needed: The JS side handles removing the DOM element smoothly!

    def open_settings(self):
        dlg = SettingsDialog(self.app_settings, self)
        dlg.exec()

    def on_vault_file_double_clicked(self, index):
        file_path = self.file_model.filePath(index)
        if Path(file_path).is_file() and file_path.endswith('.md'):
            viewer = MarkdownViewerWindow(file_path, self)
            viewer.show()

    def open_file_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Select Attachments", "", "Media Files (*.png *.jpg *.jpeg *.gif *.webp *.mp4 *.mov *.avi)")
        if paths: self.add_attachments(paths)

    def add_attachments(self, paths):
        for path in paths:
            if path not in self.attached_files:
                self.attached_files.append(path)
                thumb = AttachmentThumbnail(path)
                thumb.remove_clicked.connect(self.remove_attachment)
                self.attachment_layout.addWidget(thumb)
        if self.attached_files: self.attachment_widget.show()

    def remove_attachment(self, path):
        if path in self.attached_files: self.attached_files.remove(path)
        for i in reversed(range(self.attachment_layout.count())): 
            widget = self.attachment_layout.itemAt(i).widget()
            if isinstance(widget, AttachmentThumbnail) and widget.file_path == path:
                widget.setParent(None)
                widget.deleteLater()
        if not self.attached_files: self.attachment_widget.hide()

    def send_user_message(self):
        if self.worker and self.worker.isRunning(): return
        
        raw_text = self.input_field.toPlainText().strip()
        images = self.attached_files.copy()
        
        if not raw_text and not images: return

        if len(self.messages) == 1:
            title_text = raw_text.strip() if raw_text.strip() else "Media Upload"
            title_text = title_text.replace('\n', ' ')
            self.current_chat_title = title_text[:18] + "..." if len(title_text) > 18 else title_text

        display_raw = raw_text if raw_text else ""
        for img in images:
            if img.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                display_raw += f"\n\n_[Attached Video: {Path(img).name}]_"
            else:
                with open(img, "rb") as f: b64 = base64.b64encode(f.read()).decode('utf-8')
                ext = 'jpeg' if img.lower().endswith('jpg') else Path(img).suffix[1:].lower()
                display_raw += f"\n\n<img class='chat-img' src='data:image/{ext};base64,{b64}'>"
                
        msg_id = str(uuid.uuid4())
        user_msg = {"role": "user", "content": display_raw, "__id__": msg_id}
        
        self.ui_messages.append(user_msg)
        if self.chat_loaded:
            # Append smoothly without re-rendering the whole chat
            self.chat_view.page().runJavaScript(f"appendMessage({json.dumps(user_msg)});")
        
        if images:
            content =[{"type": "text", "text": raw_text if raw_text else "Attached files below:"}]
            for path in images:
                if path.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    content.append({"type": "text", "text": f"[User attached a video file: {path}. Note: direct video processing may be limited.]"})
                else:
                    with open(path, "rb") as f: b64_img = base64.b64encode(f.read()).decode('utf-8')
                    ext = 'jpeg' if path.lower().endswith('jpg') else Path(path).suffix[1:].lower()
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64_img}"}})
            self.messages.append({"role": "user", "content": content, "__id__": msg_id})
        else:
            self.messages.append({"role": "user", "content": raw_text, "__id__": msg_id})

        self.input_field.clear()
        for path in self.attached_files.copy(): self.remove_attachment(path)
        self.save_current_chat()

        # --- ADD THESE TWO LINES ---
        self.send_btn.setText("Stop 🛑")
        self.send_btn.setStyleSheet("background-color: #ef4444; color: white;") # Make it red
        # ---------------------------

        self.worker = AgentWorker(self.messages, self.app_settings)
        self.worker.new_message.connect(self.display_ai_message)
        self.worker.status_update.connect(self.update_status)
        self.worker.token_update.connect(self.update_token_counter)
        self.worker.start()

    def display_ai_message(self, role, content, msg_id):
        msg = {"role": role, "content": content, "__id__": msg_id}
        self.ui_messages.append(msg)
        if self.chat_loaded:
            self.chat_view.page().runJavaScript(f"appendMessage({json.dumps(msg)});")

    def update_status(self, status):
        self.status_label.setText(status)
        if status == "READY":
            # --- ADD THESE THREE LINES ---
            self.send_btn.setText("Send ✈️")
            self.send_btn.setEnabled(True)
            self.send_btn.setStyleSheet("") # Clear custom style
            # -----------------------------
            self.save_current_chat()
    
    def update_token_counter(self, count):
        self.token_label.setText(f"Tokens: {count:,}")

if __name__ == "__main__":
    if os.name == 'nt':
        try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('kaptcha.vault.agent.4.0')
        except Exception: pass
    app = QApplication(sys.argv)
    app.setWindowIcon(create_app_icon()) 
    window = KaptchaApp()
    window.show()
    sys.exit(app.exec())