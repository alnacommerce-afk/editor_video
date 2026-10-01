"""Tratamento de áudio seguro: passa-alta leve, redução de ruído moderada e loudness em duas passadas.

- loudnorm em duas passadas, modo linear: o volume do vídeo inteiro sobe/desce por igual, sem
  "bombear" (é o que evita mudanças bruscas de volume entre trechos).
- O pico verdadeiro fica abaixo de `pico_maximo` dBTP, então nada estoura.
- afftdn com intensidade baixa e rastreamento de ruído: tira chiado de fundo sem deixar a voz metálica.
"""
import json
import re
import subprocess
from functools import lru_cache

import numpy as np

from .ferramentas import binario


def fades(duracao: float, cfg: dict) -> str:
    """Micro fade de entrada/saída em cada trecho — elimina estalos nas emendas sem ser perceptível."""
    f = min(cfg["audio"]["fade_cortes_ms"] / 1000, duracao / 4)
    return f"afade=t=in:st=0:d={f:.3f},afade=t=out:st={max(duracao - f, 0):.3f}:d={f:.3f}"


def cadeia_base(cfg: dict) -> list[str]:
    a = cfg["audio"]
    filtros = []
    if a["passa_alta_hz"]:
        filtros.append(f"highpass=f={a['passa_alta_hz']}")
    rr = a["reducao_ruido"]
    if rr["ativo"]:
        filtros.append(f"afftdn=nr={rr['intensidade_db']}:nf={rr['piso_ruido_db']}:tn=1")
    return filtros


def _loudnorm(cfg: dict) -> str:
    a = cfg["audio"]
    return f"loudnorm=I={a['loudness_alvo']}:TP={a['pico_maximo']}:LRA={a['lra']}"


def filtro_medicao(cfg: dict) -> str:
    return ",".join(cadeia_base(cfg) + [_loudnorm(cfg) + ":print_format=json"])


def ler_medicao(stderr: str) -> dict:
    blocos = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not blocos:
        raise RuntimeError("Não foi possível medir o loudness do áudio.")
    return json.loads(blocos[-1])


@lru_cache
def _atraso_da_cadeia(cadeia: str, taxa: int) -> float:
    """Mede quanto a cadeia de filtros atrasa o áudio (o afftdn atrasa ~25 ms) passando uma varredura de
    teste por ela. O atraso é compensado no render — sem isso a boca e a voz ficariam fora de sincronia."""
    if not cadeia:
        return 0.0
    def tocar(filtro):
        cmd = [binario("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
               "-i", f"aevalsrc='0.5*sin(2*PI*(150+1200*t)*t)*between(t,0.5,1.5)':s={taxa}:d=2.5"]
        cmd += (["-af", filtro] if filtro else []) + ["-f", "s16le", "-ac", "1", "-"]
        return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.int16).astype(np.float32)
    original, filtrado = tocar(None), tocar(cadeia)
    n = min(len(original), len(filtrado))
    janela = int(0.2 * taxa)
    correlacao = np.correlate(filtrado[:n], original[janela:n - janela], "valid")
    return max(int(np.argmax(correlacao)) - janela, 0) / taxa


def filtro_final(cfg: dict, medicao: dict | None) -> str:
    filtros = cadeia_base(cfg)
    atraso = _atraso_da_cadeia(",".join(filtros), cfg["audio"]["taxa_amostragem"])
    if atraso > 0:
        filtros.append(f"atrim=start={atraso:.5f},asetpts=PTS-STARTPTS")
    if cfg["audio"]["normalizar"] and medicao:
        filtros.append(
            _loudnorm(cfg) + f":measured_I={medicao['input_i']}:measured_TP={medicao['input_tp']}"
            f":measured_LRA={medicao['input_lra']}:measured_thresh={medicao['input_thresh']}"
            f":offset={medicao['target_offset']}:linear=true")
    # apad + "-t" no render: o áudio termina exatamente junto com o vídeo.
    filtros += [f"aresample={cfg['audio']['taxa_amostragem']}", "apad"]
    return ",".join(filtros)
