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
    5. Downloads the Gemma 3 4B GGUF and Whisper base.en ASR model
    6. Creates .env files with generated AES key
    7. Starts PostgreSQL via Docker
    8. Validates the setup
"""

import argparse
import ctypes
import os
import sys
import json
import re
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
RUNTIME_ROOT = BACKEND_DIR / "llama_cpp" / "runtimes"
VENV_DIR = REPO_ROOT / ".venv"

HF_MODEL_REPO = os.getenv("LLAMA_SERVER_MODEL_REPO", "unsloth/gemma-3-4b-it-GGUF")
HF_GGUF_FILE = os.getenv("LLAMA_SERVER_MODEL_FILENAME", "gemma-3-4b-it-Q4_K_M.gguf")
WHISPER_RELEASE = "b4938"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin?download=true"
WHISPER_DIR = BACKEND_DIR / "whisper_cpp"

LLAMA_CPP_RELEASE_TAG = "b10618"
PYTHON_MIN_VERSION = (3, 11)


def resolve_profile(requested: str) -> str:
    """Choose a supported runtime without requiring a Python GPU package."""
    if requested != "auto":
        return requested
    if shutil.which("nvidia-smi"):
        return "cuda"
    try:
        ctypes.WinDLL("vulkan-1.dll")
        return "vulkan"
    except OSError:
        return "cpu"


def runtime_dir(profile: str) -> Path:
    return RUNTIME_ROOT / profile

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


def _running_from_venv() -> bool:
    """True if this interpreter is the project's own venv python."""
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except Exception:
        return False


def _on_rmtree_error(func, path, exc_info):
    """Give a clear hint when Windows can't delete a locked venv file."""
    print(f"    [WARN] Could not delete {path}: {exc_info[1]}")
    print("    Hint: close any terminals/PIDs using the venv (e.g. deactivate it),")
    print("    close your editor/IDE, then re-run this script with system Python.")


def setup_venv():
    _step("Setting up Python virtual environment")

    if _running_from_venv():
        _fail(
            "This script is running from inside the .venv it wants to replace. "
            "Run 'deactivate' (or open a new terminal) and re-run with system Python:\n"
            "    python scripts/setup.py"
        )

    if VENV_DIR.exists():
        print("    Virtual environment already exists, recreating...")
        try:
            shutil.rmtree(VENV_DIR, onerror=_on_rmtree_error)
        except PermissionError:
            _fail(
                "Could not remove the existing .venv because files are locked. "
                "Close any terminals/editors using it, then re-run."
            )

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
    _install_en_core_sci_md()

    _ok()


def _install_scispacy():
    """Install scispacy. Uses --no-deps if the resolver rejects its spaCy pin."""
    try:
        _pip_install("scispacy>=0.5.4")
    except subprocess.CalledProcessError:
        print("    scispacy's spaCy pin conflicts; installing with --no-deps...")
        _pip_install("scispacy>=0.5.4", "--no-deps")
        print("    scispacy installed (using already-installed spaCy)")


def _install_en_core_sci_md():
    """Install the scispacy clinical model.

    Installed with --no-deps: the model's metadata pins ``spacy<3.8``, which
    would make pip downgrade spaCy (and attempt to build an old spaCy that
    cannot compile on Python 3.14). The model is pure data + code and needs no
    extra deps beyond the already-installed spaCy.
    """
    url = (
        "https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/"
        "releases/v0.5.4/en_core_sci_md-0.5.4.tar.gz"
    )
    try:
        _pip_install(url, "--no-deps")
    except subprocess.CalledProcessError as exc:
        _warn(f"en_core_sci_md install failed ({exc}).")
        print("    It can be retried later with:")
        print(f"      {_python_exe()} -m pip install --no-deps \"{url}\"")
        return
    _fix_en_core_sci_md_config()


