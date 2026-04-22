# COBOL to Python Agentic Migrator

An AI-powered tool that automatically translates COBOL programs to Python using a multi-agent architecture with LangGraph. The system iteratively translates, tests, and refines the output until the Python code produces equivalent results to the original COBOL.

## Features

- **Multi-provider LLM Support**: OpenAI, Anthropic (Claude), Google (Gemini), and xAI (Grok)
- **Agentic Translation Loop**: Iterative refinement with automated testing
- **Four-tier Validation Stack**:
  - Differential testing (input/output comparison)
  - Property-based testing (with Hypothesis)
  - LLM-as-Judge evaluation
  - Static analysis (with Pyflakes)
- **Real-time Progress**: Server-Sent Events (SSE) for live updates
- **Isolated Test Execution**: Sandboxed environment with virtual environments
- **Migration History**: SQLite persistence for all migration records

## Prerequisites

Before running the setup script, ensure you have:

- **Python 3.11+** - [Download](https://www.python.org/downloads/)
- **Node.js 18+** - [Download](https://nodejs.org/)
- **curl** - Usually pre-installed on Linux/macOS
- **git** - Usually pre-installed on Linux/macOS

### Check your versions:
```bash
python3 --version   # Should be 3.11 or higher
node --version      # Should be v18 or higher
```

## Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/YOUR_USERNAME/IE624-cobol-migrator.git
cd IE624-cobol-migrator
```

### 2. Run Setup
```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Install `uv` (Python package manager) if not present
- Install all Python dependencies
- Install all Node.js dependencies
- Create `.env` from `.env.example`
- Create required directories (`logs/`, `data/`)

### 3. Configure Your API Key

Edit `backend/.env` and add your LLM provider API key:

```bash
# Choose your provider
LLM_PROVIDER=openai  # Options: openai, anthropic, google, xai

# Add your API key (only the key for your selected provider is required)
OPENAI_API_KEY=sk-your-actual-key-here
```

### 4. Start the Application
```bash
./run.sh
```

The application will be available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **Health Check**: http://localhost:8000/health

Press `Ctrl+C` to stop both servers.

## Usage

1. Open http://localhost:5173 in your browser
2. Paste your COBOL code in the left editor panel
3. (Optional) Check "Create dummy files" if your COBOL reads from external files
4. Click "Start Migration"
5. Watch the agent reason through translation, testing, and refinement
6. View the final Python code in the right panel

## Run Modes

```bash
./run.sh              # Development mode (auto-reload on code changes)
./run.sh --no-reload  # Stable mode (recommended for actual migrations)
```

## Project Structure

```
.
├── backend/
│   ├── src/cobol_migrator/
│   │   ├── agent/          # LangGraph agent implementation
│   │   │   └── nodes/      # Individual agent nodes (planner, translate, etc.)
│   │   ├── validators/     # Validation stack components
│   │   ├── api.py          # FastAPI endpoints
│   │   ├── models.py       # LLM provider abstraction
│   │   └── config.py       # Configuration management
│   └── tests/              # Backend tests
├── frontend/
│   └── src/                # React/TypeScript frontend
├── stages/                 # Project planning documents
├── setup.sh                # One-time environment setup
├── run.sh                  # Start development servers
└── CLAUDE.md               # Coding conventions
```

## Supported LLM Providers

| Provider | Models | Environment Variable |
|----------|--------|---------------------|
| OpenAI | gpt-4o, gpt-4o-mini, gpt-4-turbo, o1 | `OPENAI_API_KEY` |
| Anthropic | claude-sonnet-4-20250514, claude-haiku-4-20250514 | `ANTHROPIC_API_KEY` |
| Google | gemini-2.0-flash, gemini-1.5-pro | `GOOGLE_API_KEY` |
| xAI | grok-2, grok-2-mini | `XAI_API_KEY` |

Configure models per task in `backend/.env`:
```bash
OPENAI_TRANSLATE_MODEL=gpt-4o
OPENAI_ANALYZE_MODEL=gpt-4o-mini
# ... etc
```

## Example COBOL Input

```cobol
       IDENTIFICATION DIVISION.
       PROGRAM-ID. HELLO.
       PROCEDURE DIVISION.
           DISPLAY "HELLO, WORLD".
           STOP RUN.
```

## Troubleshooting

### Port already in use
The `run.sh` script automatically clears ports 8000 and 5173. If issues persist:
```bash
fuser -k 8000/tcp
fuser -k 5173/tcp
```

### uv not found after setup
Add to your shell profile (`~/.bashrc` or `~/.zshrc`):
```bash
export PATH="$HOME/.local/bin:$PATH"
```

### API key errors
Ensure your `.env` file has the correct key for your chosen provider and that `LLM_PROVIDER` matches.

## Development

### Run Backend Tests
```bash
cd backend
uv run pytest                    # Fast tests only
uv run pytest -m slow            # Include slow integration tests
uv run ruff check src tests      # Linting
uv run ruff format src tests     # Formatting
```

### Frontend Development
```bash
cd frontend
npm run lint                     # ESLint
npm run build                    # Production build
```

## License

This project was created for IE624 coursework.
