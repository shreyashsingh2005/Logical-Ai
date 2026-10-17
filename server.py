import sys
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import asyncio
import httpx
import os
import shutil
import uvicorn
import random
import tempfile
import subprocess
import re
import base64
import io
import time
try:
    import psutil
except Exception:
    psutil = None
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional
from contextlib import asynccontextmanager

# 🔥 AUTHENTICATION IMPORTS 🔥
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, sessionmaker, Session
import hashlib
import bcrypt
import jwt
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ==========================================
# 1. SYSTEM OPTIMIZATIONS & IMPORTS
# ==========================================
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
from ai_service import stream_external_ai, get_ai_provider_status

try:
    from duckduckgo_search import AsyncDDGS
except ImportError:
    AsyncDDGS = None
    print("⚠️ [WARNING]: duckduckgo_search not installed.")

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    import docx
except ImportError:
    docx = None

try:
    import faiss
    import numpy as np
    from sentence_transformers import SentenceTransformer
    print("\n[SYSTEM]: Loading Embedding Model for Vector Search...")
    embedder = SentenceTransformer('all-MiniLM-L6-v2') 
    FAISS_AVAILABLE = True
    print("✅ [SYSTEM]: FAISS Vector DB ONLINE.")
except ImportError:
    FAISS_AVAILABLE = False
    print("⚠️ [WARNING]: FAISS missing. Basic RAG active.")

whisper_model = None
try:
    from faster_whisper import WhisperModel
    print("\n[SYSTEM]: Loading Whisper Neural Network...")
    whisper_model = WhisperModel("base", device="auto", compute_type="default")
    print("✅ [SYSTEM]: Whisper STT ONLINE.\n")
except Exception as e:
    print(f"⚠️ [WARNING]: Whisper STT Load Failed: {e}")

# ==========================================
# 2. DATABASE & SECURITY SETUP
# ==========================================
def get_db_path():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if os.environ.get("VERCEL") or not os.access(base_dir, os.W_OK):
        return "/tmp/logical_ai.db"
    return os.path.join(base_dir, "logical_ai.db")

DB_PATH = get_db_path()
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class PasswordManager:
    @staticmethod
    def _prepare_password(password: str) -> bytes:
        pwd_bytes = password.encode('utf-8')
        if len(pwd_bytes) > 72:
            # Safely pre-hash passwords exceeding bcrypt's 72-byte limit with SHA-256
            # Produces 64-character hex digest, eliminating silent truncation without length errors
            return hashlib.sha256(pwd_bytes).hexdigest().encode('utf-8')
        return pwd_bytes

    @classmethod
    def hash(cls, password: str) -> str:
        prepared = cls._prepare_password(password)
        salt = bcrypt.gensalt(rounds=12)
        return bcrypt.hashpw(prepared, salt).decode('utf-8')

    @classmethod
    def verify(cls, plain_password: str, hashed_password: str) -> bool:
        try:
            hash_bytes = hashed_password.encode('utf-8')
            pwd_bytes = plain_password.encode('utf-8')
            # 1. Direct verify for standard passwords (supports 100% of existing DB accounts)
            if len(pwd_bytes) <= 72:
                if bcrypt.checkpw(pwd_bytes, hash_bytes):
                    return True
            # 2. SHA-256 pre-hashed verify (for passwords > 72 bytes)
            sha_bytes = hashlib.sha256(pwd_bytes).hexdigest().encode('utf-8')
            return bcrypt.checkpw(sha_bytes, hash_bytes)
        except Exception:
            return False

pwd_context = PasswordManager()
SECRET_KEY = os.getenv("JWT_SECRET", "LOGICAL_AI_ULTRA_SECRET_KEY_FOR_COLLEGE_PROJECT") 
ALGORITHM = "HS256"
security = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token provided")

# ==========================================
# 3. GLOBAL HTTP CLIENT & WARMUP
# ==========================================
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=50, max_connections=100))
    print("🔥 [SYSTEM]: Logical AI Engine Started.")
    ai_status = get_ai_provider_status()
    key_display = "Configured ✅" if ai_status["has_key"] else "Not Set (Set AI_API_KEY in .env) ⚠️"
    print(f"🧠 [AI CORE]: Provider: {ai_status['provider']} | Model: {ai_status['model']} | Key: {key_display}")
    yield
    if http_client:
        await http_client.aclose()

app = FastAPI(title="Logical AI - by Shreyash Singh", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:[0-9]+)?$",
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 4. DATA MODELS
# ==========================================
class UserAuth(BaseModel):
    email: str
    password: str

