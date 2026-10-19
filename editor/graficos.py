"""Emotion graphics: cartões animados sincronizados com a fala (opcional, marcado na tela de envio).

Tudo local e por regras simples — sem IA paga e sem enviar nada para fora do computador:
  1. a fala, já nos tempos do vídeo EDITADO, é dividida em trechos curtos (frases e orações);
  2. cada trecho rende, no máximo, um cartão: chamada para ação ("comenta X"), número/valor ou o ponto-chave da frase;
  3. os cartões são desenhados com Pillow e sobrepostos pelo FFmpeg (entram deslizando, saem com fade);
  4. o texto principal de cada cartão são palavras que a pessoa realmente falou; só o rótulo pequeno
     (ATENÇÃO, GANHO, AGENDA...) vem de uma lista fixa de categorias.
"""
import re
import unicodedata
from pathlib import Path

from .ferramentas import ffmpeg

NAVY, GREEN, RED, BLUE, AMBER = (11, 46, 87), (63, 159, 99), (211, 47, 47), (30, 102, 190), (232, 156, 20)
BRANCO, SUAVE = (255, 255, 255), (200, 214, 232)
LARGURA_BASE = 752   # o desenho foi pensado nesta largura; tudo é multiplicado por k = largura_real / 752

PADRAO = {"espaco_minimo": 2.8, "maximo": 40, "duracao_minima": 2.4, "duracao_maxima": 4.4, "max_caracteres": 56}

# Só para tirar do começo do texto (nunca do meio): conectivos e verbos fracos que não carregam o assunto.
LIGACAO = {"então", "bom", "aí", "ai", "e", "mas", "porque", "que", "agora", "hoje", "olha", "olá", "ola", "oi", "tipo",
           "né", "assim", "também", "tambem", "só", "so", "bem", "daí", "dai", "ou", "se", "quando", "onde", "como",
           "aqui", "isso", "essa", "esse", "dessa", "desse", "nós", "nos", "a", "o", "as", "os", "um", "uma", "de", "do",
           "da", "no", "na", "em", "com", "para", "pra", "por", "eu", "vai", "vou", "é", "foi", "tem", "temos", "tenho",
           "ter", "ser", "está", "esta", "então,", "olha,"}
FINAIS_FRACOS = LIGACAO | {"você", "voce", "meu", "minha", "seu", "sua", "dele", "dela", "das", "dos", "nas", "nos",
                           "pela", "pelas", "pelo", "pelos", "ao", "aos", "às", "num", "numa", "entre", "sobre", "até",
                           "quanto", "mais", "muito"}

# (ícone, cor, rótulo, palavras-gatilho já sem acento). A primeira categoria que casar vence.
CATEGORIAS = [
    ("alert", AMBER, "ATENÇÃO", {"cansado", "cansada", "problema", "erro", "cuidado", "perder", "perdido", "perde",
                                  "dificil", "medo", "risco", "atencao", "dor", "perigo"}),
    ("up", GREEN, "GANHO", {"entrada", "entradas", "receita", "receitas", "lucro", "faturamento", "vender", "vendas",
                            "venda", "ganhar", "ganho", "crescer", "aumentar", "economizar", "resultado", "resultados"}),
    ("down", RED, "GASTO", {"saida", "saidas", "despesa", "despesas", "custo", "custos", "gasto", "gastos", "prejuizo",
                            "divida", "dividas", "imposto", "impostos"}),
    ("cal", BLUE, "AGENDA", {"calendario", "agenda", "data", "datas", "dia", "dias", "mes", "semana", "prazo",
                             "vencimento"}),
    ("clock", BLUE, "TEMPO", {"tempo", "hora", "horas", "minuto", "minutos", "rapido", "rapidamente", "segundo",
                              "segundos", "automatico", "automatica", "automaticamente"}),
    ("user", AMBER, "PARA QUEM", {"cliente", "clientes", "equipe", "pessoas", "vendedor", "vendedores", "empresa",
                                  "empreendedor", "lojista"}),
    ("chat", GREEN, "CONVERSA", {"mensagem", "direct", "whatsapp", "contato", "comentario", "comentarios"}),
    ("chart", BLUE, "NÚMEROS", {"caixa", "fluxo", "projecao", "grafico", "painel", "dashboard", "relatorio", "metrica",
                                "metricas", "dados", "controle", "ferramenta", "planilha"}),
]
PADRAO_CAT = ("spark", NAVY, "EM DESTAQUE")

