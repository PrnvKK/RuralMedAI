"""Parchee Edge - Zero-to-Running Setup Script.

Downloads all assets, configures the environment, and prepares
everything needed to run Parchee Edge locally.

Usage:
    python scripts/setup.py

What it does:
    1. Checks prerequisites (Python, Git, Docker)
    2. Creates a Python virtual environment
    3. Installs all dependencies
    4. Downloads llama.cpp Windows binaries from GitHub releases
    5. Downloads Gemma 4 GGUF + multimodal projector via huggingface-hub
    6. Creates .env files with generated AES key
    7. Starts PostgreSQL via Docker
    8. Validates the setup
"""

import os
import sys
import json
import shutil
import base64
import secrets
import subprocess
import zipfile
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
BIN_DIR = BACKEND_DIR / "llama_cpp" / "bin"
VENV_DIR = REPO_ROOT / ".venv"

HF_MODEL_REPO = "unsloth/gemma-4-E2B-it-GGUF"
HF_GGUF_FILE = "gemma-4-E2B-it-Q4_K_M.gguf"
HF_MMPROJ_FILE = "mmproj-BF16.gguf"

LLAMA_CPP_RELEASE_API = (
    "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
)
PYTHON_MIN_VERSION = (3, 11)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _python_exe() -> str:
    if sys.platform == "win32":
        return str(VENV_DIR / "Scripts" / "python.exe")
    return str(VENV_DIR / "bin" / "python3")


def _pip_install(*packages: str):
    """Install packages via `python -m pip install` (avoids pip self-upgrade issues)."""
    return _run([_python_exe(), "-m", "pip", "install", *packages])


def _run(cmd, **kwargs):
    kwargs.setdefault("check", True)
    kwargs.setdefault("cwd", str(REPO_ROOT))
    return subprocess.run(cmd, **kwargs)


def _banner():
    print()
    print("=" * 52)
    print("  Parchee Edge - Rural Medical AI Setup")
    print("  Local-first medical scribe & claim assistant")
    print("=" * 52)
    print()


def _step(msg):
    print(f"  [{msg}]")


def _ok(msg=""):
    suffix = f" - {msg}" if msg else ""
    print(f"  [OK]{suffix}")


def _warn(msg):
    print(f"  [WARN] {msg}")


def _fail(msg):
    print(f"\n  [FAIL] {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1: Check prerequisites
# ---------------------------------------------------------------------------


def check_prerequisites():
    _step("Checking prerequisites")

    if sys.version_info < PYTHON_MIN_VERSION:
        _fail(
            f"Python {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+ required. "
            f"You have {sys.version_info.major}.{sys.version_info.minor}"
        )

    print(f"    Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")

    try:
        _run(["git", "--version"], capture_output=True)
        print("    Git available")
    except Exception:
        _warn("Git not found. You will need it to work with the repo.")

    try:
        _run(["docker", "--version"], capture_output=True)
        print("    Docker available")
    except Exception:
        _warn("Docker not found. PostgreSQL will need to be started manually.")

    _ok()


# ---------------------------------------------------------------------------
# Step 2: Create virtual environment
# ---------------------------------------------------------------------------


def setup_venv():
    _step("Setting up Python virtual environment")

    if VENV_DIR.exists():
        print("    Virtual environment already exists, recreating...")
        shutil.rmtree(VENV_DIR)

    _run([sys.executable, "-m", "venv", str(VENV_DIR), "--clear"])
    _ok(f"Created at {VENV_DIR}")


# ---------------------------------------------------------------------------
# Step 3: Install dependencies
# ---------------------------------------------------------------------------


def install_dependencies():
    _step("Installing Python dependencies")

    print("    Upgrading pip...")
    _pip_install("--upgrade", "pip")

    print("    Installing from requirements.txt (this may take a few minutes)...")
    _pip_install("-r", str(REPO_ROOT / "requirements.txt"))

    print("    Installing scispacy clinical NER model...")
    _install_scispacy()

    print("    Installing scispacy model en_core_sci_md...")
    _pip_install(
        "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_md-0.5.4.tar.gz",
    )

    _ok()


def _install_scispacy():
    """Install scispacy. Uses --no-deps if the resolver rejects its spaCy pin."""
    try:
        _pip_install("scispacy>=0.5.4")
    except subprocess.CalledProcessError:
        print("    scispacy's spaCy pin conflicts; installing with --no-deps...")
        _pip_install("scispacy>=0.5.4", "--no-deps")
        print("    scispacy installed (using already-installed spaCy)")


# ---------------------------------------------------------------------------
# Step 4: Download llama.cpp Windows binaries
# ---------------------------------------------------------------------------


def download_llama_cpp_binaries():
    _step("Downloading llama.cpp Windows binaries")

    existing = list(BIN_DIR.glob("llama-server*")) if BIN_DIR.exists() else []
    if existing:
        print("    llama.cpp binaries already present, skipping")
        _ok()
        return

    BIN_DIR.mkdir(parents=True, exist_ok=True)

    tag = _fetch_llama_cpp_tag()
    zip_name = f"llama-{tag}-bin-win-cpu-x64.zip"
    zip_url = (
        f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{zip_name}"
    )

    print(f"    Latest release: {tag}")
    print(f"    Downloading {zip_name} (~18 MB)...")

    tmp_path = None
    try:
        tmp_path = _download_file(zip_url, f"Downloading llama.cpp {tag}")
        print()

        print(f"    Extracting to {BIN_DIR}...")
        with zipfile.ZipFile(tmp_path, "r") as zf:
            for member in zf.namelist():
                if member.endswith((".exe", ".dll")):
                    target_name = Path(member).name
                    dest = BIN_DIR / target_name
                    with zf.open(member) as src:
                        with open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)

        os.unlink(tmp_path)

        server_exe = BIN_DIR / "llama-server.exe"
        if not server_exe.exists():
            _fail("llama-server.exe not found after extraction")

        file_count = len(list(BIN_DIR.glob("*")))
        print(f"    Extracted {file_count} files")

    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _fail(f"Binary download failed: {exc}")

    _ok()