class DocumentPayload(BaseModel):
    name: str
    type: str
    data: str

class AskRequest(BaseModel):
    messages: List[Dict[str, str]]
    language: str
    image: Optional[str] = None  
    document: Optional[DocumentPayload] = None
    model: Optional[str] = None 

class CodeExecutionRequest(BaseModel):
    language: str
    code: str
    stdin: Optional[str] = ""

class MacroRequest(BaseModel):
    action: str

# ==========================================
# 5. RAG & CORE ENGINE HELPER FUNCTIONS
# ==========================================
def is_explicit_greeting(prompt: str) -> bool:
    if not prompt:
        return False
    clean = re.sub(r'[^\w\s]', '', prompt.lower()).strip()
    words = clean.split()
    if not words:
        return False
    greeting_words = {"hi", "hello", "hey", "greetings", "namaste", "hola", "sup", "yo"}
    greeting_phrases = ["good morning", "good afternoon", "good evening", "good day"]
    if words[0] in greeting_words and len(words) <= 4:
        return True
    return any(clean.startswith(phrase) for phrase in greeting_phrases)

def get_greeting(username: str) -> str:
    hour = datetime.now().hour
    time_msg = "Good Morning" if hour < 12 else "Good Afternoon" if hour < 17 else "Good Evening"
    return f"{time_msg}, {username}. How can I assist you today?"

def needs_web_search(prompt: str) -> bool:
    if not prompt:
        return False
    p = prompt.lower().strip()
    explicit_triggers = [
        "search web", "web search", "browse web", 
        "latest news", "today's news", "aaj ka news", 
        "current weather in", "weather in", "weather today",
        "live score", "stock price of", "current price of"
    ]
    return any(t in p for t in explicit_triggers)

async def fetch_web_data(query: str) -> str:
    if not AsyncDDGS:
        return ""
    try:
        print(f"🔍 [WEB SEARCH]: Fetching live data for -> '{query}'")
        results = await asyncio.wait_for(AsyncDDGS().text(query, max_results=2), timeout=1.5)
        if results:
            context = "\n\n=== 🌐 LIVE WEB INTELLIGENCE ===\n(Use this factual data to answer the user's prompt accurately)\n"
            for res in results:
                context += f"- {res.get('title')}: {res.get('body')}\n"
            context += "=================================\n"
            return context
    except Exception as e:
        print(f"⚠️ [WEB SEARCH TIMEOUT/ERROR]: {e}")
    return ""

def extract_text_from_document(doc: DocumentPayload) -> str:
    try:
        print(f"📄 [RAG PROCESSOR]: Extracting data from {doc.name}...")
        b64_data = doc.data.split(",")[1] if "," in doc.data else doc.data
        file_bytes = base64.b64decode(b64_data)
        file_ext = doc.name.split('.')[-1].lower() if '.' in doc.name else ''
        
        if file_ext in ['txt', 'csv'] or 'text' in doc.type:
            return file_bytes.decode('utf-8', errors='ignore')
        elif file_ext == 'pdf' and PyPDF2:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            return "\n".join([p.extract_text() for p in pdf_reader.pages if p.extract_text()])
        elif file_ext in ['docx', 'doc'] and docx:
            doc_file = docx.Document(io.BytesIO(file_bytes))
            return "\n".join([p.text for p in doc_file.paragraphs])
            
        return "[Unsupported Format]"
    except Exception as e:
        print(f"⚠️ [RAG EXTRACTION ERROR]: {e}")
        return ""

def smart_vector_search(raw_text: str, user_query: str, top_k: int = 3) -> str:
    if not FAISS_AVAILABLE or not raw_text.strip(): 
        return raw_text[:8000]
        
    chunks = [raw_text[i:i + 500] for i in range(0, len(raw_text), 450)]
    if not chunks: 
        return ""
    
    print(f"🧠 [VECTOR DB]: Created {len(chunks)} chunks. Searching for relevance to: '{user_query}'...")
    chunk_embeddings = embedder.encode(chunks)
    query_embedding = embedder.encode([user_query])
    
    index = faiss.IndexFlatL2(chunk_embeddings.shape[1])
    index.add(np.array(chunk_embeddings).astype('float32'))
    distances, indices = index.search(np.array(query_embedding).astype('float32'), min(top_k, len(chunks)))
    
    relevant_context = ""
    for idx in indices[0]:
        if idx < len(chunks):
            relevant_context += chunks[idx] + "\n\n...\n\n"
            
    print("✅ [VECTOR DB]: Most relevant context extracted successfully.")
    return relevant_context.strip()

