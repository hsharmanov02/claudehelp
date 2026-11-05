"""LocalForge remote engine on Modal: your own uncensored (abliterated) GGUF model, served by llama.cpp on a
Modal GPU, for the LocalForge and LocalForge Autopilot phone apps.

- The model runs in your own Modal account. Nothing is sent to an AI company's API.
- Every chat request needs your key (LOCALFORGE_KEY), so a stranger who finds the address can't use it.
- It scales to zero: Modal only bills while a container is up (while answering, plus SCALEDOWN seconds after).

Deploy (the "Deploy Modal engine" GitHub workflow runs these for you):
    modal secret create localforge-key LOCALFORGE_KEY=<your password> --force
    modal run remote/modal_engine.py::download
    modal deploy remote/modal_engine.py

Note: the "Deploy Modal engine" workflow keeps the address out of the (public) logs, since any request, even one
without the key, wakes the GPU.

Settings (environment variables when deploying):
    LF_GPU        L4 (24 GB, default), T4 (16 GB, cheapest), A10, L40S (48 GB), A100-80GB
    LF_MODEL_REPO Hugging Face repo with GGUF files (must be abliterated/uncensored)
    LF_QUANT      Q4_K_M (default)
    LF_CTX        context size in tokens (default 16384)
    LF_SCALEDOWN  seconds a container stays up after the last request (default 120)
"""
import os
import subprocess

import modal

GPU = os.environ.get("LF_GPU", "L4")
MODEL_REPO = os.environ.get("LF_MODEL_REPO", "bartowski/huihui-ai_Qwen3-14B-abliterated-GGUF")
QUANT = os.environ.get("LF_QUANT", "Q4_K_M")
CTX = int(os.environ.get("LF_CTX", "16384"))
SCALEDOWN = int(os.environ.get("LF_SCALEDOWN", "120"))
PORT = 8080
MODELS = "/models"

UNCENSORED = ("abliterat", "uncensored", "dolphin", "lexi")
if not any(m in MODEL_REPO.lower() for m in UNCENSORED):
    raise SystemExit(f"{MODEL_REPO}: LocalForge only runs uncensored / abliterated models.")

app = modal.App("localforge-engine")
volume = modal.Volume.from_name("localforge-models", create_if_missing=True)

# llama.cpp's official CUDA server build, plus Python for Modal.
image = (
    modal.Image.from_registry("ghcr.io/ggml-org/llama.cpp:server-cuda", add_python="3.12")
    .entrypoint([])
    .pip_install("huggingface_hub[hf_transfer]>=0.26")
    .env({
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "LF_GPU": GPU, "LF_MODEL_REPO": MODEL_REPO, "LF_QUANT": QUANT,
        "LF_CTX": str(CTX), "LF_SCALEDOWN": str(SCALEDOWN),
    })
)


def pick_file(files: list[str], quant: str) -> str:
    """The single-file GGUF for [quant] (shortest name wins, e.g. no -imatrix variants)."""
    ggufs = [f for f in files if f.lower().endswith(".gguf") and "mmproj" not in f.lower() and "-of-0" not in f.lower()]
    picked = [f for f in ggufs if quant.lower() in f.lower()] or ggufs
    if not picked:
        raise SystemExit("No GGUF files found in the repo.")
    return sorted(picked, key=len)[0]


def model_path() -> str:
    name_file = os.path.join(MODELS, "current.txt")
    with open(name_file) as f:
        return os.path.join(MODELS, f.read().strip())


@app.function(image=image, volumes={MODELS: volume}, timeout=3600, cpu=2)
def download():
    """Puts the model file in the Modal volume (runs on a cheap CPU container)."""
    from huggingface_hub import hf_hub_download, list_repo_files

    name = pick_file(list_repo_files(MODEL_REPO), QUANT)
    print(f"Downloading {MODEL_REPO}/{name} …")
    hf_hub_download(MODEL_REPO, name, local_dir=MODELS)
    with open(os.path.join(MODELS, "current.txt"), "w") as f:
        f.write(name)
    volume.commit()
    print(f"Ready: {name} ({os.path.getsize(os.path.join(MODELS, name)) / 1e9:.1f} GB)")


def server_args(model: str, key: str, ngl: int = 999) -> list[str]:
    """llama-server flags. tests/test_remote_engine.yml runs the same flags on a CPU machine."""
    return [
        "-m", model, "-c", str(CTX), "-ngl", str(ngl),
        "--host", "0.0.0.0", "--port", str(PORT),
        "--api-key", key,
        "--jinja", "--reasoning-format", "none",
        "--alias", os.path.basename(model).removesuffix(".gguf"),
        "--parallel", "1",
    ]


@app.function(
    image=image,
    gpu=GPU,
    volumes={MODELS: volume},
    secrets=[modal.Secret.from_name("localforge-key")],
    scaledown_window=SCALEDOWN,
    max_containers=1,          # never more than one GPU at a time
    timeout=60 * 60,
)
@modal.concurrent(max_inputs=4)
@modal.web_server(port=PORT, startup_timeout=600)
def serve():
    key = os.environ["LOCALFORGE_KEY"]
    subprocess.Popen(["/app/llama-server", *server_args(model_path(), key)])
