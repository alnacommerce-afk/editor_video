"""Renderização: cada trecho mantido vira um arquivo intermediário, depois tudo é unido num MP4 final.

Por que trecho a trecho:
- cada FFmpeg só decodifica o pedaço que precisa (rápido mesmo com centenas de cortes) e roda em paralelo;
- os trechos começam e terminam na grade de frames, com FPS constante → vídeo e áudio têm a mesma
  duração em todo trecho, então a sincronia não "escorrega" ao longo do vídeo e não há frames congelados;
- o áudio intermediário é PCM (sem compressão), então a emenda é perfeita; o AAC só é gerado uma vez, no fim.
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path

from . import audio_processing
from .ferramentas import ffmpeg, ffprobe
from .video_input import InfoVideo


def fps_saida(info: InfoVideo, cfg: dict) -> str:
    fps = cfg["saida"]["fps"]
    return str(info.fps) if fps == "original" else str(fps)


def duracoes_saida(segmentos, fps: str, fator: float) -> list[float]:
    """Duração de cada trecho no vídeo final: (fim - início) / velocidade, arredondada para frames inteiros.
    Vídeo e áudio de cada trecho são forçados a essa mesma duração — é isso que garante a sincronia."""
    q = Fraction(fps)
    return [float(max(round((b - a) / fator * q), 1) / q) for a, b in segmentos]


def renderizar_trechos(info: InfoVideo, segmentos, duracoes, fator: float, filtros, cfg: dict, pasta: Path,
                       progresso=lambda f: None) -> list[Path]:
    pasta.mkdir(parents=True, exist_ok=True)
    s = cfg["saida"]
    fps = fps_saida(info, cfg)
    arquivos = [pasta / f"trecho_{i:04d}.mkv" for i in range(len(segmentos))]

    def um(i):
        a, b = segmentos[i]
        dur = float(duracoes[i])
        if fator != 1:
            # Acelera e volta para o FPS original arredondando para baixo: o frame fica no máximo meio frame
            # adiantado, o mesmo sentido do atempo (~15 ms adiantado) → boca e voz ficam juntas.
            filtro_v = filtros[i] + f",setpts=(PTS-STARTPTS)/{fator},fps={fps}:round=down"
            modo_fps = ["-fps_mode", "passthrough"]
        else:
            filtro_v = filtros[i]
            modo_fps = ["-fps_mode", "cfr", "-r", fps]
        filtro_a = (f"atempo={fator}," if fator != 1 else "") + audio_processing.fades(dur, cfg) + f",apad=whole_dur={dur:.6f}"
        ffmpeg(["-ss", f"{a:.6f}", "-t", f"{b - a + 0.5:.6f}", "-i", str(info.caminho), "-t", f"{dur:.6f}",
                "-map", "0:v:0", "-map", "0:a:0",
                "-filter:v", filtro_v, *modo_fps,
                # Número exato de frames: sem isso o modo CFR às vezes duplica o último frame (+33 ms por trecho).
                "-frames:v", str(round(dur * Fraction(fps))),
                "-c:v", "libx264", "-preset", s["preset"], "-crf", str(s["crf"]), "-pix_fmt", "yuv420p",
                "-profile:v", "high",
                "-filter:a", filtro_a, "-c:a", "pcm_s16le",
                "-ar", str(cfg["audio"]["taxa_amostragem"]), "-ac", "2",
                str(arquivos[i])], f"renderizar o trecho {i + 1}")

    feitos = 0
    with ThreadPoolExecutor(max_workers=max(1, cfg["processamento"]["renders_paralelos"])) as executor:
        for futuro in as_completed([executor.submit(um, i) for i in range(len(segmentos))]):
            futuro.result()
            feitos += 1
            progresso(feitos / len(segmentos))
    return arquivos


def finalizar(arquivos: list[Path], saida: Path, duracao: float, cfg: dict, pasta: Path, progresso=lambda f: None) -> dict:
    lista = pasta / "lista_trechos.txt"
    lista.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in arquivos), encoding="utf-8")
    entrada = ["-f", "concat", "-safe", "0", "-i", str(lista)]

    medicao = None
    if cfg["audio"]["normalizar"]:
        proc = ffmpeg(entrada + ["-vn", "-filter:a", audio_processing.filtro_medicao(cfg), "-f", "null", "-"],
                      "medir o volume do áudio")
        medicao = audio_processing.ler_medicao(proc.stderr)
        progresso(0.15)

    ffmpeg(entrada + ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
                      "-filter:a", audio_processing.filtro_final(cfg, medicao, duracao),
                      "-c:a", "aac", "-b:a", cfg["audio"]["bitrate"],
                      "-movflags", "+faststart", str(saida)],
           "gerar o MP4 final", duracao=duracao, progresso=lambda f: progresso(0.15 + 0.85 * f))
    return {"loudness_original": medicao}


def verificar(saida: Path) -> dict:
    """Confere o arquivo final: duração de vídeo e áudio (sincronia), resolução e codecs."""
    dados = json.loads(ffprobe(["-print_format", "json", "-show_format", "-show_streams", str(saida)],
                               "verificar o vídeo final"))
    v = next(s for s in dados["streams"] if s["codec_type"] == "video")
    a = next(s for s in dados["streams"] if s["codec_type"] == "audio")
    dv, da = float(v.get("duration", 0)), float(a.get("duration", 0))
    return {"duracao": round(float(dados["format"]["duration"]), 3), "duracao_video": round(dv, 3),
            "duracao_audio": round(da, 3), "diferenca_av_ms": round(abs(dv - da) * 1000, 1),
            "sincronizado": abs(dv - da) < 0.1, "resolucao": f"{v['width']}x{v['height']}",
            "fps": v.get("avg_frame_rate"), "codec_video": v["codec_name"], "codec_audio": a["codec_name"],
            "tamanho_mb": round(saida.stat().st_size / 1e6, 2)}