NUMERO = re.compile(r"R\$\s?\d[\d.,]*|\d[\d.,]*\s?(?:%|mil\b|milh[õo]es\b|milh[ãa]o\b|reais\b|x\b)?", re.I)


def _norm(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _limpar(palavra: str) -> str:
    return palavra.strip(" .,;:!?…\"'()")


# ---------------------------------------------------------------- planejamento (sem Pillow, sem FFmpeg)

def palavras_editadas(palavras: list[dict], segmentos, converter) -> list[tuple[float, float, str]]:
    """Palavras mantidas, com início/fim no vídeo editado. Mesmo critério da legenda: decide pelo meio da palavra."""
    saida = []
    for p in palavras:
        meio = (p["inicio"] + p["fim"]) / 2
        trecho = next(((a, b) for a, b in segmentos if a <= meio <= b), None)
        if trecho is None:
            continue   # palavra removida (hesitação/repetição)
        saida.append((converter(max(p["inicio"], trecho[0])), converter(min(p["fim"], trecho[1])), p["palavra"]))
    return saida


def trechos_de_fala(pal: list[tuple[float, float, str]]) -> list[list[tuple[float, float, str]]]:
    """Divide a fala em orações: fecha em ponto/interrogação, em vírgula (se já tem 3+ palavras) ou em pausa."""
    trechos, atual = [], []
    for w in pal:
        if atual and w[0] - atual[-1][1] > 0.45:
            trechos.append(atual)
            atual = []
        atual.append(w)
        fim = w[2].rstrip().endswith((".", "!", "?"))
        virgula = w[2].rstrip().endswith((",", ";", ":")) and len(atual) >= 3
        if fim or virgula:
            trechos.append(atual)
            atual = []
    if atual:
        trechos.append(atual)
    return trechos


def _categoria(norm: str):
    palavras = set(re.findall(r"[a-z0-9]+", norm))
    for icone, cor, rotulo, gatilhos in CATEGORIAS:
        if palavras & gatilhos:
            return icone, cor, rotulo
    return PADRAO_CAT


def _texto_chave(trecho, limite: int) -> str | None:
    tokens = [w[2] for w in trecho]
    while tokens and _limpar(tokens[0]).lower() in LIGACAO:
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None
    pergunta = tokens[-1].rstrip().endswith("?")
    corte = False
    saida = []
    for t in tokens:
        if len(" ".join(saida + [t])) > limite:
            corte = True
            break
        saida.append(t)
    while corte and len(saida) > 2 and _limpar(saida[-1]).lower() in FINAIS_FRACOS:
        saida.pop()
    if len(saida) < 2:
        return None
    texto = " ".join(saida).rstrip(" .,;:!")
    if corte:
        texto += "…"
    elif pergunta and not texto.endswith("?"):
        texto += "?"
    return texto[0].upper() + texto[1:]


def _item(trecho, cfg_g: dict) -> dict | None:
    norm = _norm(" ".join(w[2] for w in trecho))
    ini, fim = trecho[0][0], trecho[-1][1]

    # 1) Chamada para ação: "comenta (aqui) PALAVRA ..." -> balão com a palavra pedida.
    for i, w in enumerate(trecho):
        if _norm(_limpar(w[2])).startswith("coment") and len(_norm(_limpar(w[2]))) <= 9:
            resto = [_limpar(x[2]) for x in trecho[i + 1:]]
            while resto and _norm(resto[0]) in {"aqui", "a", "ai", "com", "a palavra", "palavra", "la", "ali"}:
                resto.pop(0)
            if resto and len(resto[0]) >= 3:
                return {"tipo": "cta", "inicio": w[0], "fim": fim, "icone": "chat", "cor": GREEN,
                        "rotulo": "COMENTA AQUI", "texto": resto[0].upper()}

    # 2) Número ou valor em reais: aparece grande, no instante em que foi falado.
    for i, w in enumerate(trecho):
        if re.search(r"\d", w[2]):
            achado = NUMERO.search(" ".join(x[2] for x in trecho[i:i + 2]))
            if achado and re.search(r"\d", achado.group(0)):
                return {"tipo": "numero", "inicio": w[0], "fim": fim, "icone": "chart", "cor": BLUE,
                        "rotulo": "NÚMERO", "texto": achado.group(0).strip(" .,;:!?")}

    # 3) Ponto-chave da frase.
    chave = _texto_chave(trecho, cfg_g["max_caracteres"])
    if not chave or fim - ini < 1.2:
        return None
    icone, cor, rotulo = _categoria(norm)
    return {"tipo": "frase", "inicio": ini, "fim": fim, "icone": icone, "cor": cor, "rotulo": rotulo, "texto": chave}


def planejar(pal: list[tuple[float, float, str]], duracao_final: float, cfg: dict) -> list[dict]:
    """Lista de cartões {inicio, fim, tipo, icone, cor, rotulo, texto} na linha do tempo do vídeo editado."""
    g = {**PADRAO, **cfg.get("graficos", {})}
    espaco = max(g["espaco_minimo"], duracao_final / max(g["maximo"], 1))
    itens: list[dict] = []
    for trecho in trechos_de_fala(pal):
        item = _item(trecho, g)
        if not item:
            continue
        cta = item["tipo"] == "cta"
        inicio = item["inicio"] + (0.0 if cta else 0.10)
        if cta:
            fim = max(item["fim"] + 1.2, inicio + 3.0)
        else:
            fim = inicio + min(max((item["fim"] - item["inicio"]) * 0.95 + 0.4, g["duracao_minima"]), g["duracao_maxima"])
        fim = min(fim, duracao_final - 0.05)
        if fim - inicio < 1.2:
            continue
        item["inicio"], item["fim"] = round(inicio, 3), round(fim, 3)
        if itens:
            anterior = itens[-1]
            if cta and anterior["tipo"] == "cta" and anterior["texto"] == item["texto"] and inicio - anterior["fim"] < 6:
                anterior["fim"] = item["fim"]   # mesma chamada falada duas vezes: um balão só, sem repetir a animação
                continue
            if not cta and inicio < anterior["inicio"] + espaco:
                continue
            if inicio < anterior["fim"] + 0.1:
                anterior["fim"] = round(inicio - 0.1, 3)
                if anterior["fim"] - anterior["inicio"] < 1.2:
                    itens.pop()
        itens.append(item)
    return itens


# ---------------------------------------------------------------- desenho (Pillow) e composição (FFmpeg)

def _fonte(tamanho: int):
    from PIL import ImageFont
    for nome in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        for pasta in ("C:/Windows/Fonts/", ""):
            try:
                return ImageFont.truetype(pasta + nome, tamanho)
            except OSError:
                continue
    return ImageFont.load_default()


def _icone(d, tipo: str, cx: float, cy: float, s: float, cor):
    d.ellipse((cx - s, cy - s, cx + s, cy + s), fill=cor)
    k = s * 0.5
    L = max(int(s / 9), 3)
    if tipo == "up":
        d.polygon([(cx, cy - k), (cx - k, cy + k * 0.7), (cx + k, cy + k * 0.7)], fill=BRANCO)
    elif tipo == "down":
        d.polygon([(cx, cy + k), (cx - k, cy - k * 0.7), (cx + k, cy - k * 0.7)], fill=BRANCO)
    elif tipo == "cal":
        d.rounded_rectangle((cx - k, cy - k * 0.8, cx + k, cy + k), 6, outline=BRANCO, width=L)
        d.rectangle((cx - k, cy - k * 0.8, cx + k, cy - k * 0.2), fill=BRANCO)
        for dx in (-k * 0.45, k * 0.45):
            d.line((cx + dx, cy - k * 1.1, cx + dx, cy - k * 0.55), fill=BRANCO, width=L)
        for dx in (-k * 0.5, 0, k * 0.5):
            d.ellipse((cx + dx - L, cy + k * 0.3 - L, cx + dx + L, cy + k * 0.3 + L), fill=BRANCO)
    elif tipo == "chart":
        for i, h in enumerate((0.5, 0.95, 1.4)):
            x0 = cx - k + i * k * 0.75
            d.rectangle((x0, cy + k - h * k, x0 + k * 0.5, cy + k), fill=BRANCO)
    elif tipo == "clock":
        d.ellipse((cx - k, cy - k, cx + k, cy + k), outline=BRANCO, width=L)
        d.line((cx, cy, cx, cy - k * 0.65), fill=BRANCO, width=L)
        d.line((cx, cy, cx + k * 0.5, cy + k * 0.2), fill=BRANCO, width=L)
    elif tipo == "user":
        d.ellipse((cx - k * 0.4, cy - k * 0.95, cx + k * 0.4, cy - k * 0.15), fill=BRANCO)
        d.pieslice((cx - k * 0.9, cy - k * 0.05, cx + k * 0.9, cy + k * 1.6), 180, 360, fill=BRANCO)
    elif tipo == "alert":
        d.polygon([(cx, cy - k), (cx - k * 1.05, cy + k * 0.85), (cx + k * 1.05, cy + k * 0.85)], fill=BRANCO)
        d.rectangle((cx - L / 2, cy - k * 0.35, cx + L / 2, cy + k * 0.25), fill=cor)
        d.ellipse((cx - L / 2, cy + k * 0.4, cx + L / 2, cy + k * 0.4 + L), fill=cor)
    elif tipo == "chat":
        d.rounded_rectangle((cx - k, cy - k * 0.8, cx + k, cy + k * 0.45), 10, fill=BRANCO)
        d.polygon([(cx - k * 0.4, cy + k * 0.4), (cx - k * 0.7, cy + k * 1.0), (cx + k * 0.1, cy + k * 0.4)], fill=BRANCO)
        for dx in (-k * 0.5, 0, k * 0.5):
            d.ellipse((cx + dx - L, cy - k * 0.2 - L, cx + dx + L, cy - k * 0.2 + L), fill=cor)
    else:  # spark: estrela de 4 pontas
        d.polygon([(cx, cy - k * 1.2), (cx + k * 0.3, cy - k * 0.3), (cx + k * 1.2, cy), (cx + k * 0.3, cy + k * 0.3),
                   (cx, cy + k * 1.2), (cx - k * 0.3, cy + k * 0.3), (cx - k * 1.2, cy), (cx - k * 0.3, cy - k * 0.3)],
                  fill=BRANCO)


def _quebrar(texto: str, fonte, largura: float) -> list[str]:
    linhas, atual = [], ""
    for palavra in texto.split():
        tentativa = (atual + " " + palavra).strip()
        if fonte.getlength(tentativa) <= largura or not atual:
            atual = tentativa
        else:
            linhas.append(atual)
            atual = palavra
    linhas.append(atual)
    return linhas[:3]


def desenhar_cartao(caminho: Path, item: dict, k: float) -> tuple[int, int]:
    from PIL import Image, ImageDraw
    pad, isz, maxw = int(26 * k), int(44 * k), int(700 * k)
    f_rot = _fonte(int(24 * k))
    util = maxw - (pad * 3 + isz * 2)
    if item["tipo"] == "cta":
        f_txt = _fonte(int(60 * k))
        linhas = [item["texto"]]
        largura = max(f_txt.getlength(item["texto"]) + 2 * pad, f_rot.getlength(item["rotulo"]) + isz * 2 + 3 * pad)
        w = int(min(max(largura + 2 * pad, 560 * k), maxw))
        r_icone, pill = isz * 0.9, 90 * k
        h = int(pad * 2 + r_icone * 2 + 18 * k + pill)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), int(36 * k), fill=NAVY + (240,))
        d.rounded_rectangle((0, 0, int(14 * k), h - 1), int(7 * k), fill=item["cor"] + (255,))
        cy = pad + r_icone
        _icone(d, item["icone"], pad + r_icone + 6 * k, cy, r_icone, item["cor"])
        f_rot_cta = _fonte(int(32 * k))
        d.text((pad * 2 + r_icone * 2 + 6 * k, cy - f_rot_cta.size * 0.62), item["rotulo"], font=f_rot_cta, fill=SUAVE)
        topo = h - pad - pill
        d.rounded_rectangle((pad, topo, w - pad, h - pad), int(pill / 2), fill=item["cor"] + (255,))
        d.text(((w - f_txt.getlength(item["texto"])) / 2, topo + (pill - f_txt.size) / 2 - 6 * k), item["texto"],
               font=f_txt, fill=BRANCO)
        img.save(caminho)
        return w, h

    tamanho = 46 if item["tipo"] != "numero" else 64
    f_txt = _fonte(int(tamanho * k))
    linhas = _quebrar(item["texto"], f_txt, util)
    while len(linhas) > 2 and tamanho > 32:
        tamanho -= 4
        f_txt = _fonte(int(tamanho * k))
        linhas = _quebrar(item["texto"], f_txt, util)
    larg_txt = max(max(f_txt.getlength(l) for l in linhas), f_rot.getlength(item["rotulo"]))
    lh = f_txt.size + int(6 * k)
    w = int(pad * 3 + isz * 2 + larg_txt)
    h = int(pad * 2 + 30 * k + lh * len(linhas))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), int(30 * k), fill=NAVY + (238,))
    d.rounded_rectangle((0, 0, int(14 * k), h - 1), int(7 * k), fill=item["cor"] + (255,))
    _icone(d, item["icone"], pad + isz + 6 * k, h / 2, isz, item["cor"])
    x = pad * 2 + isz * 2
    d.text((x, pad - 2 * k), item["rotulo"], font=f_rot, fill=SUAVE)
    for i, linha in enumerate(linhas):
        d.text((x, pad + 28 * k + i * lh), linha, font=f_txt, fill=BRANCO)
    img.save(caminho)
    return w, h