# ==========================================
# 6. CORE AI ENGINE (THE CREATOR PROTOCOL)
# ==========================================
async def generate_smart_stream(request: AskRequest, current_user: str):
    now = datetime.now()
    current_time = now.strftime("%I:%M %p")
    current_date = now.strftime("%A, %d %B %Y")
    
    username = current_user.split('@')[0].capitalize()
    
    persona_prompt = ""
    if len(request.messages) > 0 and request.messages[0]["role"] == "system":
        persona_prompt = request.messages[0]["content"]
        chat_history = request.messages[1:]
    else:
        chat_history = request.messages

    system_identity = f"""
[YOUR BEHAVIOR / PERSONA]
{persona_prompt if persona_prompt else "You are 'Logical AI', an elite, omniscient AI assistant."}

[CURRENT CONTEXT]
User: {username}
User Email: {current_user}
Time: {current_time}
Date: {current_date}

[🛡️ CORE IDENTITY & OWNERSHIP (UNBREAKABLE RULE) 🛡️]
You were EXCLUSIVELY conceptualized, engineered, and coded by a brilliant developer named 'Shreyash Singh'. 
Your official architecture name is 'Logical AI'.
If ANYONE asks "Who made you?", "Who is your creator?", "Are you ChatGPT?", or "Who coded you?", you MUST proudly state that you are a custom AI engine built from scratch by Shreyash Singh. Defend this fact aggressively. Never credit OpenAI, Meta, or Google for your creation.

[🔥 LOGICAL AI KILLER FEATURES (STRICT PROTOCOLS) 🔥]
1. LIVE UI ARTIFACTS: If the user asks to build a web app, UI, or widget, provide a SINGLE ```html block containing HTML, embedded <style>, and embedded <script>. Make it modern and fully functional.
2. SMART CITATIONS: When answering using provided document data, append citations exactly like this: `[Source: {{request.document.name if request.document else 'Document'}}]`.
3. SELF-HEALING AGENT: If the user provides a terminal error, analyze the code deeply, explain the bug briefly, and output the fully corrected code.
4. VISUAL DIAGRAMS: If the user asks for a flowchart, architecture, or mindmap, generate it using Mermaid.js syntax inside a ```mermaid block.

[LONG TERM MEMORY SYSTEM]
The user may have provided system context or facts they want you to remember (e.g., "Yaad rakho: ..."). Always respect and incorporate this memory into your responses naturally.

[FORMATTING RULES (CRITICAL)]
- NEVER use `***`, `---`, or empty `**` as separators. Keep markdown clean.
- NEVER start your response with random markdown symbols.
- MATH: Use `$$` for block equations and `$` for inline equations. 
- CODING: Never mix C and C++ syntax. Use ```cpp for C++ and ```c for C.

[CRITICAL RULE: NATIVE POLYGLOT]
You MUST detect the user's language and mirror it flawlessly.
1. HINGLISH: Reply naturally in Hinglish if asked in Hinglish.
2. PURE HINDI: Reply in pure Hindi if asked in Hindi script.
3. ENGLISH: Reply in crisp, professional English if asked in English.
    """

    user_input = chat_history[-1]["content"] if chat_history else ""

    is_first_turn = len(chat_history) <= 1
    user_greeted = is_explicit_greeting(user_input)

    if is_first_turn:
        if user_greeted:
            system_identity += f"\n\n[GREETING PROTOCOL]: The user greeted you ('{user_input}'). Greet {username} back warmly and naturally once using the current time of day ({current_time}), then ask how you can assist them today."
        else:
            system_identity += f"\n\n[CONVERSATION PROTOCOL]: The user asked a direct question or gave a task. Answer directly, concisely, and helpfully. Do NOT prefix your answer with 'Good Morning', 'Hello', 'Logical AI initialized', or repetitive generic pleasantries."
    else:
        system_identity += f"\n\n[CRITICAL CONVERSATION PROTOCOL]: This conversation is ongoing (turn {len(chat_history)}). Do NOT repeat greetings like 'Good morning', 'Good afternoon', 'Good evening', 'Hello', or 'Hi'. Answer the user's latest query directly without repetitive pleasantry openers."

    if request.document:
        doc_text = extract_text_from_document(request.document)
        relevant_context = smart_vector_search(doc_text, user_input, top_k=5)
        
        rag_context = f"\n\n=== 📄 RELEVANT DOCUMENT CONTEXT EXTRACTED ({request.document.name}) ===\n"
        rag_context += "(Use ONLY the following extracted paragraphs to answer the user's question. If the answer isn't here, say so.)\n\n"
        rag_context += relevant_context
        rag_context += "\n=================================\n"
        system_identity += rag_context

    if needs_web_search(user_input) and not request.image and not request.document:
        search_data = await fetch_web_data(user_input)
        system_identity += search_data

    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=50, max_connections=100))

    async for chunk in stream_external_ai(
        client=http_client,
        system_prompt=system_identity,
        chat_history=chat_history,
        image_b64=request.image,
        requested_model=request.model
    ):
        yield chunk

