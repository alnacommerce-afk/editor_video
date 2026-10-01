"""Decide o que sai do vídeo: pausas longas, trechos sem fala, hesitações isoladas e frases recomeçadas.

Regra geral: na dúvida, preserva. Só vira corte o que tem sinal claro (silêncio medido no áudio,
hesitação cercada de pausas, frase repetida logo em seguida).
"""
import math
import re
import unicodedata
from dataclasses import dataclass
from fractions import Fraction

from . import disfluencias
from .pause_detection import AnaliseAudio, classificar_pausa

PONTUACAO = re.compile(r"[\.,!?;:…\"'“”‘’()\[\]\-–—]+")
FIM_DE_FRASE = re.compile(r"[\.!?]$")


def normalizar(palavra: str) -> str:
    return PONTUACAO.sub("", palavra).strip().lower()


def sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _colapsar(texto: str) -> str:
    """'éééé' → 'é', 'hmmm' → 'hm' — hesitações alongadas viram a forma base."""
    return re.sub(r"(.)\1+", r"\1", texto)


@dataclass
class Atomo:
    ini: float
    fim: float
    tipo: str              # silencio | fala | ruido | sem_fala
    forcado: str | None = None

    @property
    def dur(self) -> float:
        return self.fim - self.ini


@dataclass
class Frase:
    i0: int   # índice da primeira palavra
    i1: int   # índice da última palavra (inclusive)


def _frases(analise: AnaliseAudio, cfg: dict) -> list[Frase]:
    palavras, pausas = analise.palavras, analise.pausas_entre_palavras
    minima = cfg["recomecos"]["pausa_minima_entre_frases"]
    frases, inicio = [], 0
    for i, pausa in enumerate(pausas):
        dur = (pausa[1] - pausa[0]) if pausa else 0.0
        if dur >= minima or (FIM_DE_FRASE.search(palavras[i]["palavra"]) and dur >= 0.15):
            frases.append(Frase(inicio, i))
            inicio = i + 1
    if palavras:
        frases.append(Frase(inicio, len(palavras) - 1))
    return frases


def _pausa_antes(analise, f: Frase):
    return analise.pausas_entre_palavras[f.i0 - 1] if f.i0 > 0 else None


def _pausa_depois(analise, f: Frase):
    return analise.pausas_entre_palavras[f.i1] if f.i1 < len(analise.palavras) - 1 else None


def _texto(analise, f: Frase) -> str:
    return " ".join(p["palavra"] for p in analise.palavras[f.i0:f.i1 + 1])


def _tokens(analise, f: Frase) -> list[str]:
    return [t for t in (sem_acento(normalizar(p["palavra"])) for p in analise.palavras[f.i0:f.i1 + 1]) if t]


def detectar_hesitacoes(analise: AnaliseAudio, frases: list[Frase], cfg: dict) -> list[dict]:
    h = cfg["hesitacoes"]
    if not h["ativo"]:
        return []
    muletas = {_colapsar(normalizar(m)) for m in h["muletas"]}
    ambiguas = {_colapsar(normalizar(m)) for m in h["palavras_ambiguas"]}
    remocoes = []
    for k, f in enumerate(frases):
        tokens = [_colapsar(normalizar(p["palavra"])) for p in analise.palavras[f.i0:f.i1 + 1]]
        tokens = [t for t in tokens if t]
        # Só uma "frase" formada APENAS por 1–2 hesitações, isolada por pausas reais dos dois lados.
        # "É muito importante..." nunca entra: o "é" está colado em outras palavras.
        if not tokens or len(tokens) > 2 or not all(t in muletas or t in ambiguas for t in tokens):
            continue
        minima = h["pausa_minima_muletas"] if all(t in muletas for t in tokens) else h["pausa_minima_ambiguas"]
        antes, depois = _pausa_antes(analise, f), _pausa_depois(analise, f)
        primeira, ultima = f.i0 == 0, f.i1 == len(analise.palavras) - 1
        if primeira and ultima:
            continue
        ok_antes = primeira or (antes is not None and antes[1] - antes[0] >= minima)
        ok_depois = ultima or (depois is not None and depois[1] - depois[0] >= minima)
        if not (ok_antes and ok_depois):
            continue
        ini = antes[1] if antes else analise.palavras[f.i0]["inicio"]
        fim = depois[0] if depois else analise.palavras[f.i1]["fim"]
        remocoes.append({"inicio": ini, "fim": fim, "motivo": "hesitacao", "texto": _texto(analise, f),
                         "detalhe": f"hesitação isolada por pausas (mín. {minima}s)", "frase": k})
    return remocoes


