import asyncio
import logging
import os
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.services.hardware_profiles import detect_profile

logger = logging.getLogger(__name__)


def _repo_backend_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw) if raw else default
    if path.is_absolute():
        return path
    return _repo_backend_dir() / path


@dataclass
class LlamaServerConfig:
    hardware_profile: str = os.getenv("PARCHEE_HARDWARE_PROFILE", "auto")
    autostart: bool = os.getenv("LLAMA_SERVER_AUTOSTART", "true").lower() == "true"
    host: str = os.getenv("LLAMA_SERVER_HOST", "127.0.0.1")
    port: int = int(os.getenv("LLAMA_SERVER_PORT", "8085"))
    binary_path: Optional[Path] = (
        _path_from_env("LLAMA_SERVER_BINARY", Path("")) if os.getenv("LLAMA_SERVER_BINARY") else None
    )
    model_path: Path = _path_from_env(
        "LLAMA_SERVER_MODEL",
        _repo_backend_dir() / "llama_cpp" / "models" / "gemma-3-4b-it-Q4_K_M.gguf",
    )
    # The live scribe is text-only after Whisper ASR; a projector is unnecessary.
    mmproj_path: Optional[Path] = (
        _path_from_env("LLAMA_SERVER_MMPROJ", Path("")) if os.getenv("LLAMA_SERVER_MMPROJ") else None
    )
    model_repo: str = os.getenv(
        "LLAMA_SERVER_MODEL_REPO",
        "unsloth/gemma-3-4b-it-GGUF",
    )
    model_filename: str = os.getenv(
        "LLAMA_SERVER_MODEL_FILENAME",
        "gemma-3-4b-it-Q4_K_M.gguf",
    )
    mmproj_repo: str = os.getenv(
        "LLAMA_SERVER_MMPROJ_REPO",
        "unsloth/gemma-4-E2B-it-GGUF",
    )
    mmproj_filename: str = os.getenv(
        "LLAMA_SERVER_MMPROJ_FILENAME",
        "mmproj-BF16.gguf",
    )
    model_url: str = os.getenv(
        "LLAMA_SERVER_MODEL_URL",
        "https://huggingface.co/unsloth/gemma-3-4b-it-GGUF/resolve/main/gemma-3-4b-it-Q4_K_M.gguf?download=true",
    )
    mmproj_url: str = os.getenv(
        "LLAMA_SERVER_MMPROJ_URL",
        "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-BF16.gguf?download=true",
    )
    download_models: bool = os.getenv("LLAMA_SERVER_DOWNLOAD_MODELS", "true").lower() == "true"
    chat_template_path: Optional[Path] = (
        _path_from_env("LLAMA_SERVER_CHAT_TEMPLATE", Path("")) if os.getenv("LLAMA_SERVER_CHAT_TEMPLATE") else None
    )
    ctx_size: int = int(os.getenv("LLAMA_SERVER_CTX_SIZE", "4096"))
    threads: int = int(os.getenv("LLAMA_SERVER_THREADS", "8"))
    batch_size: int = int(os.getenv("LLAMA_SERVER_BATCH_SIZE", "2048"))
    ubatch_size: int = int(os.getenv("LLAMA_SERVER_UBATCH_SIZE", "256"))
    flash_attn: bool = os.getenv("LLAMA_SERVER_FLASH_ATTN", "true").lower() == "true"
    numa: bool = os.getenv("LLAMA_SERVER_NUMA", "true").lower() == "true"
    n_gpu_layers: Optional[int] = (
        int(os.getenv("LLAMA_SERVER_N_GPU_LAYERS")) if os.getenv("LLAMA_SERVER_N_GPU_LAYERS") else None
    )
    extra_args: str = os.getenv("LLAMA_SERVER_EXTRA_ARGS", "")
    memory_budget_gb: Optional[float] = (
        float(os.getenv("LLAMA_SERVER_MEMORY_BUDGET_GB")) if os.getenv("LLAMA_SERVER_MEMORY_BUDGET_GB") else None
    )

    def __post_init__(self):
        # Preserve a legacy/custom explicit binary instead of auto-detecting a
        # different accelerator and creating a runtime mismatch.
        requested = self.hardware_profile
        if requested == "auto" and self.binary_path is not None:
            names = {path.name.lower() for path in self.binary_path.parent.glob("*.dll")}
            requested = (
                "vulkan"
                if any("vulkan" in name or "-vk" in name for name in names)
                else "cuda"
                if any("cuda" in name for name in names)
                else "cpu"
            )
        profile = detect_profile(requested)
        self.hardware_profile = profile.name
        if self.binary_path is None:
            runtime = _repo_backend_dir() / "llama_cpp" / "runtimes" / profile.name / "llama-server.exe"
            legacy = _repo_backend_dir() / "llama_cpp" / "bin" / "llama-server.exe"
            self.binary_path = runtime if runtime.exists() or not legacy.exists() else legacy
        if self.n_gpu_layers is None:
            self.n_gpu_layers = profile.gpu_layers