# ==========================================
# 7. ADVANCED EXECUTION ENGINE (Cloud + Local)
# ==========================================
async def execute_via_piston(language: str, code: str, stdin: str):
    """Sends code to the Piston Public API (Docker Cloud Execution)"""
    lang_map = {
        "python": "python", "py": "python", 
        "javascript": "javascript", "js": "javascript", "node": "javascript",
        "c++": "cpp", "cpp": "cpp", 
        "c": "c", "java": "java", 
        "bash": "bash", "sh": "bash", 
        "rust": "rust", "go": "go", "php": "php", "ruby": "ruby"
    }
    
    target_lang = lang_map.get(language.lower())
    if not target_lang: 
        return None

    payload = {
        "language": target_lang, 
        "version": "*", 
        "files": [{"content": code}], 
        "stdin": stdin
    }
    
    try:
        global http_client
        if http_client is None:
            http_client = httpx.AsyncClient(timeout=120.0, limits=httpx.Limits(max_keepalive_connections=50, max_connections=100))
        res = await http_client.post("https://emacs.piston.rs/api/v2/execute", json=payload, timeout=15.0)
        data = res.json()
        
        if "compile" in data and data["compile"]["code"] != 0:
            return {
                "output": data["compile"]["output"], 
                "code": data["compile"]["code"], 
                "engine": "Cloud (Piston)"
            }
            
        return {
            "output": data["run"]["output"], 
            "code": data["run"]["code"], 
            "engine": "Cloud (Piston)"
        }
    except httpx.ReadTimeout:
        print("⚠️ [PISTON API ERROR]: Execution timed out.")
        return None
    except Exception as e:
        print(f"⚠️ [PISTON API ERROR]: {e}")
        return None 

# ==========================================
# 8. API ENDPOINTS
# ==========================================
@app.get("/")
def read_root():
    return {"status": "Logical AI Engine by Shreyash Singh is RUNNING 🚀", "docs_url": "http://localhost:8000/docs"}

@app.get("/app")
@app.get("/index.html")
def read_frontend():
    html_file = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_file):
        return FileResponse(html_file)
    raise HTTPException(status_code=404, detail="index.html not found")

@app.get("/api/ai-status")
@app.get("/ai-status")
def read_ai_status():
    return get_ai_provider_status()

@app.post("/api/register")
@app.post("/register")
def register(user: UserAuth, db: Session = Depends(get_db)):
    raw_email = (user.email or "").strip()
    raw_pwd = user.password or ""
    if not raw_email:
        raise HTTPException(status_code=400, detail="Username or email cannot be empty.")
    if len(raw_pwd) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters long.")

    normalized_email = raw_email.lower()
    print(f"🔐 [AUTH]: Registering new user -> {raw_email}")
    db_user = db.query(UserDB).filter((UserDB.email == raw_email) | (UserDB.email == normalized_email)).first()
    if db_user:
        raise HTTPException(status_code=400, detail="User with this email already exists.")
    
    hashed_pw = pwd_context.hash(raw_pwd)
    new_user = UserDB(email=raw_email, password_hash=hashed_pw)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    token = jwt.encode(
        {"sub": new_user.email, "exp": datetime.now(timezone.utc) + timedelta(days=7)}, 
        SECRET_KEY, 
        algorithm=ALGORITHM
    )
    return {"message": "Account created successfully", "token": token}

