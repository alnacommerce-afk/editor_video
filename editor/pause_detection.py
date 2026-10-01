"""Análise das pausas: junta a energia do áudio (onde há silêncio de verdade) com as palavras do Whisper.

Os timestamps do Whisper são aproximados (às vezes esticam o fim da última palavra da frase
para dentro do silêncio). Por isso o *onde cortar* vem do áudio, e as palavras servem para saber
*o que* é fala, o que é respiração e o que é trecho sem conteúdo.
"""
from dataclasses import dataclass, field

import numpy as np

JANELA = 0.02   # 20 ms
PASSO = 0.01    # 10 ms


@dataclass
class AnaliseAudio:
    db: np.ndarray
    piso_db: float
    nivel_fala_db: float
    limiar_db: float
    silencios: list[tuple[float, float]]
    palavras: list[dict]
    # pausas_entre_palavras[i] = intervalo de silêncio real entre palavras[i] e palavras[i+1] (ou None)
    pausas_entre_palavras: list[tuple[float, float] | None]
    palavras_descartadas: list[dict] = field(default_factory=list)

    def nivel_medio(self, ini: float, fim: float) -> float:
        a, b = _quadro(ini), max(_quadro(fim), _quadro(ini) + 1)
        trecho = self.db[a:b]
        return float(np.mean(trecho)) if len(trecho) else self.piso_db

    def resumo(self) -> dict:
        return {"piso_ruido_db": round(self.piso_db, 1), "nivel_fala_db": round(self.nivel_fala_db, 1),
                "limiar_silencio_db": round(self.limiar_db, 1), "silencios_detectados": len(self.silencios),
                "palavras": len(self.palavras), "palavras_descartadas_como_alucinacao": len(self.palavras_descartadas)}


def _quadro(t: float) -> int:
    return max(int(round(t / PASSO)), 0)


def energia_db(amostras: np.ndarray, taxa: int) -> np.ndarray:
    """Energia RMS em dB a cada 10 ms (janela de 20 ms), via soma acumulada — leve até para vídeos longos."""
    n_janela, n_passo = int(taxa * JANELA), int(taxa * PASSO)
    if len(amostras) < n_janela:
        amostras = np.pad(amostras, (0, n_janela - len(amostras)))
    acumulado = np.concatenate([[0.0], np.cumsum(amostras.astype(np.float64) ** 2)])
    inicios = np.arange(0, len(amostras) - n_janela + 1, n_passo)
    media = (acumulado[inicios + n_janela] - acumulado[inicios]) / n_janela
    return 10 * np.log10(media + 1e-10)


def _intervalos(mascara: np.ndarray) -> list[tuple[int, int]]:
    """Trechos contíguos True como (quadro_inicial, quadro_final_exclusivo)."""
    if not len(mascara):
        return []
    m = np.concatenate([[False], mascara, [False]]).astype(np.int8)
    bordas = np.flatnonzero(np.diff(m))
    return list(zip(bordas[::2], bordas[1::2]))


def detectar_silencios(db: np.ndarray, limiar: float, duracao_minima: float, ignorar_ruidos: float) -> list[tuple[float, float]]:
    silencioso = db < limiar
    # Estalos/cliques curtos dentro de um silêncio não quebram o silêncio.
    for a, b in _intervalos(~silencioso):
        if (b - a) * PASSO < ignorar_ruidos and a > 0 and b < len(silencioso):
            silencioso[a:b] = True
    meio = JANELA / 2
    return [(a * PASSO + meio - PASSO / 2, b * PASSO + meio - PASSO / 2)
            for a, b in _intervalos(silencioso) if (b - a) * PASSO >= duracao_minima]


def _subtrair(intervalos, protegidos):
    """Remove dos intervalos as partes que caem dentro dos protegidos."""
    resultado = []
    for ini, fim in intervalos:
        pedacos = [(ini, fim)]
        for p0, p1 in protegidos:
            if p1 <= ini or p0 >= fim:
                continue
            novos = []
            for a, b in pedacos:
                if p1 <= a or p0 >= b:
                    novos.append((a, b))
                    continue
                if a < p0:
                    novos.append((a, p0))
                if p1 < b:
                    novos.append((p1, b))
            pedacos = novos
        resultado.extend(pedacos)
    return resultado


def _nucleo(p: dict) -> tuple[float, float]:
    """Parte central de uma palavra — nunca pode ser tratada como silêncio (protege falas baixas)."""
    d = min(p["fim"] - p["inicio"], 0.8)
    return p["inicio"] + 0.25 * d, p["inicio"] + 0.75 * d


def _meio(p: dict) -> float:
    return p["inicio"] + min((p["fim"] - p["inicio"]) / 2, 0.4)


def analisar(amostras: np.ndarray, taxa: int, palavras: list[dict], cfg: dict) -> AnaliseAudio:
    s = cfg["silencio"]
    db = energia_db(amostras, taxa)
    piso = float(np.percentile(db, 10))

    quadros_fala = np.zeros(len(db), dtype=bool)
    for p in palavras:
        quadros_fala[_quadro(p["inicio"]):_quadro(p["fim"])] = True
    nivel_fala = float(np.median(db[quadros_fala])) if quadros_fala.any() else float(np.percentile(db, 90))

    if s["limiar_db"] == "auto":
        limiar = piso + s["fator_limiar_auto"] * (nivel_fala - piso)
        limiar = max(limiar, piso + 3)   # gravação muito ruidosa: quase nada vira silêncio (conservador)
    else:
        limiar = float(s["limiar_db"])

    # Palavras inteiramente dentro de silêncio são alucinações do Whisper.
    validas, descartadas = [], []
    for p in palavras:
        trecho = db[_quadro(p["inicio"]):max(_quadro(p["fim"]), _quadro(p["inicio"]) + 1)]
        (validas if len(trecho) and trecho.max() >= limiar else descartadas).append(p)

    silencios = detectar_silencios(db, limiar, s["duracao_minima"], s["ignorar_ruidos_menores_que"])
    silencios = [(a, b) for a, b in _subtrair(silencios, [_nucleo(p) for p in validas]) if b - a >= s["duracao_minima"]]

    pausas = []
    for atual, proxima in zip(validas, validas[1:]):
        j0, j1 = _meio(atual), _meio(proxima)
        candidatos = [(max(a, j0), min(b, j1)) for a, b in silencios if b > j0 and a < j1]
        candidatos = [c for c in candidatos if c[1] - c[0] >= 0.05]
        pausas.append(max(candidatos, key=lambda c: c[1] - c[0]) if candidatos else None)

    return AnaliseAudio(db=db, piso_db=piso, nivel_fala_db=nivel_fala, limiar_db=limiar, silencios=silencios,
                        palavras=validas, pausas_entre_palavras=pausas, palavras_descartadas=descartadas)


def classificar_pausa(duracao: float, faixas: list[dict]) -> dict:
    for faixa in faixas:
        if faixa["ate"] is None or duracao < faixa["ate"]:
            return faixa
    return faixas[-1]
