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
    autostart: bool = os.getenv("LLAMA_SERVER_AUTOSTART", "true").lower() == "true"
    host: str = os.getenv("LLAMA_SERVER_HOST", "127.0.0.1")
    port: int = int(os.getenv("LLAMA_SERVER_PORT", "8085"))
    binary_path: Path = _path_from_env(
        "LLAMA_SERVER_BINARY",
        _repo_backend_dir() / "llama_cpp" / "bin" / "llama-server.exe",
    )
    model_path: Path = _path_from_env(
        "LLAMA_SERVER_MODEL",
        _repo_backend_dir() / "llama_cpp" / "models" / "gemma-4.gguf",
    )
    mmproj_path: Optional[Path] = (
        _path_from_env(
            "LLAMA_SERVER_MMPROJ",
            _repo_backend_dir() / "llama_cpp" / "models" / "mmproj.gguf",
        )
        if os.getenv("LLAMA_SERVER_MMPROJ")
        else _repo_backend_dir() / "llama_cpp" / "models" / "mmproj.gguf"
    )
    model_repo: str = os.getenv(
        "LLAMA_SERVER_MODEL_REPO",
        "unsloth/gemma-4-E2B-it-GGUF",
    )
    model_filename: str = os.getenv(
        "LLAMA_SERVER_MODEL_FILENAME",
        "gemma-4-E2B-it-Q4_K_M.gguf",
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
        "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf?download=true",
    )
    mmproj_url: str = os.getenv(
        "LLAMA_SERVER_MMPROJ_URL",
        "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-BF16.gguf?download=true",
    )
    download_models: bool = os.getenv("LLAMA_SERVER_DOWNLOAD_MODELS", "true").lower() == "true"
    chat_template_path: Path = _path_from_env(
        "LLAMA_SERVER_CHAT_TEMPLATE",
        _repo_backend_dir() / "llama_templates" / "gemma4_no_think.jinja",
    )
    ctx_size: int = int(os.getenv("LLAMA_SERVER_CTX_SIZE", "4096"))
    threads: int = int(os.getenv("LLAMA_SERVER_THREADS", "8"))
    batch_size: int = int(os.getenv("LLAMA_SERVER_BATCH_SIZE", "2048"))
    ubatch_size: int = int(os.getenv("LLAMA_SERVER_UBATCH_SIZE", "256"))
    flash_attn: bool = os.getenv("LLAMA_SERVER_FLASH_ATTN", "true").lower() == "true"
    numa: bool = os.getenv("LLAMA_SERVER_NUMA", "true").lower() == "true"
    n_gpu_layers: int = int(os.getenv("LLAMA_SERVER_N_GPU_LAYERS", "0"))
    extra_args: str = os.getenv("LLAMA_SERVER_EXTRA_ARGS", "")


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

        # Warn if GPU offload requested but no Vulkan DLLs present
        if self.config.n_gpu_layers > 0:
            has_vulkan = any(
                (self.config.binary_path.parent / f).exists()
                for f in ("ggml-vulkan.dll", "ggml-vk.dll")
            )
            if has_vulkan:
                logger.info("Vulkan GPU offload: %d layers", self.config.n_gpu_layers)
            else:
                logger.warning(
                    "LLAMA_SERVER_N_GPU_LAYERS=%d but no Vulkan DLLs found — running CPU only. "
                    "Download Vulkan build for GPU offload: "
                    "https://github.com/ggml-org/llama.cpp/releases",
                    self.config.n_gpu_layers,
                )

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
            self.config.chat_template_path,
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

    def _ensure_model_files(self):
        if not self.config.download_models:
            return

        self._download_model(
            target=self.config.model_path,
            repo_id=self.config.model_repo,
            filename=self.config.model_filename,
            fallback_url=self.config.model_url,
            label="Gemma 4 model",
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
                resume=True,
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
        command = [
            str(self.config.binary_path),
            "-m",
            str(self.config.model_path),
        ]
        if self.config.mmproj_path:
            command.extend(["--mmproj", str(self.config.mmproj_path)])

        command.extend(
            [
                "--chat-template-file",
                str(self.config.chat_template_path),
                "--reasoning",
                "off",
                "--reasoning-budget",
                "0",
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
