"""Arquivos de saída: cortes.json (auditoria), legendas .srt e o resumo mostrado na interface."""
import json
from pathlib import Path


def salvar_json(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def mapa_tempo(segmentos):
    """Converte um instante do vídeo original para o instante no vídeo editado (None se foi cortado)."""
    deslocamentos, acumulado = [], 0.0
    for a, b in segmentos:
        deslocamentos.append((a, b, acumulado))
        acumulado += b - a

    def converter(t: float):
        for a, b, d in deslocamentos:
            if a <= t <= b:
                return d + (t - a)
        return None
    return converter


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def gerar_srt(palavras: list[dict], segmentos, cfg: dict) -> str:
    """Legenda do vídeo JÁ EDITADO (tempos recalculados). Pronta para queimar no vídeo no futuro."""
    leg = cfg["legendas"]
    converter = mapa_tempo(segmentos)
    blocos, atual = [], []

    def fechar():
        if atual:
            blocos.append((atual[0][0], atual[-1][1], " ".join(w for _, _, w in atual)))
            atual.clear()

    for p in palavras:
        ini, fim = converter(p["inicio"]), converter(p["fim"])
        if ini is None or fim is None:
            continue   # palavra removida (hesitação/repetição)
        texto = " ".join([w for _, _, w in atual] + [p["palavra"]])
        if atual and (len(texto) > leg["max_caracteres"] or fim - atual[0][0] > leg["max_duracao"] or ini - atual[-1][1] > 0.6):
            fechar()
        atual.append((ini, fim, p["palavra"]))
        if p["palavra"].endswith((".", "!", "?")):
            fechar()
    fechar()
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(blocos, 1))


def resumo(info, segmentos, plano: dict, verificacao: dict, enquadramento: dict) -> dict:
    final = sum(b - a for a, b in segmentos)
    por_motivo = {}
    for c in plano["cortes"]:
        chave = c["motivo"].split("_")[0] if c["motivo"].startswith("pausa_") else c["motivo"]
        por_motivo[chave] = por_motivo.get(chave, 0) + 1
    return {
        "duracao_original": round(info.duracao, 2),
        "duracao_final": round(verificacao.get("duracao", final), 2),
        "tempo_removido": round(info.duracao - final, 2),
        "percentual_removido": round(100 * (info.duracao - final) / info.duracao, 1) if info.duracao else 0,
        "cortes": len(plano["cortes"]),
        "cortes_por_motivo": por_motivo,
        "hesitacoes_removidas": [h["texto"] for h in plano["hesitacoes"]],
        "repeticoes_removidas": [r["texto"] for r in plano["repeticoes"]],
        "enquadramento": enquadramento["modo"],
        "avisos": plano["avisos"],
        "arquivo_final": verificacao,
    }