def _atomos(duracao: float, analise: AnaliseAudio, cfg: dict) -> list[Atomo]:
    p, h = cfg["pausas"], cfg["hesitacoes"]
    atomos, cursor = [], 0.0
    for ini, fim in analise.silencios:
        if ini > cursor:
            atomos.append(Atomo(cursor, ini, "ruido"))
        atomos.append(Atomo(max(ini, cursor), min(fim, duracao), "silencio"))
        cursor = min(fim, duracao)
    if cursor < duracao:
        atomos.append(Atomo(cursor, duracao, "ruido"))

    palavras = analise.palavras
    for a in atomos:
        if a.tipo != "ruido":
            continue
        if any(min(a.fim, w["fim"]) - max(a.ini, w["inicio"]) >= 0.04 for w in palavras
               if w["fim"] > a.ini and w["inicio"] < a.fim):
            a.tipo = "fala"
        elif a.dur >= p["sem_fala_minimo"] and analise.nivel_medio(a.ini, a.fim) < analise.nivel_fala_db - p["sem_fala_diferenca_db"]:
            a.tipo = "sem_fala"
        elif h["remover_sons_sem_palavras"] and a.dur >= h["som_sem_palavra_minimo"]:
            # Som de voz sem palavra nenhuma ("haaaa", "hmmm" que o Whisper nem escreveu): hesitação.
            a.tipo = "som_sem_palavra"
    return [a for a in atomos if a.dur > 0]


def _removivel(a: Atomo, cfg: dict) -> bool:
    p = cfg["pausas"]
    if a.forcado or a.tipo in ("silencio", "sem_fala", "som_sem_palavra"):
        return True
    # Respiração/ruído curto entre silêncios: preservado (soa natural), a menos que seja só um estalo.
    return a.tipo == "ruido" and (a.dur < p["respiracao_minima"] or not p["preservar_respiracoes"])


def planejar_cortes(duracao: float, analise: AnaliseAudio, cfg: dict) -> dict:
    p = cfg["pausas"]
    frases = _frases(analise, cfg)
    hesitacoes = detectar_hesitacoes(analise, frases, cfg)
    diretos = disfluencias.detectar(analise, cfg)
    repeticoes = [d for d in diretos if d["motivo"] != "hesitacao"]
    hesitacoes = hesitacoes + [d for d in diretos if d["motivo"] == "hesitacao"]

    atomos = _atomos(duracao, analise, cfg)
    for r in detectar_hesitacoes_forcadas(hesitacoes):
        for a in atomos:
            sobreposicao = min(a.fim, r["fim"]) - max(a.ini, r["inicio"])
            if sobreposicao > 0 and sobreposicao >= 0.5 * a.dur:
                a.forcado = r["motivo"]

    falas = [i for i, a in enumerate(atomos) if a.tipo == "fala" and not a.forcado]
    cortes, pausas_auditoria, avisos = [], [], []
    if not falas:
        avisos.append("Nenhuma fala detectada — o vídeo foi mantido inteiro.")
        return {"cortes": [], "pausas": [], "hesitacoes": hesitacoes, "repeticoes": repeticoes, "avisos": avisos,
                "frases": len(frases)}

    primeira, ultima = atomos[falas[0]], atomos[falas[-1]]
    corte_ini = primeira.ini - p["margem_inicio_video"]
    if corte_ini >= p["corte_minimo"]:
        cortes.append({"inicio": 0.0, "fim": corte_ini, "motivo": "inicio_sem_fala", "detalhe": "antes da primeira fala"})
    corte_fim = ultima.fim + p["margem_fim_video"]
    if duracao - corte_fim >= p["corte_minimo"]:
        cortes.append({"inicio": corte_fim, "fim": duracao, "motivo": "fim_sem_fala", "detalhe": "depois da última fala"})

    # Sequências de átomos removíveis entre a primeira e a última fala.
    sequencias, atual = [], []
    for a in atomos[falas[0] + 1:falas[-1]]:
        if _removivel(a, cfg):
            atual.append(a)
        elif atual:
            sequencias.append(atual)
            atual = []
    if atual:
        sequencias.append(atual)

    m_antes, m_depois = p["margem_antes_fala"], p["margem_depois_fala"]
    for seq in sequencias:
        ini, fim = seq[0].ini, seq[-1].fim
        dur = fim - ini
        forcados = sorted({a.forcado for a in seq if a.forcado})
        registro = {"inicio": round(ini, 3), "fim": round(fim, 3), "duracao": round(dur, 3)}

        if forcados:
            manter = cfg["hesitacoes"]["pausa_apos_remocao"]
            motivo, faixa = "+".join(forcados), None
        else:
            faixa = classificar_pausa(dur, p["faixas"])
            registro["faixa"] = faixa["nome"]
            if faixa["acao"] == "manter":
                pausas_auditoria.append({**registro, "acao": "manter"})
                continue
            manter = faixa.get("manter_segundos", 0.0) if faixa["acao"] == "reduzir" else 0.0
            motivo = ("trecho_sem_fala" if any(a.tipo == "sem_fala" for a in seq) else
                      "som_sem_palavra" if any(a.tipo == "som_sem_palavra" for a in seq) else f"pausa_{faixa['nome']}")

        manter = max(manter, m_antes + m_depois)
        cauda = max(m_depois, manter / 2)          # silêncio que fica depois da fala anterior
        cabeca = max(m_antes, manter - cauda)      # silêncio que fica antes da próxima fala
        if forcados:
            # A margem tem que sair do silêncio, nunca do trecho removido (senão sobra um pedaço do "é...").
            lider = sum(a.dur for a in _ate_forcado(seq))
            final = sum(a.dur for a in _ate_forcado(list(reversed(seq))))
            cauda, cabeca = min(cauda, lider), min(cabeca, final)
        c0, c1 = ini + cauda, fim - cabeca
        minimo = 0.04 if forcados else p["corte_minimo"]
        if c1 - c0 < minimo:
            pausas_auditoria.append({**registro, "acao": "manter", "obs": "corte seria pequeno demais"})
            continue
        cortes.append({"inicio": c0, "fim": c1, "motivo": motivo, "detalhe": f"pausa de {dur:.2f}s → {dur - (c1 - c0):.2f}s"})
        pausas_auditoria.append({**registro, "acao": "reduzir" if not forcados else motivo,
                                 "fica_segundos": round(dur - (c1 - c0), 3)})

    # Cortes dentro da fala (recomeços, gaguejos, muletas) entram direto: o ponto de emenda já foi escolhido.
    for d in diretos:
        cortes.append({"inicio": d["inicio"], "fim": d["fim"], "motivo": d["motivo"], "detalhe": d["detalhe"]})

    return {"cortes": sorted(cortes, key=lambda c: c["inicio"]), "pausas": pausas_auditoria, "hesitacoes": hesitacoes,
            "repeticoes": repeticoes, "avisos": avisos, "frases": len(frases)}