def aplicar(video: Path, saida: Path, itens: list[dict], cfg: dict, pasta: Path, duracao: float,
            progresso=lambda f: None) -> None:
    """Sobrepõe os cartões em `video` e grava `saida` (o áudio é copiado sem refazer)."""
    largura, altura = cfg["saida"]["largura"], cfg["saida"]["altura"]
    k = largura / LARGURA_BASE
    destino = pasta / "graficos"
    destino.mkdir(parents=True, exist_ok=True)
    entradas, filtros, anterior = ["-i", str(video)], [], "0:v"
    for i, item in enumerate(itens):
        png = destino / f"cartao_{i:03d}.png"
        w, h = desenhar_cartao(png, item, k)
        s, e = item["inicio"], item["fim"]
        x = int((largura - w) / 2) if item["tipo"] == "cta" else int(30 * k)
        y = int(altura * (0.60 if item["tipo"] == "cta" else 0.66))
        entradas += ["-loop", "1", "-framerate", "30", "-t", f"{e - s:.2f}", "-i", str(png)]
        filtros.append(f"[{i + 1}:v]format=rgba,fade=t=in:st=0:d=0.3:alpha=1,fade=t=out:st={e - s - 0.3:.2f}:d=0.3:alpha=1,"
                       f"setpts=PTS-STARTPTS+{s:.3f}/TB[o{i}]")
        rotulo = f"[b{i}]"
        filtros.append(f"[{anterior}][o{i}]overlay=x={x}:y='{y}+{int(100 * k)}*pow(max(0,1-(t-{s:.3f})/0.4),2)':"
                       f"eof_action=pass:enable='between(t,{s:.3f},{e:.3f})'{rotulo}")
        anterior = f"b{i}"
    s = cfg["saida"]
    ffmpeg(entradas + ["-filter_complex", ";".join(filtros), "-map", f"[{anterior}]", "-map", "0:a:0",
                       "-c:v", "libx264", "-preset", s["preset"], "-crf", str(s["crf"]), "-pix_fmt", "yuv420p",
                       "-profile:v", "high", "-c:a", "copy", "-movflags", "+faststart", str(saida)],
           "aplicar os emotion graphics", duracao=duracao, progresso=progresso)