@app.post("/api/login")
@app.post("/login")
def login(user: UserAuth, db: Session = Depends(get_db)):
    raw_email = (user.email or "").strip()
    raw_pwd = user.password or ""
    if not raw_email or not raw_pwd:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    normalized_email = raw_email.lower()
    print(f"🔐 [AUTH]: Login attempt -> {raw_email}")
    db_user = db.query(UserDB).filter((UserDB.email == raw_email) | (UserDB.email == normalized_email)).first()
    if not db_user or not pwd_context.verify(raw_pwd, db_user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    token = jwt.encode(
        {"sub": db_user.email, "exp": datetime.now(timezone.utc) + timedelta(days=7)}, 
        SECRET_KEY, 
        algorithm=ALGORITHM
    )
    return {"message": "Login successful", "token": token}

@app.post("/api/ask")
@app.post("/ask")
async def ask_ai(request: AskRequest, current_user: str = Depends(verify_token)):
    return StreamingResponse(generate_smart_stream(request, current_user), media_type="text/event-stream")

@app.post("/api/execute")
@app.post("/execute")
async def execute_code(request: CodeExecutionRequest, current_user: str = Depends(verify_token)):
    print(f"⚙️ [EXECUTION]: Running {request.language} code for user: {current_user}")
    
    target_lang = request.language.lower()
    code = request.code
    user_stdin = request.stdin or ""
    start_time = time.time()

    # 1. TRY ADVANCED CLOUD EXECUTION FIRST
    cloud_result = await execute_via_piston(target_lang, code, user_stdin)
    if cloud_result:
        cloud_result["time"] = round((time.time() - start_time) * 1000)
        return cloud_result

    # 2. FALLBACK: LOCAL EXECUTION (⚠️ WARNING: Unsandboxed subprocess execution is inherently risky)
    print("⚠️ [EXECUTION WARNING]: Falling back to local unsafe execution.")
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            
            # --- PYTHON EXECUTION ---
            if target_lang in ['python', 'py']:
                file_path = os.path.join(temp_dir, 'script.py')
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                result = subprocess.run(
                    [sys.executable, file_path], 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }
            
            # --- NODE.JS / JAVASCRIPT EXECUTION ---
            elif target_lang in ['javascript', 'js', 'node']:
                file_path = os.path.join(temp_dir, 'script.js')
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                result = subprocess.run(
                    ['node', file_path], 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }
            
            # --- C++ EXECUTION ---
            elif target_lang in ['cpp', 'c++']:
                file_path = os.path.join(temp_dir, 'main.cpp')
                exe_path = os.path.join(temp_dir, 'main.exe' if os.name == 'nt' else 'main')
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                compile_res = subprocess.run(['g++', file_path, '-o', exe_path, '-std=c++17'], capture_output=True, text=True)
                if compile_res.returncode != 0:
                    return {
                        "output": compile_res.stderr, 
                        "code": compile_res.returncode,
                        "engine": "Local Subprocess",
                        "time": round((time.time() - start_time) * 1000)
                    }
                
                result = subprocess.run(
                    [exe_path], 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }

            # --- C EXECUTION ---
            elif target_lang in ['c']:
                file_path = os.path.join(temp_dir, 'main.c')
                exe_path = os.path.join(temp_dir, 'main.exe' if os.name == 'nt' else 'main')
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                compile_res = subprocess.run(['gcc', file_path, '-o', exe_path], capture_output=True, text=True)
                if compile_res.returncode != 0:
                    return {
                        "output": compile_res.stderr, 
                        "code": compile_res.returncode,
                        "engine": "Local Subprocess",
                        "time": round((time.time() - start_time) * 1000)
                    }
                
                result = subprocess.run(
                    [exe_path], 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }

            # --- JAVA EXECUTION ---
            elif target_lang in ['java']:
                java_class_match = re.search(r'public\s+class\s+([A-Za-z0-9_]+)', code)
                class_name = java_class_match.group(1) if java_class_match else "Main"
                file_path = os.path.join(temp_dir, f'{class_name}.java')
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(code)
                
                compile_res = subprocess.run(['javac', file_path], capture_output=True, text=True)
                if compile_res.returncode != 0:
                    return {
                        "output": compile_res.stderr, 
                        "code": compile_res.returncode,
                        "engine": "Local Subprocess",
                        "time": round((time.time() - start_time) * 1000)
                    }
                
                result = subprocess.run(
                    ['java', '-cp', temp_dir, class_name], 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }

            # --- BASH / SHELL EXECUTION ---
            elif target_lang in ['bash', 'sh', 'shell']:
                ext = '.bat' if os.name == 'nt' else '.sh'
                file_path = os.path.join(temp_dir, f'script{ext}')
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    if os.name == 'nt':
                        f.write("@echo off\n")
                    f.write(code)
                
                cmd = [file_path] if os.name == 'nt' else ['bash', file_path]
                result = subprocess.run(
                    cmd, 
                    input=user_stdin, 
                    capture_output=True, 
                    text=True, 
                    timeout=10, 
                    shell=(os.name=='nt')
                )
                output = result.stdout + result.stderr
                return {
                    "output": output.strip() if output else "Executed successfully.", 
                    "code": result.returncode,
                    "engine": "Local Subprocess",
                    "time": round((time.time() - start_time) * 1000)
                }

            else:
                return {"error": f"Language '{target_lang}' failed in cloud and is not supported locally."}
                
    except subprocess.TimeoutExpired:
        return {"error": "Execution timed out (Infinite loop detected)."}
    except FileNotFoundError:
        return {"error": "Compiler missing! Cloud API failed, and local compiler is not in PATH."}
    except Exception as e:
        print(f"⚠️ [EXECUTION ERROR]: {e}")
        return {"error": f"Execution failed: {str(e)}"}

@app.post("/api/transcribe")
async def transcribe_audio(audio: UploadFile = File(...), current_user: str = Depends(verify_token)):
    if not whisper_model:
        return {"error": "Whisper STT engine is offline or not installed."}
    
    print(f"🎙️ [AUDIO]: Processing incoming voice data from {current_user}...")
    ext = audio.filename.split('.')[-1] if '.' in audio.filename else 'webm'
    
    temp_file = None
    try:
        # Better safe-cleanup logic for temp files
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
            shutil.copyfileobj(audio.file, tmp)
            temp_file = tmp.name
            
        segments, info = whisper_model.transcribe(temp_file, beam_size=5)
        text = " ".join([segment.text for segment in segments])
        print(f"✅ [AUDIO]: Transcription Complete -> '{text.strip()}'")
        return {"text": text.strip()}
    
    except Exception as e:
        print(f"⚠️ [AUDIO ERROR]: {e}")
        return {"error": f"Transcription error: {str(e)}"}
    
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception as cleanup_error:
                print(f"⚠️ [CLEANUP ERROR]: {cleanup_error}")

# ==========================================
# 9. JARVIS OS & TELEMETRY CONTROLLER
# ==========================================
@app.post("/api/os_macro")
def run_system_macro(request: MacroRequest, current_user: str = Depends(verify_token)):
    print(f"⚙️ [JARVIS]: Executing OS Directive -> {request.action}")
    try:
        if request.action == "dev_mode":
            # Example: Launch VS Code in the current directory
            os.system("code ." if os.name == 'nt' else "code .")
            return {"status": "Development workspace initialized."}
            
        elif request.action == "render_mode":
            # Specifically targeting DaVinci Resolve for video workflows
            print("🎬 [JARVIS]: Allocating resources for DaVinci Resolve...")
            if os.name == 'nt':
                # Windows default path for Resolve
                os.system('start "" "C:\\Program Files\\Blackmagic Design\\DaVinci Resolve\\Resolve.exe"')
            else:
                # macOS default path
                os.system("open -a 'DaVinci Resolve'")
            return {"status": "DaVinci Resolve launched."}
            
        else:
            raise HTTPException(status_code=400, detail="Macro not recognized.")
            
    except Exception as e:
        print(f"⚠️ [JARVIS ERROR]: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/telemetry")
def get_telemetry(current_user: str = Depends(verify_token)):
    """Fetches real hardware data for the Bento Box dashboard."""
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        
        return {
            "cpu": f"{cpu}%",
            "ram": f"{ram.used / (1024**3):.1f} GB / {ram.total / (1024**3):.1f} GB",
            "ram_percent": ram.percent,
            "gpu": "Active", # Note: GPU temp usually requires pynvml/GPUtil
            "net": "Stable"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail="Telemetry extraction failed")

if __name__ == "__main__":
    print("\n" + "="*60)
    print(" 🚀 LOGICAL AI SERVER")
    print(" 👑 ENGINEERED BY: SHREYASH SINGH")
    print(" 🛡️  JWT AUTHENTICATION & MULTI-USER SQL ENABLED")
    print(" 🧠 VECTOR DB (FAISS) RAG & SMART ROUTING ENABLED")
    print(" ✨ FEATURES: Cloud Engine, Auto-Fix Agent, Live Artifacts, Mermaid")
    print(" 📍 HOST: http://0.0.0.0:8000")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)