def _fetch_llama_cpp_tag():
    try:
        req = Request(
            LLAMA_CPP_RELEASE_API,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ParcheeEdge-Setup",
            },
        )
        with urlopen(req, timeout=30) as resp:
            release = json.loads(resp.read())
        return release["tag_name"]
    except (URLError, json.JSONDecodeError, KeyError) as exc:
        _warn(f"Could not fetch latest release tag: {exc}")
        fallback = "b10107"
        print(f"    Using fallback tag: {fallback}")
        return fallback


def _download_file(url, label):
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    req = Request(url, headers={"User-Agent": "ParcheeEdge-Setup"})
    with urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length", "0") or "0")
        downloaded = 0
        chunk_size = 1024 * 1024
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            tmp.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = min(downloaded / total * 100, 100)
                print(f"\r    {label}: {pct:.0f}%", end="", flush=True)
    tmp.close()
    return tmp_path


# ---------------------------------------------------------------------------
# Step 5: Download Gemma 4 models via huggingface_hub
# ---------------------------------------------------------------------------


def download_models():
    _step("Downloading Gemma 4 GGUF models")

    model_script_lines = [
        "import sys",
        "from huggingface_hub import hf_hub_download",
        "",
        "try:",
        '    print("    Downloading Gemma 4 GGUF (Q4_K_M, ~4 GB)...")',
        "    gguf = hf_hub_download(",
        f'        repo_id="{HF_MODEL_REPO}",',
        f'        filename="{HF_GGUF_FILE}",',
        "        resume=True,",
        "    )",
        '    print(f"    Cached: {gguf}")',
        "",
        '    print("    Downloading multimodal projector...")',
        "    mmproj = hf_hub_download(",
        f'        repo_id="{HF_MODEL_REPO}",',
        f'        filename="{HF_MMPROJ_FILE}",',
        "        resume=True,",
        "    )",
        '    print(f"    Cached: {mmproj}")',
        "",
        '    print("RESULT")',
        "    print(gguf)",
        "    print(mmproj)",
        "except Exception as e:",
        '    print(f"ERROR: {e}", file=sys.stderr)',
        "    sys.exit(1)",
    ]

    model_script = "\n".join(model_script_lines)

    result = subprocess.run(
        [_python_exe(), "-c", model_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )

    if result.returncode != 0:
        _fail(f"Model download failed:\n{result.stderr}")

    lines = result.stdout.strip().split("\n")
    result_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "RESULT":
            result_idx = i
            break

    if result_idx is None or result_idx + 2 >= len(lines):
        _fail("Could not parse model download output")

    gguf_path = lines[result_idx + 1].strip()
    mmproj_path = lines[result_idx + 2].strip()

    gguf_file = Path(gguf_path)
    mmproj_file = Path(mmproj_path)

    if not gguf_file.exists():
        _fail(f"GGUF model not found at {gguf_path}")
    if not mmproj_file.exists():
        _fail(f"mmproj model not found at {mmproj_path}")

    gguf_gb = gguf_file.stat().st_size / (1024**3)
    print(f"    Gemma 4 GGUF: {gguf_gb:.1f} GB")

    _ok()
    return gguf_path, mmproj_path


# ---------------------------------------------------------------------------
# Step 6: Configure environment files
# ---------------------------------------------------------------------------


def setup_env_files(gguf_path, mmproj_path):
    _step("Configuring environment files")

    # Root .env
    root_env = REPO_ROOT / ".env.example"
    root_target = REPO_ROOT / ".env"
    if not root_target.exists() and root_env.exists():
        shutil.copy(root_env, root_target)
        print(f"    Created {root_target.name}")

    # Backend .env
    backend_env = BACKEND_DIR / ".env.example"
    backend_target = BACKEND_DIR / ".env"
    if backend_env.exists():
        env_text = backend_env.read_text(encoding="utf-8")

        aes_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        print("    Generated AES-256 key")

        bin_path = str(BIN_DIR / "llama-server.exe")

        replacements = {
            "replace_with_base64_32byte_key": aes_key,
            "llama_cpp/bin/llama-server.exe": bin_path,
            "llama_cpp/models/gemma-4.gguf": gguf_path,
            "llama_cpp/models/mmproj.gguf": mmproj_path,
        }

        for old, new in replacements.items():
            env_text = env_text.replace(old, new)

        env_text = env_text.replace(
            "LLAMA_SERVER_DOWNLOAD_MODELS=true",
            "LLAMA_SERVER_DOWNLOAD_MODELS=false",
        )

        backend_target.write_text(env_text, encoding="utf-8")
        print(f"    Updated paths in backend/{backend_target.name}")
    else:
        _warn("backend/.env.example not found - skipping env setup")

    _ok()


# ---------------------------------------------------------------------------
# Step 7: Start PostgreSQL via Docker
# ---------------------------------------------------------------------------


def start_postgres():
    _step("Starting PostgreSQL")

    try:
        result = subprocess.run(
            ["docker", "compose", "up", "-d", "postgres"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            _warn(f"Could not start PostgreSQL:\n{result.stderr.strip()}")
            print("    Start it manually: docker compose up -d postgres")
            return

        print("    PostgreSQL container started, waiting for healthy state...")

        for _ in range(30):
            time.sleep(1)
            result = subprocess.run(
                ["docker", "compose", "ps", "--format", "json", "postgres"],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            if "healthy" in result.stdout:
                _ok("PostgreSQL is ready")
                return

        _warn("PostgreSQL did not become healthy within 30s")

    except Exception as exc:
        _warn(f"PostgreSQL startup failed: {exc}")
        print("    Start it manually: docker compose up -d postgres")


# ---------------------------------------------------------------------------
# Step 8: Validation
# ---------------------------------------------------------------------------


def validate_setup():
    _step("Validating setup")

    errors = []

    server_exe = BIN_DIR / "llama-server.exe"
    if not server_exe.exists():
        errors.append(f"llama-server.exe missing at {server_exe}")

    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        env_text = env_file.read_text(encoding="utf-8")
        for line in env_text.split("\n"):
            line = line.strip()
            if line.startswith("LLAMA_SERVER_MODEL=") and not line.startswith("#"):
                p = line.split("=", 1)[1].strip()
                if not Path(p).exists():
                    errors.append(f"GGUF model missing: {p}")
                else:
                    gb = Path(p).stat().st_size / (1024**3)
                    print(f"    Model:  {Path(p).name} ({gb:.1f} GB)")
            if line.startswith("LLAMA_SERVER_MMPROJ=") and not line.startswith("#"):
                p = line.split("=", 1)[1].strip()
                if not Path(p).exists():
                    errors.append(f"mmproj missing: {p}")
    else:
        errors.append("backend/.env not found")

    if errors:
        for e in errors:
            print(f"    [MISSING] {e}")
        _fail(f"Found {len(errors)} issues. Re-run setup or fix manually.")
    else:
        _ok("All assets present and accounted for")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    _banner()

    check_prerequisites()
    setup_venv()
    install_dependencies()
    download_llama_cpp_binaries()
    gguf_path, mmproj_path = download_models()
    setup_env_files(gguf_path, mmproj_path)
    start_postgres()
    validate_setup()

    print()
    print("=" * 52)
    print("  Setup complete! Start the services:")
    print()
    print("  Terminal 1 - Backend:")
    print(f"    {_python_exe()} -m uvicorn app.main:app --reload --port 8003")
    print()
    print("  Terminal 2 - Frontend:")
    print("    cd frontend")
    print("    npm install")
    print("    npm run dev")
    print()
    print("  Then open http://localhost:3000")
    print("=" * 52)
    print()


if __name__ == "__main__":
    main()
