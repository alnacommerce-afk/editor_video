"""Transcrição local com faster-whisper, com timestamps por palavra.

O resultado fica em transcricao.json na pasta de trabalho e é reaproveitado quando o mesmo
vídeo é reprocessado com o mesmo modelo — assim dá para ajustar a configuração de cortes
e reprocessar sem transcrever de novo.
"""
import json
import os
from pathlib import Path

from .config import RAIZ

PASTA_MODELOS = RAIZ / "modelos"


def _tem_cuda() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def transcrever(amostras, duracao: float, cfg: dict, cache: Path, progresso=lambda f: None) -> dict:
    t = cfg["transcricao"]
    if cache.exists():
        dados = json.loads(cache.read_text(encoding="utf-8"))
        if dados.get("modelo") == t["modelo"] and dados.get("idioma_pedido") == t["idioma"]:
            progresso(1.0)
            return dados

    from faster_whisper import WhisperModel

    dispositivo = t["dispositivo"]
    if dispositivo == "auto":
        dispositivo = "cuda" if _tem_cuda() else "cpu"
    tipo = t["tipo_computacao"]
    if tipo == "auto":
        tipo = "float16" if dispositivo == "cuda" else "int8"

    modelo = WhisperModel(t["modelo"], device=dispositivo, compute_type=tipo, cpu_threads=os.cpu_count() or 4,
                          download_root=str(PASTA_MODELOS))
    segmentos, info = modelo.transcribe(
        amostras,   # numpy 16 kHz mono — evita depender do decodificador PyAV
        language=t["idioma"] or None,
        beam_size=t["beam_size"],
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        # Prompt com hesitações faz o Whisper transcrever "é...", "hum" em vez de omiti-los.
        initial_prompt=t["prompt_inicial"] or None,
        condition_on_previous_text=False,
    )

    lista = []
    for seg in segmentos:
        palavras = [{"inicio": round(w.start, 3), "fim": round(w.end, 3), "palavra": w.word.strip(),
                     "prob": round(w.probability, 3)} for w in (seg.words or []) if w.word.strip()]
        lista.append({"inicio": round(seg.start, 3), "fim": round(seg.end, 3), "texto": seg.text.strip(),
                      "palavras": palavras})
        progresso(min(seg.end / max(duracao, 0.001), 1.0))

    dados = {"modelo": t["modelo"], "idioma_pedido": t["idioma"], "idioma": info.language,
             "prob_idioma": round(info.language_probability, 3), "dispositivo": dispositivo, "duracao": duracao,
             "texto": " ".join(s["texto"] for s in lista), "segmentos": lista}
    cache.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    progresso(1.0)
    return dados


def palavras(transcricao: dict) -> list[dict]:
    """Lista plana de palavras em ordem, sem sobreposição."""
    todas = sorted((p for s in transcricao["segmentos"] for p in s["palavras"]), key=lambda p: p["inicio"])
    for anterior, atual in zip(todas, todas[1:]):
        if anterior["fim"] > atual["inicio"]:
            anterior["fim"] = max(anterior["inicio"], atual["inicio"])
    return todas
