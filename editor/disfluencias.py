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

# Frase refeita por inteiro (veja o detector 5 em `detectar`). Sobrescrevível em editor.config.json → recomecos.refacao.
REFACAO_PADRAO = {"ativo": True, "palavras_minimas": 8, "ancora_minima": 4, "semelhanca_minima": 0.72,
                  "pausa_minima": 0.5, "diferenca_minima": 0.03, "pausa_interna": 0.30}


def _pausa_entre(analise: AnaliseAudio, k: int) -> float:
    """Pausa real entre palavras[k] e palavras[k + 1]: o maior entre o intervalo do Whisper e o silêncio medido
    no áudio (o Whisper costuma esticar a última palavra da frase e esconder parte da pausa)."""
    p = analise.palavras
    folga = p[k + 1]["inicio"] - p[k]["fim"]
    silencio = analise.pausas_entre_palavras[k] if k < len(analise.pausas_entre_palavras) else None
    return max(folga, (silencio[1] - silencio[0]) if silencio else 0.0)


def _qualidade(analise: AnaliseAudio, ini: int, fim: int, muletas: set, pausa_interna: float) -> tuple[float, str]:
    """Nota de uma tomada (maior = melhor) e uma explicação curta. Mede o que o espectador sente como "tropeço":
    pausas no meio da frase, muletas, gaguejo, fala arrastada e a confiança do Whisper (fala clara = mais confiança)."""
    p = analise.palavras[ini:fim]
    confianca = sum(w.get("prob", 0.8) for w in p) / len(p)
    pausas = sum(1 for k in range(ini, fim - 1) if _pausa_entre(analise, k) >= pausa_interna)
    sons = sum(1 for w in p if colapsar(normalizar_com_acento(w["palavra"])) in muletas or alongada(w["palavra"]))
    toks = [normalizar(w["palavra"]) for w in p]
    gagos = sum(1 for a, b in zip(toks, toks[1:]) if a and a == b)
    por_palavra = (p[-1]["fim"] - p[0]["inicio"]) / len(p)
    arrasto = max(0.0, por_palavra - 0.55) * 0.5
    nota = confianca - 0.10 * pausas - 0.08 * sons - 0.08 * gagos - arrasto
    partes = [f"{pausas} pausa{'s' if pausas != 1 else ''} no meio"]
    if sons:
        partes.append(f"{sons} muleta{'s' if sons != 1 else ''}")
    if gagos:
        partes.append(f"{gagos} gaguejo{'s' if gagos != 1 else ''}")
    partes.append(f"confiança {confianca:.0%}")
    return nota, ", ".join(partes)


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
        ancora_minima, ancora_trecho_max = r["ancora_minima"], r["ancora_trecho_maximo"]
        candidatos = []
        for j in range(2, n - 1):
            # "...quanto de investimento foi E quanto de receita": continuação paralela, não recomeço.
            if tokens[j - 1] in CONJUNCOES:
                continue
            for i in range(max(0, j - janela), j - 1):
                if not tokens[i] or tokens[i] != tokens[j] or tokens[i + 1] != tokens[j + 1]:
                    continue
                # Âncora = palavras idênticas seguidas no começo das duas tentativas.
                ancora = 0
                while i + ancora < j and j + ancora < n and tokens[i + ancora] == tokens[j + ancora]:
                    ancora += 1
                trecho_ancora = tokens[i:i + ancora]
                # "do produto ... do produto" é coincidência; recomeço tem palavra de conteúdo na âncora,
                # e se começa com artigo/preposição precisa repetir pelo menos 3 palavras.
                if not any(len(t) >= 4 and t not in PALAVRAS_DE_LIGACAO for t in trecho_ancora):
                    continue
                if tokens[i] in PALAVRAS_DE_LIGACAO and ancora < 3:
                    continue
                a = tokens[i:j]
                b = tokens[j:j + len(a) + 2]
                iguais = sum(bloco.size for bloco in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
                semelhanca = iguais / len(a)
                # Frase completa antes do recomeço? (ponto final que não seja em "sobre.", "de.", "para." —
                # o Whisper às vezes põe ponto em frase abandonada)
                fechou_frase = any(FIM_DE_FRASE.search(p["palavra"].strip()) for p in palavras[i:j - 1]) or (
                    bool(FIM_DE_FRASE.search(palavras[j - 1]["palavra"].strip())) and tokens[j - 1] not in PALAVRAS_DE_LIGACAO)
                parecido = semelhanca >= minimo and not (fechou_frase and semelhanca < 0.85)
                # "Aqui em cadastro do SKU... nós fazemos o cadastro do SKU. Aqui em cadastro do SKU, clica...":
                # 4+ palavras idênticas e um desvio no meio = o autor abandonou e recomeçou.
                ancora_longa = ancora >= ancora_minima and ancora + 3 <= len(a) <= ancora_trecho_max
                if parecido or ancora_longa:
                    motivo = f"{semelhanca:.0%} igual" if parecido else f"{ancora} palavras idênticas no recomeço"
                    candidatos.append((iguais, ancora, i, j, motivo))

        # Os maiores primeiro: "tem um custo de 3 reais, já fica aqui 310, já fica aqui, então realmente
        # tem um custo de 3 reais, já fica aqui 30" vira um corte só, em vez de só tirar o "310".
        candidatos.sort(key=lambda c: (c[0], c[1], c[3] - c[2]), reverse=True)
        protegidas: set[int] = set()   # palavras da versão "boa" (refeita) — nunca podem ser removidas
        aceitos: list[tuple[int, int]] = []
        for iguais, ancora, i, j, motivo in candidatos:
            if protegidas.intersection(range(i, j)):
                continue
            if any(i < aj and ai < j for ai, aj in aceitos) and not any(ai <= i and j <= aj for ai, aj in aceitos):
                continue   # sobreposição parcial com um recomeço maior já aceito
            if any(ai <= i and j <= aj for ai, aj in aceitos):
                continue   # já está dentro de um corte maior
            aceitos.append((i, j))
            protegidas.update(range(j, min(j + (j - i) + 2, n)))
            remover.append((i, j, "repeticao",
                            f"“{' '.join(p['palavra'] for p in palavras[i:j])}” → refeito em seguida ({motivo})"))

        # 5) Frase refeita por inteiro: o autor termina a frase, para e fala quase a mesma coisa de novo.
        #    "Gostou dessa ferramenta? Comenta aqui X que eu mando o link no DM. [pausa] Gostou dessa ferramenta?
        #    Comenta aqui X que eu mando para você no direct."
        #    O detector 4 ignora isso de propósito (duas frases completas parecidas costumam ser paralelismo:
        #    "Eu quero que você saiba. Eu quero que você entenda."). Aqui a regra é mais rígida: frase longa
        #    (8+ palavras), começo idêntico (4+ palavras), 72%+ igual e uma PAUSA antes da repetição. Das duas
        #    tomadas, sai a pior (nota em `_qualidade`); se forem parecidas, vale a última.
        rf = {**REFACAO_PADRAO, **r.get("refacao", {})}
        if rf["ativo"] and n > 3:
            ja = {k for ini, fim, *_ in remover for k in range(ini, fim)}
            achados = []
            for j in range(2, n - 1):
                if j in ja or tokens[j - 1] in CONJUNCOES or _pausa_entre(analise, j - 1) < rf["pausa_minima"]:
                    continue
                for i in range(max(0, j - janela), j - 1):
                    ancora = 0
                    while i + ancora < j and j + ancora < n and tokens[i + ancora] == tokens[j + ancora]:
                        ancora += 1
                    if ancora < rf["ancora_minima"] or j - i < rf["palavras_minimas"]:
                        continue
                    if not any(len(t) >= 4 and t not in PALAVRAS_DE_LIGACAO for t in tokens[i:i + ancora]):
                        continue
                    a = tokens[i:j]
                    b = tokens[j:j + len(a) + 2]
                    blocos = SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
                    semelhanca = sum(bl.size for bl in blocos) / len(a)
                    if semelhanca < rf["semelhanca_minima"]:
                        continue
                    fim2 = min(j + max(bl.b + bl.size for bl in blocos), n)
                    extra = 0
                    while fim2 < n and extra < 2 and not FIM_DE_FRASE.search(palavras[fim2 - 1]["palavra"].strip()):
                        fim2, extra = fim2 + 1, extra + 1
                    if any(k in ja for k in range(i, fim2)):
                        continue
                    achados.append((semelhanca, j - i, i, j, fim2))
            achados.sort(reverse=True)
            usados: list[tuple[int, int]] = []
            for semelhanca, _, i, j, fim2 in achados:
                if any(i < uf and ui < fim2 for ui, uf in usados):
                    continue
                usados.append((i, fim2))
                nota1, por1 = _qualidade(analise, i, j, muletas, rf["pausa_interna"])
                nota2, por2 = _qualidade(analise, j, fim2, muletas, rf["pausa_interna"])
                sai_a_primeira = nota2 >= nota1 - rf["diferenca_minima"]   # empate: vale a última tomada
                ini_r, fim_r = (i, j) if sai_a_primeira else (j, fim2)
                saiu, ficou = (por1, por2) if sai_a_primeira else (por2, por1)
                texto = " ".join(p["palavra"] for p in palavras[ini_r:fim_r])
                remover.append((ini_r, fim_r, "repeticao",
                                f"“{texto}” → frase dita duas vezes ({semelhanca:.0%} igual); saiu a "
                                f"{'1ª' if sai_a_primeira else '2ª'} tomada ({saiu}) e ficou a outra ({ficou})"))

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
