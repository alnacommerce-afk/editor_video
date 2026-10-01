"""Teste objetivo de sincronia: um vídeo com flash branco + clique no MESMO instante, a cada 1 s.

Passa pelo render (cortes + aceleração) e mede, no MP4 final, a distância entre cada flash e cada clique.

    python testes/testar_sincronia.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editor import config, render, video_input  # noqa: E402
from editor.ferramentas import binario  # noqa: E402

FPS = 30
TOLERANCIA_MS = 45   # limite em que o atraso entre boca e voz começa a ser percebido (ITU-R BT.1359)


def ffmpeg(*args, saida=False):
    r = subprocess.run([binario("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, check=True)
    return r.stdout if saida else None


def eventos_video(arquivo: Path) -> list[float]:
    bruto = ffmpeg("-i", str(arquivo), "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-", saida=True)
    medias = np.frombuffer(bruto, np.uint8).reshape(-1, 32 * 32).mean(axis=1)
    claros = np.flatnonzero(medias > 128)
    return [k / FPS for i, k in enumerate(claros) if i == 0 or k - claros[i - 1] > 1]


def eventos_audio(arquivo: Path) -> list[float]:
    bruto = ffmpeg("-i", str(arquivo), "-vn", "-ac", "1", "-ar", "48000", "-f", "s16le", "-", saida=True)
    a = np.abs(np.frombuffer(bruto, np.int16).astype(np.float32))
    altos = np.flatnonzero(a > a.max() * 0.3)
    return [k / 48000 for i, k in enumerate(altos) if i == 0 or k - altos[i - 1] > 4800]


def main() -> int:
    cfg = config.carregar()
    falhas = 0
    with tempfile.TemporaryDirectory() as t:
        pasta = Path(t)
        fonte = pasta / "sincronia.mp4"
        # Flash de 2 frames e clique de 10 ms começando no mesmo instante: t = k + 0.5 s.
        ffmpeg("-f", "lavfi", "-i", f"color=c=black:s=320x568:r={FPS}:d=12",
               "-f", "lavfi", "-i", "aevalsrc='0.8*sin(2*PI*1000*t)*between(mod(t,1),0.5,0.51)':s=48000:d=12",
               "-vf", f"drawbox=c=white:t=fill:enable='between(mod(t,1),0.5,0.5+1.9/{FPS})'",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(fonte))
        info = video_input.analisar(fonte)
        segmentos = [(0.2, 3.3), (4.1, 7.6), (8.0, 11.9)]
        for fator in (1.0, 1.1, 1.2):
            dur = render.duracoes_saida(segmentos, render.fps_saida(info, cfg), fator)
            trechos = render.renderizar_trechos(info, segmentos, dur, fator, ["scale=320:568,setsar=1"] * 3, cfg,
                                                pasta / f"trechos_{fator}")
            saida = pasta / f"saida_{fator}.mp4"
            render.finalizar(trechos, saida, sum(dur), cfg, pasta)
            flashes, cliques = eventos_video(saida), eventos_audio(saida)
            desvios = [min((c - f for c in cliques), key=abs) * 1000 for f in flashes]
            pior = max(desvios, key=abs)
            ok = len(flashes) >= 8 and abs(pior) <= TOLERANCIA_MS
            falhas += not ok
            print(f"[{'OK' if ok else 'FALHOU'}] velocidade {fator}×: {len(flashes)} flashes, desvio áudio-vídeo "
                  f"de {min(desvios):+.0f} a {max(desvios):+.0f} ms (pior {pior:+.0f} ms, limite ±{TOLERANCIA_MS})")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
