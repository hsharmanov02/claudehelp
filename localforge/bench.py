"""LocalForge.exe --bench model.gguf [...]: load speed, prompt speed, generation speed and GPU offload per model.
Results go to the console and to bench.txt next to LocalForge.exe."""
import time
from pathlib import Path

from . import config, hardware
from .engine import Engine

PROMPT = ("Write a Python function that returns the n-th Fibonacci number iteratively, with a docstring "
          "and three example calls. Then explain its time complexity in two sentences.")
LONG = "Summarise this in one sentence: " + ("LocalForge runs AI models on your own PC without the internet. " * 120)


def _vram_used() -> str:
    out = hardware._run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"])
    return out.strip().splitlines()[0] if out.strip() else "?"


def run(models: list[str], n_ctx: int) -> int:
    pc = hardware.scan()
    lines = [f"PC: {pc.cpu}, RAM {pc.totalRam / 2**30:.0f} GB, GPU {pc.best_gpu.name if pc.best_gpu else 'none'} "
             f"{(pc.vram / 2**30):.0f} GB, context {n_ctx}"]
    print(lines[0], flush=True)
    for m in models:
        p = Path(m)
        name = p.name
        eng = Engine()
        try:
            t0 = time.time()
            eng.load(str(p), pc, n_ctx, 0, -1, "auto")
            load_s = time.time() - t0
            vram = _vram_used()
            eng.chat([{"role": "user", "content": LONG}], 32, 0.2, lambda x: None)
            pp = eng.last_prompt_tps
            gen = []
            for _ in range(2):
                eng.chat([{"role": "user", "content": PROMPT}], 300, 0.2, lambda x: None)
                gen.append(eng.last_tps)
            line = (f"{name}: {p.stat().st_size / 2**30:.1f} GB | {eng.backend} | load {load_s:.0f}s | "
                    f"prompt {pp:.0f} tok/s | generate {max(gen):.1f} tok/s | VRAM {vram} | {eng.gpu_summary()}")
        except Exception as e:
            line = f"{name}: FAILED {e} | " + " / ".join(l for l in eng.log[-6:])
        finally:
            eng.unload()
        print(line, flush=True)
        lines.append(line)
    try:
        (config.app_dir() / "bench.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass
    return 0
