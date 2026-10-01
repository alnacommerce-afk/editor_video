"""Leitura da fala palavra por palavra: acha o que o autor disse "errado" e logo corrigiu.

Detectores (todos devolvem trechos de palavras a remover, do início da primeira palavra removida
até o início da palavra que fica):

- recomeço: o autor começa uma frase, abandona e fala de novo de forma mais clara.
    "a conversão de clique para lá... conversão de clique pela impressão"
    "consegue ver aqui o quanto de investimento foi, quanto direito consegue ver o quanto de investimento foi..."
  → compara o trecho abandonado com o que vem logo depois; se a nova fala repete boa parte dele
    (começando pelas mesmas palavras), a primeira tentativa sai.
- gaguejo: palavra (ou par de palavras) repetida em seguida. "eu eu vou", "de de", "pa- para".
- palavra abandonada: conectivo deixado no ar e trocado por outro. "e na... nessa semana".
- muleta: sons que nunca carregam sentido ("hum", "ahn", "haaa", "ééé") saem em qualquer posição.

Os pontos de corte são ajustados para o instante de menor energia perto do início de cada palavra,
para não deixar pedaço de sílaba nem cortar o começo da palavra seguinte.
"""
import re
from difflib import SequenceMatcher

import numpy as np

from .pause_detection import PASSO, AnaliseAudio, _quadro

PONTUACAO = re.compile(r"[\.,!?;:…\"'“”‘’()\[\]–—]+")
FIM_DE_FRASE = re.compile(r"(?<!\.)[\.!?]$")          # "." final, mas não reticências
REFLEXAO = re.compile(r"(\.\.\.|…|-)$")                 # palavra deixada no ar

PALAVRAS_DE_LIGACAO = {"a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "do", "da", "dos", "das", "em", "no",
                       "na", "nos", "nas", "nesse", "nessa", "neste", "nesta", "para", "pra", "pro", "por", "pelo",
                       "pela", "com", "sem", "sobre", "que", "e", "ou", "mas", "porque", "se", "como", "quando",
                       "meu", "minha", "seu", "sua", "esse", "essa", "este", "esta", "isso", "muito", "mais", "ao",
                       "aos", "entre", "ate", "la", "aqui", "ai"}


CONJUNCOES = {"e", "ou", "mas", "nem"}