class LlamaServerManager:
    def __init__(self, config: Optional[LlamaServerConfig] = None):
        self.config = config or LlamaServerConfig()
        self.process: Optional[subprocess.Popen] = None
        self.output_tail: list[str] = []

    async def start(self):
        if not self.config.autostart:
            logger.info("llama-server autostart disabled")
            return

        if _port_open(self.config.host, self.config.port):
            logger.info(
                "llama-server already reachable at http://%s:%s",
                self.config.host,
                self.config.port,
            )
            return

        await asyncio.to_thread(self._ensure_model_files)
        self._validate_files()
        command = self._build_command()
        logger.info("Starting llama-server: %s", " ".join(str(part) for part in command))

        # Fail early on a profile/runtime mismatch instead of silently using CPU.
        if self.config.n_gpu_layers > 0:
            expected_dll = {"vulkan": ("ggml-vulkan.dll", "ggml-vk.dll"), "cuda": ("ggml-cuda.dll",)}.get(
                self.config.hardware_profile
            )
            if expected_dll and not any((self.config.binary_path.parent / name).exists() for name in expected_dll):
                raise RuntimeError(
                    f"{self.config.hardware_profile} profile selected but its runtime DLLs are missing beside "
                    f"{self.config.binary_path}. Run `python scripts/setup.py "
                    f"--profile {self.config.hardware_profile}` "
                    "or use PARCHEE_HARDWARE_PROFILE=custom with a matching LLAMA_SERVER_BINARY."
                )
            logger.info("%s GPU offload: %d layers", self.config.hardware_profile, self.config.n_gpu_layers)

        self.process = subprocess.Popen(
            command,
            cwd=str(self.config.binary_path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=self._build_env(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        asyncio.create_task(self._log_output())
        await self._wait_until_ready()

    async def stop(self):
        if not self.process or self.process.poll() is not None:
            return

        logger.info("Stopping managed llama-server")
        self.process.terminate()
        try:
            await asyncio.to_thread(self.process.wait, 10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            await asyncio.to_thread(self.process.wait)

    def _validate_files(self):
        required = [
            self.config.binary_path,
            self.config.model_path,
        ]
        if self.config.mmproj_path:
            required.append(self.config.mmproj_path)

        missing = [str(path) for path in required if path and not path.exists()]
        if missing:
            msg = (
                "Missing llama.cpp runtime files:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\n\nRun the setup script to download missing assets:"
                + "\n  python scripts/setup.py"
            )
            raise FileNotFoundError(msg)
        if self.config.ctx_size < 1024:
            raise ValueError("LLAMA_SERVER_CTX_SIZE must be at least 1024 tokens")
        if self.config.model_path.suffix.lower() != ".gguf":
            raise ValueError(f"LLAMA_SERVER_MODEL must be a GGUF file: {self.config.model_path}")
        model_gb = self.config.model_path.stat().st_size / (1024**3)
        if self.config.memory_budget_gb is not None and model_gb > self.config.memory_budget_gb:
            raise ValueError(
                f"Model is {model_gb:.1f} GB but LLAMA_SERVER_MEMORY_BUDGET_GB is "
                f"{self.config.memory_budget_gb:.1f} GB. Choose a smaller quantization or increase the budget."
            )
        logger.info(
            "GGUF preflight: %s (%.1f GB), context %d, profile %s",
            self.config.model_path.name,
            model_gb,
            self.config.ctx_size,
            self.config.hardware_profile,
        )

    def _ensure_model_files(self):
        if not self.config.download_models:
            return

        self._download_model(
            target=self.config.model_path,
            repo_id=self.config.model_repo,
            filename=self.config.model_filename,
            fallback_url=self.config.model_url,
            label="Gemma 3 4B model",
        )
        if self.config.mmproj_path:
            self._download_model(
                target=self.config.mmproj_path,
                repo_id=self.config.mmproj_repo,
                filename=self.config.mmproj_filename,
                fallback_url=self.config.mmproj_url,
                label="Gemma 4 mmproj",
            )

    def _download_model(
        self,
        target: Path,
        repo_id: str,
        filename: str,
        fallback_url: str,
        label: str,
    ):
        if target.exists() and target.stat().st_size > 0:
            logger.info("%s already present at %s", label, target)
            return

        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            from huggingface_hub import hf_hub_download

            logger.info("Downloading %s from HuggingFace Hub (%s/%s)", label, repo_id, filename)
            cached = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
            )
            shutil.copy2(cached, target)
            logger.info("%s cached at %s, copied to %s", label, cached, target)
        except ImportError:
            logger.info("huggingface_hub not installed, falling back to URL download for %s", label)
            self._download_via_url(target, fallback_url, label)
        except Exception as exc:
            logger.warning("HF hub download failed for %s: %s. Trying URL fallback.", label, exc)
            self._download_via_url(target, fallback_url, label)

    def _download_via_url(self, target: Path, url: str, label: str):
        if not url:
            return

        tmp_target = target.with_suffix(target.suffix + ".download")
        if tmp_target.exists():
            tmp_target.unlink()

        logger.info("Downloading %s from %s to %s", label, url, target)
        with urllib.request.urlopen(url, timeout=30) as response:
            total = int(response.headers.get("Content-Length", "0") or "0")
            downloaded = 0
            next_log = 0
            with open(tmp_target, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if total and downloaded >= next_log:
                        logger.info(
                            "%s download %.1f%%",
                            label,
                            downloaded / total * 100,
                        )
                        next_log += max(total // 20, 1)

        if tmp_target.stat().st_size == 0:
            tmp_target.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded {label} was empty")

        tmp_target.replace(target)
        logger.info("Finished downloading %s", label)

    def _build_command(self):
        assert self.config.binary_path is not None
        command = [
            str(self.config.binary_path),
            "-m",
            str(self.config.model_path),
        ]
        command.extend(
            [
                "--ctx-size",
                str(self.config.ctx_size),
                "-t",
                str(self.config.threads),
                "--host",
                self.config.host,
                "--port",
                str(self.config.port),
                "--batch-size",
                str(self.config.batch_size),
                "--ubatch-size",
                str(self.config.ubatch_size),
                "--no-webui",
            ]
        )
        if self.config.flash_attn:
            command.extend(["--flash-attn", "on"])
        if self.config.numa:
            command.extend(["--numa", "distribute"])
        if self.config.n_gpu_layers > 0:
            command.extend(["--n-gpu-layers", str(self.config.n_gpu_layers)])
        if self.config.extra_args.strip():
            command.extend(self.config.extra_args.split())
        return command

    def _build_env(self):
        env = os.environ.copy()
        bin_dir = str(self.config.binary_path.parent)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        env.setdefault("CUDA_MODULE_LOADING", "LAZY")
        return env

    async def _wait_until_ready(self):
        for _ in range(180):
            if self.process and self.process.poll() is not None:
                tail = "\n".join(self.output_tail[-30:])
                raise RuntimeError(
                    "llama-server exited before becoming ready.\n"
                    f"Exit code: {self.process.returncode}\n"
                    f"Last llama-server output:\n{tail}"
                )
            if _port_open(self.config.host, self.config.port):
                logger.info("llama-server is ready")
                return
            await asyncio.sleep(1)

        raise TimeoutError("Timed out waiting for llama-server to start")

    async def _log_output(self):
        if not self.process or not self.process.stdout:
            return

        while True:
            line = await asyncio.to_thread(self.process.stdout.readline)
            if not line:
                return
            clean_line = line.rstrip()
            self.output_tail.append(clean_line)
            self.output_tail = self.output_tail[-50:]
            logger.info("[llama-server] %s", clean_line)


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, port)) == 0
