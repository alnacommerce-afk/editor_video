"""Teste real do pipeline: processa os vídeos de testes/videos e confere o resultado.

    python testes/gerar_videos_teste.py foto.png   (uma vez)
    python testes/testar_pipeline.py

Verifica: transcrição, pausas, cortes, concatenação, reframing, áudio (loudness/pico), render,
duração final, sincronia (correlação do áudio original × editado) e o MP4 (H.264/AAC, 1080x1920, FPS).
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from editor import config, pipeline  # noqa: E402
from editor.ferramentas import binario  # noqa: E402
from editor.video_input import analisar  # noqa: E402

PASTA = RAIZ / "testes" / "videos"
ESPERADO = {
    "muitas_pausas_horizontal.mp4": {"enquadramento": "smart", "min_removido": 8, "repeticoes": 1},
    "poucas_pausas_vertical.mp4": {"enquadramento": "ja_vertical", "max_removido": 1.0},
    "sem_rosto_horizontal.mp4": {"enquadramento": "sem_rosto_desfoque", "max_removido": 1.0},
}


def pcm(arquivo: Path, ini: float, dur: float) -> np.ndarray:
    cmd = [binario("ffmpeg"), "-loglevel", "error", "-ss", f"{ini:.3f}", "-t", f"{dur:.3f}", "-i", str(arquivo),
           "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.int16).astype(np.float32)


def deslocamento_ms(original: Path, editado: Path, ini_orig: float, ini_edit: float) -> float:
    """Compara 2 s de fala no original e no editado; 0 ms = áudio no lugar certo."""
    folga = 0.3
    ref = pcm(original, ini_orig, 2.0)
    alvo = pcm(editado, max(ini_edit - folga, 0), 2.0 + 2 * folga)
    corr = np.correlate(alvo, ref, "valid")
    return (int(np.argmax(corr)) / 16000 - min(folga, ini_edit)) * 1000


def loudness(arquivo: Path) -> dict:
    proc = subprocess.run([binario("ffmpeg"), "-hide_banner", "-i", str(arquivo), "-af", "ebur128=peak=true", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    resumo = proc.stderr[proc.stderr.rfind("Summary:"):]
    integrado = float(resumo.split("I:")[1].split("LUFS")[0])
    pico = float(resumo.split("Peak:")[1].split("dBFS")[0])
    return {"lufs": integrado, "pico_dbfs": pico}


def main() -> int:
    cfg = config.carregar()
    falhas = 0
    for nome, esperado in ESPERADO.items():
        video = PASTA / nome
        if not video.exists():
            print(f"!! {nome} não existe — rode testes/gerar_videos_teste.py primeiro")
            return 1
        trabalho = RAIZ / "trabalho" / f"teste_{video.stem}"
        r = pipeline.processar(video, trabalho, cfg)
        cortes = json.loads((trabalho / "cortes.json").read_text(encoding="utf-8"))
        saida = Path(r["arquivo"])
        info_orig, info_final = analisar(video), analisar(saida)
        f = r["arquivo_final"]

        # Sincronia: mede o áudio num trecho mantido no meio do vídeo.
        seg = max(cortes["segmentos_mantidos"], key=lambda s: s["fim"] - s["inicio"])
        meio = seg["inicio"] + max((seg["fim"] - seg["inicio"]) / 2 - 1.0, 0)
        desloc = deslocamento_ms(video, saida, meio, seg["no_video_editado"] + (meio - seg["inicio"]))
        som = loudness(saida)

        checagens = {
            "transcrição tem palavras": cortes["analise_audio"]["palavras"] > 5,
            "MP4 H.264 + AAC": f["codec_video"] == "h264" and f["codec_audio"] == "aac" and saida.suffix == ".mp4",
            "resolução 1080x1920": f["resolucao"] == "1080x1920",
            "FPS original preservado": info_final.fps == info_orig.fps,
            f"enquadramento = {esperado['enquadramento']}": r["enquadramento"] == esperado["enquadramento"],
            "vídeo e áudio com mesma duração (<40 ms)": f["diferenca_av_ms"] < 40,
            f"sincronia do áudio ({desloc:+.1f} ms, tolerância 15 ms)": abs(desloc) <= 15,
            "duração final = soma dos trechos": abs(f["duracao"] - sum(s["fim"] - s["inicio"] for s in cortes["segmentos_mantidos"])) < 0.1,
            f"loudness ≈ {cfg['audio']['loudness_alvo']} LUFS ({som['lufs']})": abs(som["lufs"] - cfg["audio"]["loudness_alvo"]) <= 1.5,
            f"sem estourar (pico {som['pico_dbfs']} dBFS)": som["pico_dbfs"] < 0,
        }
        if "min_removido" in esperado:
            checagens[f"removeu ≥ {esperado['min_removido']}s de pausas ({r['tempo_removido']}s)"] = r["tempo_removido"] >= esperado["min_removido"]
        if "max_removido" in esperado:
            checagens[f"fala corrida quase intacta ({r['tempo_removido']}s removidos)"] = r["tempo_removido"] <= esperado["max_removido"]
        if "repeticoes" in esperado:
            checagens["frase recomeçada removida"] = len(r["repeticoes_removidas"]) == esperado["repeticoes"]

        print(f"\n=== {nome}: {r['duracao_original']}s → {r['duracao_final']}s, {r['cortes']} cortes, "
              f"{r['tempo_processamento']}s de processamento")
        for descricao, ok in checagens.items():
            print(f"  [{'OK' if ok else 'FALHOU'}] {descricao}")
            falhas += not ok
    print(f"\n{'Tudo certo' if not falhas else f'{falhas} verificação(ões) falharam'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