def sem_acento(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def normalizar(palavra: str) -> str:
    return sem_acento(PONTUACAO.sub("", palavra.replace("-", " ")).strip().lower())


def normalizar_com_acento(palavra: str) -> str:
    """Para muletas o acento importa: "ã" (hesitação) ≠ "a" (artigo)."""
    return PONTUACAO.sub("", palavra.replace("-", " ")).strip().lower()


def colapsar(texto: str) -> str:
    """'haaaa' → 'ha', 'éééé' → 'e', 'hmmm' → 'hm'."""
    return re.sub(r"(.)\1+", r"\1", texto)


def alongada(palavra: str) -> bool:
    """Som esticado de quem está pensando: 'ééé', 'haaaa', 'nééé'."""
    return bool(re.search(r"([aeiouhm])\1\1", sem_acento(palavra.lower())))


def _vale(analise: AnaliseAudio, alvo: float, ini: float, fim: float) -> float:
    """Ponto de emenda: o vale de energia (respiro entre duas palavras) mais próximo de `alvo` dentro de [ini, fim].

    Os tempos do Whisper erram até ~0,4 s, então não dá para cortar no tempo dele. O áudio sempre tem um
    vale quase silencioso entre palavras; é ali que a emenda fica inaudível e não corta sílaba.
    """
    db = analise.db
    a, b = _quadro(max(ini, 0)), min(_quadro(fim) + 1, len(db))
    if b - a < 3:
        return alvo
    trecho = db[a:b]
    vales = [k for k in range(1, len(trecho) - 1)
             if trecho[k] <= trecho[k - 1] and trecho[k] <= trecho[k + 1] and trecho[k] < analise.limiar_db + 6]
    if not vales:
        return (a + int(np.argmin(trecho))) * PASSO + PASSO / 2
    return min(((a + k) * PASSO + PASSO / 2 for k in vales), key=lambda t: abs(t - alvo))


def detectar(analise: AnaliseAudio, cfg: dict) -> list[dict]:
    palavras = analise.palavras
    r, h = cfg["recomecos"], cfg["hesitacoes"]
    tokens = [normalizar(p["palavra"]) for p in palavras]
    n = len(palavras)
    remover: list[tuple[int, int, str, str]] = []   # (i, j, motivo, detalhe): remove palavras[i:j]

    muletas = {colapsar(normalizar_com_acento(m)) for m in h["muletas"]}
    ambiguas = {colapsar(normalizar_com_acento(m)) for m in h["palavras_ambiguas"]}
    excecoes = {normalizar(x) for x in r["repeticoes_permitidas"]}

    # 1) Muletas puras e sons alongados — em qualquer posição.
    if h["ativo"]:
        for i, p in enumerate(palavras):
            base = colapsar(normalizar_com_acento(p["palavra"]))
            if base in muletas and base not in ambiguas:
                remover.append((i, i + 1, "hesitacao", f"muleta “{p['palavra']}”"))
            elif alongada(p["palavra"]) and (base in muletas or base in ambiguas or len(base) <= 2) and base != "a":
                remover.append((i, i + 1, "hesitacao", f"som alongado “{p['palavra']}”"))

    if r["ativo"]:
        # 2) Gaguejo: "eu eu", "vou vou", "eu vou eu vou", "pa- para".
        for i in range(n - 1):
            a, b = tokens[i], tokens[i + 1]
            if a and a == b and a not in excecoes:
                remover.append((i, i + 1, "gaguejo", f"“{palavras[i]['palavra']} {palavras[i + 1]['palavra']}”"))
            elif a and b and len(a) <= 4 and b.startswith(a) and a != b and REFLEXAO.search(palavras[i]["palavra"].strip()):
                remover.append((i, i + 1, "gaguejo", f"palavra começada “{palavras[i]['palavra']}”"))
            elif i + 3 < n and tokens[i:i + 2] == tokens[i + 2:i + 4] and all(tokens[i:i + 2]):
                remover.append((i, i + 2, "gaguejo", f"“{' '.join(p['palavra'] for p in palavras[i:i + 4])}”"))

        # 3) Conectivo deixado no ar e trocado: "e na... nessa semana".
        for i in range(n - 1):
            p, prox = palavras[i]["palavra"].strip(), tokens[i + 1]
            if REFLEXAO.search(p) and tokens[i] in PALAVRAS_DE_LIGACAO and prox and (
                    prox in PALAVRAS_DE_LIGACAO or prox[0] == tokens[i][:1]) and prox != tokens[i]:
                remover.append((i, i + 1, "palavra_abandonada", f"“{p} {palavras[i + 1]['palavra']}”"))

        # 4) Recomeço: o autor volta e fala de novo o mesmo trecho.
        janela, minimo = r["janela_palavras"], r["semelhanca_minima"]
        protegidas: set[int] = set()   # palavras da versão "boa" (refeita) — nunca podem ser removidas depois
        for j in range(2, n - 1):
            melhor = None
            # "...quanto de investimento foi E quanto de receita": continuação paralela, não recomeço.
            if tokens[j - 1] in CONJUNCOES:
                continue
            for i in range(max(0, j - janela), j - 1):
                if not tokens[i] or tokens[i] != tokens[j] or tokens[i + 1] != tokens[j + 1]:
                    continue
                if protegidas.intersection(range(i, j)):
                    continue
                a = tokens[i:j]
                b = tokens[j:j + len(a) + 2]
                iguais = sum(bloco.size for bloco in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
                semelhanca = iguais / len(a)
                # Frase completa antes do recomeço? (ponto final que não seja em "sobre.", "de.", "para." —
                # o Whisper às vezes põe ponto em frase abandonada)
                fechou_frase = any(FIM_DE_FRASE.search(p["palavra"].strip()) for p in palavras[i:j - 1]) or (
                    bool(FIM_DE_FRASE.search(palavras[j - 1]["palavra"].strip())) and tokens[j - 1] not in PALAVRAS_DE_LIGACAO)
                if semelhanca < minimo or (fechou_frase and semelhanca < 0.85):
                    continue   # frases completas diferentes com o mesmo começo são estilo, não erro
                if melhor is None or (semelhanca, j - i) > (melhor[0], melhor[2] - melhor[1]):
                    melhor = (semelhanca, i, j)
            if melhor:
                _, i, j2 = melhor
                protegidas.update(range(j2, min(j2 + (j2 - i) + 2, n)))
                remover.append((i, j2, "repeticao",
                                f"“{' '.join(p['palavra'] for p in palavras[i:j2])}” → refeito em seguida "
                                f"({melhor[0]:.0%} igual)"))

    # Une trechos sobrepostos e converte para tempo.
    remover.sort()
    unidos: list[list] = []
    for i, j, motivo, detalhe in remover:
        if unidos and i < unidos[-1][1]:
            u = unidos[-1]
            if j > u[1]:
                u[1] = j
            if motivo not in u[2]:
                u[2].append(motivo)
            u[3].append(detalhe)
        else:
            unidos.append([i, j, [motivo], [detalhe]])

    resultado = []
    for i, j, motivos, detalhes in unidos:
        # Início do corte: logo depois da palavra que fica antes (vale mais perto do fim dela).
        if i > 0:
            anterior = palavras[i - 1]
            ini = _vale(analise, anterior["fim"], max(anterior["inicio"], anterior["fim"] - 0.15), palavras[i]["inicio"] + 0.05)
        else:
            ini = _vale(analise, palavras[0]["inicio"], palavras[0]["inicio"] - 0.3, palavras[0]["inicio"] + 0.05)
        # Fim do corte: logo antes da palavra que fica depois (vale mais perto do início dela).
        if j < n:
            ultima_removida = palavras[j - 1]
            fim = _vale(analise, palavras[j]["inicio"], max(ultima_removida["fim"] - 0.1, ini + 0.04),
                        palavras[j]["inicio"] + 0.08)
        else:
            fim = palavras[j - 1]["fim"] + 0.05
        if fim - ini < 0.04:
            continue
        resultado.append({"inicio": ini, "fim": fim, "motivo": "+".join(motivos), "detalhe": "; ".join(detalhes),
                          "texto": " ".join(p["palavra"] for p in palavras[i:j])})
    return resultado
