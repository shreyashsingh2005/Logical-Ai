# Logical AI ⚡

> An intelligent, full-stack AI workspace featuring real-time streaming, multi-turn reasoning, an integrated execution sandbox, dynamic session management, and a refined responsive UI.

**Engineered by Shreyash Singh**

---

## 🌟 Key Features

* **⚡ Conversational Intelligence:** Direct streaming completion with Google Gemini (`gemini-3.8-flash`) and configurable provider support (OpenAI, Claude, Groq, DeepSeek).
* **🖥️ Interactive Execution Sandbox:** Live Python and JavaScript sandbox code execution with standard input (`stdin`) and real-time terminal output.
* **🌓 Dual Theme System:** Seamless Dark, Light, and System Default themes with strict WCAG AA contrast.
* **📱 Responsive Design:** Adaptive layouts across mobile drawer navigation, tablet viewports, and desktop split views.
* **⚙️ Production Settings Modal:** 
  * Theme switcher
  * Live AI provider status and model connectivity check
  * One-click session PDF export
  * Secure local chat history management
* **🔒 Authentication:** Built-in JWT authentication with password hashing and local session state.

---

## 🚀 Quick Start

### 1. Prerequisites
* **Node.js** (v18 or higher)
* **Python** (v3.10 or higher)

### 2. Clone and Install

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/logical-ai.git
cd logical-ai

# Install frontend dependencies
npm install

# Create virtual environment & install backend dependencies
python -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the root directory (based on `.env.example`):

```env
AI_PROVIDER=gemini
AI_API_KEY=your_gemini_api_key_here
AI_MODEL=gemini-3.8-flash
JWT_SECRET=your_secret_key_here
```

### 4. Run the Application

```bash
# Start both Backend (port 8000) and Frontend (port 5173) concurrently:
npm run dev
```

Open your browser at `http://localhost:5173`.

---

## 🏗️ Project Architecture

```
Logical AI/
├── index.html            # Vite SPA frontend (Sidebar, Chat, Composer, Settings)
├── server.py             # FastAPI backend (Auth, /api/ask SSE streaming, /api/execute sandbox)
├── ai_service.py         # Unified multi-provider AI adapter (Gemini, OpenAI, Claude)
├── package.json          # Node dependencies & Vite build scripts
├── requirements.txt      # Python backend dependencies
├── vercel.json           # Vercel deployment configuration
├── start-server.js       # Background server launcher
└── .env.example          # Sample environment configuration
```

---

## 🌐 Deployment

* **Frontend (Vercel):** The repository is pre-configured with `vercel.json` for one-click deployment on [Vercel](https://vercel.com).
* **Backend (Render / Railway / VPS):** Deploy `server.py` as a Python Web Service with `uvicorn server:app --host 0.0.0.0 --port $PORT`.

---

## 📄 License

Author: **Shreyash Singh**  
All rights reserved.
