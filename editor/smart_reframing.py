"""Smart reframing: transforma o vídeo em 9:16 mantendo o rosto de quem fala dentro do quadro.

Como funciona:
1. O FFmpeg entrega frames reduzidos (640 px, tons de cinza) a cada `intervalo_amostras` segundos.
2. O OpenCV (classificador Haar, que já vem no pacote) acha o rosto em cada amostra.
3. Para cada trecho mantido, calcula a posição do recorte:
   - se o rosto quase não se mexe no trecho → recorte fixo (o mais comum, e o mais "humano");
   - se a pessoa anda pelo quadro → acompanha com movimento suave, ignorando tremidas pequenas.
4. Se não houver rosto confiável no vídeo inteiro, não arrisca um recorte cego: usa fundo desfocado
   (o quadro inteiro aparece) ou recorte central, conforme `sem_rosto`.

Vídeo vertical já em 9:16 é só redimensionado — o enquadramento original é preservado.
"""
import statistics

import numpy as np

from .ferramentas import binario
from .video_input import InfoVideo


def _par(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def _proporcao(cfg: dict) -> float:
    return cfg["saida"]["largura"] / cfg["saida"]["altura"]


def detectar_rostos(info: InfoVideo, cfg: dict, progresso=lambda f: None) -> list[tuple[float, tuple | None]]:
    import subprocess

    import cv2

    passo = cfg["enquadramento"]["intervalo_amostras"]
    escala = 640 / max(info.largura, info.altura)
    aw, ah = _par(info.largura * escala), _par(info.altura * escala)
    cmd = [binario("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(info.caminho), "-an",
           "-vf", f"fps=1/{passo},scale={aw}:{ah}", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    classificador = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    minimo = int(min(aw, ah) * 0.07)
    total = max(int(info.duracao / passo), 1)
    tamanho = aw * ah

    amostras, anterior = [], None
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    k = 0
    while True:
        bruto = proc.stdout.read(tamanho)
        if len(bruto) < tamanho:
            break
        img = np.frombuffer(bruto, dtype=np.uint8).reshape(ah, aw)
        rostos = classificador.detectMultiScale(img, scaleFactor=1.1, minNeighbors=5, minSize=(minimo, minimo))
        if not len(rostos):
            # Vídeo escuro/contraluz: tenta de novo com histograma equalizado.
            rostos = classificador.detectMultiScale(cv2.equalizeHist(img), scaleFactor=1.1, minNeighbors=5,
                                                    minSize=(minimo, minimo))
        caixa = None
        if len(rostos):
            def pontuacao(r):
                x, y, w, h = r
                area = w * h
                if anterior is None:
                    return area
                # Prefere o rosto grande e perto de onde a pessoa estava (evita pular para um rosto no fundo).
                dist = abs((x + w / 2) - anterior[0]) + abs((y + h / 2) - anterior[1])
                return area / (1 + dist / (aw * 0.1))
            x, y, w, h = max(rostos, key=pontuacao)
            anterior = (x + w / 2, y + h / 2)
            caixa = (x / escala, y / escala, w / escala, h / escala)
        amostras.append((k * passo, caixa))
        k += 1
        if k % 10 == 0:
            progresso(min(k / total, 1.0))
    proc.wait()
    progresso(1.0)
    return amostras


def _suavizar(amostras):
    """Remove detecções isoladas fora da curva (falsos positivos) com mediana móvel do centro."""
    centros = [(t, (c[0] + c[2] / 2, c[1] + c[3] / 2, c[2], c[3])) for t, c in amostras if c]
    if len(centros) < 3:
        return centros
    resultado = []
    for i, (t, (cx, cy, w, h)) in enumerate(centros):
        janela = centros[max(0, i - 2):i + 3]
        mx = statistics.median(c[1][0] for c in janela)
        my = statistics.median(c[1][1] for c in janela)
        if abs(cx - mx) > w * 1.5 or abs(cy - my) > h * 1.5:
            continue
        resultado.append((t, (cx, cy, w, h)))
    return resultado


def _expressao(chaves: list[tuple[float, float]]) -> str:
    """Interpolação linear por partes como expressão do FFmpeg (t = tempo dentro do trecho)."""
    expr = f"{chaves[-1][1]:.1f}"
    for (t0, p0), (t1, p1) in reversed(list(zip(chaves, chaves[1:]))):
        expr = f"if(lt(t,{t1:.3f}),{p0:.1f}+({p1 - p0:.1f})*(t-{t0:.3f})/{max(t1 - t0, 0.001):.3f},{expr})"
    return f"if(lt(t,{chaves[0][0]:.3f}),{chaves[0][1]:.1f},{expr})"


def _filtro_escala(cfg) -> str:
    return f"scale={cfg['saida']['largura']}:{cfg['saida']['altura']}:flags=lanczos,setsar=1"


def _filtro_encaixe(cfg) -> str:
    ow, oh = cfg["saida"]["largura"], cfg["saida"]["altura"]
    return (f"scale={ow}:{oh}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={ow}:{oh}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")


def _filtro_desfoque(cfg) -> str:
    ow, oh = cfg["saida"]["largura"], cfg["saida"]["altura"]
    return (f"split=2[fundo][frente];"
            f"[fundo]scale={ow // 4}:{oh // 4}:force_original_aspect_ratio=increase,crop={ow // 4}:{oh // 4},"
            f"boxblur=10:2,eq=brightness=-0.06,scale={ow}:{oh}[fundo2];"
            f"[frente]scale={ow}:{oh}:force_original_aspect_ratio=decrease:flags=lanczos[frente2];"
            f"[fundo2][frente2]overlay=(W-w)/2:(H-h)/2,setsar=1")


def planejar(info: InfoVideo, segmentos: list[tuple[float, float]], cfg: dict, progresso=lambda f: None) -> dict:
    e = cfg["enquadramento"]
    alvo = _proporcao(cfg)

    if not e["ativo"]:
        # Sem reframing: mantém a proporção original, só padroniza para a resolução de saída.
        return {"modo": "desativado", "filtros": [_filtro_encaixe(cfg)] * len(segmentos), "detalhes": []}
    if info.ja_esta_na_proporcao(alvo):
        progresso(1.0)
        return {"modo": "ja_vertical", "filtros": [_filtro_encaixe(cfg)] * len(segmentos), "detalhes": []}

    sem_rosto = {"desfoque": _filtro_desfoque(cfg), "centro": None, "barras": _filtro_encaixe(cfg)}[e["sem_rosto"]]
    W, H = info.largura, info.altura
    if W / H > alvo:
        cw, ch, eixo = _par(H * alvo), _par(H), "x"
        if cw > W:
            cw = _par(W)
    else:
        cw, ch, eixo = _par(W), _par(W / alvo), "y"
        if ch > H:
            ch = _par(H)
    limite = (W - cw) if eixo == "x" else (H - ch)

    def recorte_fixo(pos):
        x, y = (pos, 0) if eixo == "x" else (0, pos)
        return f"crop={cw}:{ch}:{x:.0f}:{y:.0f}," + _filtro_escala(cfg)

    if e["modo"] == "centro":
        return {"modo": "centro", "filtros": [recorte_fixo(limite / 2)] * len(segmentos), "detalhes": []}

    amostras = detectar_rostos(info, cfg, progresso)
    rostos = _suavizar(amostras)
    presenca = len(rostos) / max(len(amostras), 1)
    if presenca < e["presenca_minima_rosto"]:
        filtro = sem_rosto or recorte_fixo(limite / 2)
        return {"modo": f"sem_rosto_{e['sem_rosto']}", "presenca_rosto": round(presenca, 3),
                "filtros": [filtro] * len(segmentos), "detalhes": []}

    def posicao(rosto):
        cx, cy, w, h = rosto
        if eixo == "x":
            # Rosto centralizado; como o recorte usa a altura inteira, cabeça e ombros nunca são cortados.
            p = cx - cw / 2
        else:
            # Rosto no terço superior, mas sempre com o topo da cabeça (≈ meia caixa acima do rosto) + folga dentro.
            p = cy - ch * e["posicao_vertical_rosto"]
            p = min(p, cy - h / 2 - h * (0.5 + e["margem_rosto"]))
        return float(min(max(p, 0), limite))

    todas = [posicao(r) for _, r in rostos]
    padrao = statistics.median(todas)
    zona_morta = e["zona_morta"] * (cw if eixo == "x" else ch)

    filtros, detalhes = [], []
    for a, b in segmentos:
        locais = [(t - a, posicao(r)) for t, r in rostos if a - 0.25 <= t <= b + 0.25]
        if not locais:
            pos = padrao
            # Usa o rosto mais próximo no tempo, se houver, em vez da mediana geral.
            proximos = sorted(rostos, key=lambda tr: min(abs(tr[0] - a), abs(tr[0] - b)))
            if proximos and min(abs(proximos[0][0] - a), abs(proximos[0][0] - b)) < 3:
                pos = posicao(proximos[0][1])
            filtros.append(recorte_fixo(pos))
            detalhes.append({"inicio": round(a, 3), "fim": round(b, 3), "tipo": "fixo_estimado", "posicao": round(pos)})
            continue

        valores = [p for _, p in locais]
        if max(valores) - min(valores) <= 2 * zona_morta:
            pos = statistics.median(valores)
            filtros.append(recorte_fixo(pos))
            detalhes.append({"inicio": round(a, 3), "fim": round(b, 3), "tipo": "fixo", "posicao": round(pos)})
            continue

        # A pessoa se desloca: segue com pan suave, só quando sai da zona morta.
        atual = statistics.median(valores[:3])
        chaves = [(0.0, atual)]
        for i in range(1, len(locais)):
            t, p = locais[i]
            if abs(p - atual) > zona_morta:
                chaves.append((max(locais[i - 1][0], chaves[-1][0] + 0.001), atual))
                atual = p
                chaves.append((max(t, chaves[-1][0] + 0.001), atual))
        expr = _expressao(chaves)
        x, y = (f"'{expr}'", "0") if eixo == "x" else ("0", f"'{expr}'")
        filtros.append(f"crop={cw}:{ch}:{x}:{y}," + _filtro_escala(cfg))
        detalhes.append({"inicio": round(a, 3), "fim": round(b, 3), "tipo": "acompanhando", "movimentos": len(chaves) // 2})

    return {"modo": "smart", "presenca_rosto": round(presenca, 3), "recorte": [cw, ch], "eixo": eixo,
            "filtros": filtros, "detalhes": detalhes}
