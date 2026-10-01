"""Testa a leitura da fala (recomeços, gaguejos, muletas) com frases reais — sem precisar de vídeo.

    python testes/testar_disfluencias.py

Cada caso diz qual texto deve sobrar depois dos cortes. Inclui casos que NÃO podem ser cortados
(paralelismo, ênfase, "é" como verbo).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from editor import config, disfluencias  # noqa: E402
from editor.pause_detection import PASSO, AnaliseAudio  # noqa: E402

CASOS = [
    # (fala gravada, o que deve sobrar)
    ("Então, veja que ela vem melhorando na última semana e na... nessa semana.",
     "Então, veja que ela vem melhorando na última semana e nessa semana."),
    ("Veja aqui a... veja aqui a impressão dela, quantos cliques",
     "veja aqui a impressão dela, quantos cliques"),
    ("quantos cliques, a conversão de clique para lá... conversão de clique pela impressão e mais ao lado",
     "quantos cliques, a conversão de clique pela impressão e mais ao lado"),
    ("uma visão mais pragmática, consegue ver aqui o quanto de investimento foi, quanto direito consegue ver o quanto "
     "de investimento foi e quanto de receita foi gerada",
     "uma visão mais pragmática, consegue ver o quanto de investimento foi e quanto de receita foi gerada"),
    ("Hoje eu vou falar sobre... hoje eu vou mostrar três dicas para organizar a casa.",
     "hoje eu vou mostrar três dicas para organizar a casa."),
    ("eu eu vou te mostrar uma coisa", "eu vou te mostrar uma coisa"),
    ("e aí você vai vai clicar aqui", "e aí você vai clicar aqui"),
    ("hum, hoje eu quero te mostrar", "hoje eu quero te mostrar"),
    ("a gente pode haaaa usar isso", "a gente pode usar isso"),
    # --- não pode cortar ---
    ("É muito importante você saber disso.", "É muito importante você saber disso."),
    ("ferramenta muito útil no dia a dia dos sellers", "ferramenta muito útil no dia a dia dos sellers"),
    ("Eu quero que você saiba. Eu quero que você entenda.", "Eu quero que você saiba. Eu quero que você entenda."),
    ("isso é muito muito bom", "isso é muito muito bom"),
    ("veja aqui a impressão e a conversão", "veja aqui a impressão e a conversão"),
]


def analise_sintetica(texto: str) -> AnaliseAudio:
    """Cada palavra dura 0,3 s com 0,06 s de silêncio entre elas (energia baixa no intervalo)."""
    palavras, t = [], 0.2
    for w in texto.split():
        palavras.append({"inicio": t, "fim": t + 0.3, "palavra": w, "prob": 0.9})
        t += 0.36
    db = np.full(int((t + 0.5) / PASSO), -65.0)
    for p in palavras:
        db[int(p["inicio"] / PASSO):int(p["fim"] / PASSO)] = -20.0
    return AnaliseAudio(db=db, piso_db=-65, nivel_fala_db=-20, limiar_db=-50, silencios=[], palavras=palavras,
                        pausas_entre_palavras=[None] * (len(palavras) - 1))


def main() -> int:
    cfg = config.carregar()
    falhas = 0
    for fala, esperado in CASOS:
        analise = analise_sintetica(fala)
        cortes = disfluencias.detectar(analise, cfg)
        ficou = [p["palavra"] for p in analise.palavras
                 if not any(c["inicio"] <= (p["inicio"] + p["fim"]) / 2 <= c["fim"] for c in cortes)]
        resultado = " ".join(ficou)
        ok = resultado.lower() == esperado.lower()
        falhas += not ok
        print(f"[{'OK' if ok else 'FALHOU'}] {fala}")
        if not ok:
            print(f"         esperado: {esperado}\n         ficou:    {resultado}")
    print(f"\n{'Tudo certo' if not falhas else f'{falhas} caso(s) falharam'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
