"""Ritmo da fala: mede a velocidade de articulação (sílabas por segundo falando, sem contar pausas)
e decide se vale acelerar o vídeo de leve (1,1× ou 1,2×).

A aceleração é a mesma no vídeo inteiro (trechos com velocidades diferentes soariam estranhos) e
preserva o tom da voz (atempo do FFmpeg), então a pessoa não fica com voz de desenho animado.
"""
import re

VOGAIS = re.compile(r"[aeiouáéíóúâêôãõàüy]+", re.IGNORECASE)


def silabas(palavra: str) -> int:
    """Aproximação para português: cada grupo de vogais é um núcleo de sílaba."""
    return max(1, len(VOGAIS.findall(palavra)))


def medir(palavras: list[dict], segmentos: list[tuple[float, float]]) -> dict:
    mantidas = [p for p in palavras
                if any(a <= (p["inicio"] + p["fim"]) / 2 <= b for a, b in segmentos)]
    if len(mantidas) < 5:
        return {"silabas_por_segundo": None, "palavras": len(mantidas)}
    # Blocos de fala contínua (palavras separadas por menos de 250 ms) — o tempo "falando".
    tempo, ini, fim = 0.0, mantidas[0]["inicio"], mantidas[0]["fim"]
    for p in mantidas[1:]:
        if p["inicio"] - fim > 0.25:
            tempo += fim - ini
            ini = p["inicio"]
        fim = max(fim, p["fim"])
    tempo += fim - ini
    total = sum(silabas(p["palavra"]) for p in mantidas)
    return {"silabas_por_segundo": round(total / max(tempo, 0.1), 2), "silabas": total,
            "tempo_falando": round(tempo, 2), "palavras": len(mantidas)}


def escolher_velocidade(medicao: dict, cfg: dict) -> tuple[float, str]:
    v = cfg["velocidade"]
    taxa = medicao.get("silabas_por_segundo")
    if not v["ativo"] or taxa is None:
        return 1.0, "aceleração desativada" if not v["ativo"] else "fala insuficiente para medir o ritmo"
    for faixa in v["faixas"]:
        if taxa < faixa["abaixo_de"]:
            fator = min(faixa["fator"], v["maximo"])
            return fator, f"fala a {taxa} sílabas/s (abaixo de {faixa['abaixo_de']}) → {fator}×"
    return 1.0, f"fala a {taxa} sílabas/s — ritmo bom, sem aceleração"
