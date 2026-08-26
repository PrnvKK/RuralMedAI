"""Parchee Edge - Zero-to-Running Setup Script.

Configures the environment and prepares everything needed to run
Parchee Edge locally with the Gemini API.

Usage:
    python scripts/setup.py

What it does:
    1. Checks prerequisites (Python, Git, Docker)
    2. Creates a Python virtual environment
    3. Installs all dependencies
    4. Configures .env files (Gemini API key + generated AES key)
    5. Starts PostgreSQL via Docker
    6. Validates the Gemini API key against the live API
"""

import base64
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
VENV_DIR = REPO_ROOT / ".venv"

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
    print("  Medical scribe & claim assistant (Gemini API)")
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


def _read_existing_gemini_key() -> str:
    for env_path in (REPO_ROOT / ".env", BACKEND_DIR / ".env"):
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key and "replace_with" not in key:
                    return key
    return ""


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
# Step 4: Configure environment files
# ---------------------------------------------------------------------------


def setup_env_files() -> str:
    _step("Configuring environment files")

    api_key = _read_existing_gemini_key()
    if not api_key:
        print()
        print("    A Gemini API key is required (get one at https://aistudio.google.com/apikey).")
        api_key = input("    Enter GEMINI_API_KEY: ").strip()
        if not api_key:
            _fail("A Gemini API key is required to run Parchee Edge.")

    aes_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    print("    Generated AES-256 key")

    for env_example, target in (
        (REPO_ROOT / ".env.example", REPO_ROOT / ".env"),
        (BACKEND_DIR / ".env.example", BACKEND_DIR / ".env"),
    ):
        if not env_example.exists():
            _warn(f"{env_example.name} not found - skipping {target}")
            continue

        if target.exists():
            _update_env_value(target, "GEMINI_API_KEY", api_key)
            if not _env_has_real_value(target, "AES_256_KEY"):
                _update_env_value(target, "AES_256_KEY", aes_key)
            print(f"    Updated {target}")
        else:
            env_text = env_example.read_text(encoding="utf-8")
            env_text = env_text.replace("replace_with_your_gemini_api_key", api_key)
            env_text = env_text.replace("replace_with_base64_32byte_key", aes_key)
            target.write_text(env_text, encoding="utf-8")
            print(f"    Created {target}")

    _ok()
    return api_key


def _update_env_value(path: Path, key: str, value: str):
    lines = path.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
    if not updated:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _env_has_real_value(path: Path, key: str) -> bool:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].strip()
            return bool(value) and "replace_with" not in value
    return False


# ---------------------------------------------------------------------------
# Step 5: Start PostgreSQL via Docker
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
# Step 6: Validation
# ---------------------------------------------------------------------------


def validate_setup():
    _step("Validating Gemini API access")

    check_script = (
        "import sys; sys.path.insert(0, 'backend');\n"
        "from app.services.gemini_client import GeminiClient;\n"
        "client = GeminiClient();\n"
        "reply = client.generate_text(prompt='Reply with exactly: OK', model='gemini-3.5-flash',"
        " max_output_tokens=512, thinking_level='low');\n"
        "print('REPLY:' + reply)\n"
    )

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [_python_exe(), "-c", check_script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )

    if result.returncode != 0:
        _fail(f"Gemini API check failed:\n{result.stderr.strip()[:500]}")

    reply = next((line for line in result.stdout.splitlines() if line.startswith("REPLY:")), "")
    if "OK" not in reply:
        _warn(f"Unexpected reply from Gemini API: {reply!r}")

    _ok("Gemini API key is valid")

    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        _fail("backend/.env not found")
    if not _env_has_real_value(env_file, "GEMINI_API_KEY"):
        _fail("GEMINI_API_KEY missing from backend/.env")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    _banner()

    check_prerequisites()
    setup_venv()
    install_dependencies()
    setup_env_files()
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