def detectar_hesitacoes_forcadas(hesitacoes: list[dict]) -> list[dict]:
    """Só as hesitações isoladas por pausas viram 'átomos forçados' (a pausa em volta é recalculada)."""
    return [x for x in hesitacoes if "frase" in x]


def _ate_forcado(seq: list[Atomo]) -> list[Atomo]:
    resultado = []
    for a in seq:
        if a.forcado:
            break
        resultado.append(a)
    return resultado


def segmentos_mantidos(duracao: float, cortes: list[dict], fps: Fraction, analise: AnaliseAudio, cfg: dict) -> list[tuple[float, float]]:
    """Complemento dos cortes, alinhado à grade de frames (assim vídeo e áudio têm exatamente a mesma duração
    em cada trecho e não há perda de sincronia ao juntar)."""
    quadro = 1 / float(fps)
    total_quadros = math.floor(duracao * float(fps) + 1e-6)
    mantidos, cursor = [], 0.0
    for c in sorted(cortes, key=lambda c: c["inicio"]):
        if c["inicio"] > cursor:
            mantidos.append((cursor, c["inicio"]))
        cursor = max(cursor, c["fim"])
    if cursor < duracao:
        mantidos.append((cursor, duracao))

    alinhados = []
    for a, b in mantidos:
        qa = math.floor(a / quadro + 1e-6)                    # arredonda para fora: nunca perde fala
        qb = min(math.ceil(b / quadro - 1e-6), total_quadros)
        if qb <= qa:
            continue
        if alinhados and qa <= alinhados[-1][1]:
            alinhados[-1] = (alinhados[-1][0], max(alinhados[-1][1], qb))
        else:
            alinhados.append((qa, qb))

    minimo = cfg["seguranca"]["segmento_minimo_sem_fala"]
    resultado = []
    for qa, qb in alinhados:
        a, b = qa * quadro, qb * quadro
        tem_fala = any(w["fim"] > a and w["inicio"] < b for w in analise.palavras)
        if b - a < minimo and not tem_fala:
            continue
        resultado.append((a, b))
    return resultado
