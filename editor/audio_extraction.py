"""Extrai o áudio para análise (WAV mono 16 kHz — o formato que o Whisper usa)."""
import wave
from pathlib import Path

import numpy as np

from .ferramentas import ffmpeg

TAXA_ANALISE = 16000


def extrair(video: Path, destino: Path) -> Path:
    ffmpeg(["-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(TAXA_ANALISE), "-c:a", "pcm_s16le",
            str(destino)], "extrair o áudio")
    return destino


def carregar(wav: Path) -> np.ndarray:
    with wave.open(str(wav), "rb") as f:
        bruto = f.readframes(f.getnframes())
    return np.frombuffer(bruto, dtype=np.int16).astype(np.float32) / 32768.0