def _fix_en_core_sci_md_config():
    """Patch string booleans in en_core_sci_md's config.cfg.

    The 0.5.4 model ships ``include_static_vectors = "True"`` and
    ``normalize = "False"`` (strings). spaCy 3.8 requires real booleans and
    refuses to load the model otherwise.
    """
    cfg = (
        VENV_DIR
        / "Lib/site-packages/en_core_sci_md/en_core_sci_md-0.5.4/config.cfg"
    )
    if not cfg.exists():
        _warn(f"en_core_sci_md config.cfg not found at {cfg}")
        return

    text = cfg.read_text(encoding="utf-8")
    before = text
    text = text.replace('normalize = "False"', "normalize = false")
    text = text.replace('include_static_vectors = "True"', "include_static_vectors = true")
    if text != before:
        cfg.write_text(text, encoding="utf-8")
        print("    Patched en_core_sci_md config.cfg booleans for spaCy 3.8")


# ---------------------------------------------------------------------------
# Step 4: Download llama.cpp Windows binaries
# ---------------------------------------------------------------------------


def _extract_windows_archive(archive: str, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as zf:
        for member in zf.namelist():
            if member.endswith((".exe", ".dll")):
                with zf.open(member) as src, open(destination / Path(member).name, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def download_llama_cpp_binaries(profile: str):
    _step(f"Downloading llama.cpp {profile} runtime")
    destination = runtime_dir(profile)
    existing = list(destination.glob("llama-server*")) if destination.exists() else []
    if existing:
        print("    llama.cpp runtime already present, skipping")
        _ok()
        return destination / "llama-server.exe"

    tag = LLAMA_CPP_RELEASE_TAG
    variant = {"cpu": "cpu", "vulkan": "vulkan", "cuda": "cuda-12.4"}[profile]
    zip_name = f"llama-{tag}-bin-win-{variant}-x64.zip"
    zip_url = (
        f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{zip_name}"
    )

    print(f"    Latest release: {tag}")
    print(f"    Downloading {zip_name} (~18 MB)...")

    tmp_path = None
    try:
        tmp_path = _download_file(zip_url, f"Downloading llama.cpp {tag}")
        print()

        print(f"    Extracting to {destination}...")
        _extract_windows_archive(tmp_path, destination)

        os.unlink(tmp_path)

        # Official CUDA executables may need the matching redistributable DLLs.
        if profile == "cuda":
            cudart_name = f"cudart-llama-bin-win-{variant}-x64.zip"
            cudart_url = f"https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{cudart_name}"
            cudart_archive = _download_file(cudart_url, f"Downloading {cudart_name}")
            _extract_windows_archive(cudart_archive, destination)
            os.unlink(cudart_archive)

        server_exe = destination / "llama-server.exe"
        if not server_exe.exists():
            _fail("llama-server.exe not found after extraction")

        file_count = len(list(destination.glob("*")))
        print(f"    Extracted {file_count} files")

    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _fail(f"Binary download failed: {exc}")

    _ok()
    return destination / "llama-server.exe"


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
# Step 5: Download Gemma 3 model via huggingface_hub
# ---------------------------------------------------------------------------


def download_whisper_assets(profile: str):
    """Install a matching CPU/CUDA Whisper runtime and the compact English model."""
    whisper_profile = "cuda" if profile == "cuda" else "cpu"
    _step(f"Downloading whisper.cpp {whisper_profile} runtime")
    binary_dir = WHISPER_DIR / "runtimes" / whisper_profile
    binary = binary_dir / "whisper-cli.exe"
    model = WHISPER_DIR / "models" / "ggml-base.en.bin"
    binary_dir.mkdir(parents=True, exist_ok=True)
    if not binary.exists():
        asset = "whisper-cublas-12.4.0-bin-x64.zip" if whisper_profile == "cuda" else "whisper-bin-x64.zip"
        url = f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_RELEASE}/{asset}"
        archive = _download_file(url, "Downloading whisper.cpp")
        _extract_windows_archive(archive, binary_dir)
        os.unlink(archive)
    if not model.exists():
        model.parent.mkdir(parents=True, exist_ok=True)
        downloaded = _download_file(WHISPER_MODEL_URL, "Downloading Whisper base.en model")
        shutil.move(downloaded, model)
    _ok()
    return str(binary), str(model)


def download_models():
    _step(f"Downloading GGUF model ({HF_MODEL_REPO}/{HF_GGUF_FILE})")

    model_script_lines = [
        "import sys",
        "from huggingface_hub import hf_hub_download",
        "",
        "try:",
        '    print("    Downloading selected GGUF model...")',
        "    gguf = hf_hub_download(",
        f'        repo_id="{HF_MODEL_REPO}",',
        f'        filename="{HF_GGUF_FILE}",',
        # hf_hub_download resumes automatically since huggingface_hub 0.26+.
        "    )",
        '    print(f"    Cached: {gguf}")',
        "",
        '    print("RESULT")',
        "    print(gguf)",
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

    if result_idx is None or result_idx + 1 >= len(lines):
        _fail("Could not parse model download output")

    gguf_path = lines[result_idx + 1].strip()

    gguf_file = Path(gguf_path)

    if not gguf_file.exists():
        _fail(f"GGUF model not found at {gguf_path}")

    gguf_gb = gguf_file.stat().st_size / (1024**3)
    print(f"    Gemma 3 GGUF: {gguf_gb:.1f} GB")

    _ok()
    return gguf_path


# ---------------------------------------------------------------------------
# Step 6: Configure environment files
# ---------------------------------------------------------------------------


def _set_env_value(text: str, key: str, value: str) -> str:
    pattern = rf"(?m)^{key}=.*$"
    replacement = f"{key}={value}"
    return re.sub(pattern, replacement, text) if re.search(pattern, text) else text + f"\n{replacement}\n"


def setup_env_files(profile, llama_binary, gguf_path, whisper_binary, whisper_model):
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
        existed = backend_target.exists()
        env_text = backend_target.read_text(encoding="utf-8") if existed else backend_env.read_text(encoding="utf-8")

        aes_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        print("    Generated AES-256 key")

        if not existed:
            replacements = {
                "replace_with_base64_32byte_key": aes_key,
                "llama_cpp/models/gemma-3-4b-it-Q4_K_M.gguf": gguf_path,
            }
            for old, new in replacements.items():
                env_text = env_text.replace(old, new)
            env_text = env_text.replace("LLAMA_SERVER_DOWNLOAD_MODELS=true", "LLAMA_SERVER_DOWNLOAD_MODELS=false")
        elif "AES_256_KEY=replace_with_base64_32byte_key" in env_text:
            env_text = env_text.replace("AES_256_KEY=replace_with_base64_32byte_key", f"AES_256_KEY={aes_key}")

        # Runtime choices are managed by the selected setup profile. Existing GGUF
        # repository, filename, and model path are intentionally left untouched.
        for key, value in {
            "PARCHEE_HARDWARE_PROFILE": profile,
            "LLAMA_SERVER_BINARY": str(llama_binary),
            "LLAMA_SERVER_N_GPU_LAYERS": "0" if profile == "cpu" else "999",
            "WHISPER_CPP_BINARY": whisper_binary,
            "WHISPER_CPP_MODEL": whisper_model,
        }.items():
            env_text = _set_env_value(env_text, key, value)

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


def validate_setup(profile: str):
    _step("Validating setup")

    errors = []

    server_exe = runtime_dir(profile) / "llama-server.exe"
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
    parser = argparse.ArgumentParser(description="Set up Parchee Edge local runtimes.")
    parser.add_argument(
        "--profile",
        choices=("auto", "cpu", "vulkan", "cuda"),
        default=os.getenv("PARCHEE_HARDWARE_PROFILE", "auto"),
        help="Runtime backend: auto-detect (default), CPU, Vulkan, or NVIDIA CUDA.",
    )
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="Install/switch inference runtimes without recreating the venv or downloading the selected GGUF again.",
    )
    args = parser.parse_args()
    profile = resolve_profile(args.profile)
    _banner()
    print(f"  Hardware profile: {profile}")

    if args.runtime_only:
        llama_binary = download_llama_cpp_binaries(profile)
        whisper_binary, whisper_model = download_whisper_assets(profile)
        setup_env_files(profile, llama_binary, "", whisper_binary, whisper_model)
        validate_setup(profile)
        print("  Runtime switch complete. Restart the backend to apply it.")
        return

    check_prerequisites()
    setup_venv()
    install_dependencies()
    llama_binary = download_llama_cpp_binaries(profile)
    whisper_binary, whisper_model = download_whisper_assets(profile)
    gguf_path = download_models()
    setup_env_files(profile, llama_binary, gguf_path, whisper_binary, whisper_model)
    start_postgres()
    validate_setup(profile)

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
