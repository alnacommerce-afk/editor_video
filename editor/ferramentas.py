"""Localiza FFmpeg/ffprobe e executa comandos com mensagens de erro legíveis."""
import glob
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


class ErroFerramenta(RuntimeError):
    pass


def _candidatos(nome: str):
    exe = nome + (".exe" if os.name == "nt" else "")
    if os.environ.get("FFMPEG_DIR"):
        yield Path(os.environ["FFMPEG_DIR"]) / exe
    achado = shutil.which(nome)
    if achado:
        yield Path(achado)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        # winget (Gyan.FFmpeg) — o PATH só é atualizado em terminais novos, então procuramos direto.
        yield Path(local) / "Microsoft" / "WinGet" / "Links" / exe
        padrao = Path(local) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg*" / "*" / "bin" / exe
        for p in sorted(glob.glob(str(padrao)), reverse=True):
            yield Path(p)


@lru_cache
def binario(nome: str) -> str:
    for c in _candidatos(nome):
        if c.is_file():
            return str(c)
    raise ErroFerramenta(f"{nome} não encontrado. Rode instalar.bat ou defina a variável FFMPEG_DIR.")


def _erro(descricao: str, codigo: int, stderr: str) -> ErroFerramenta:
    cauda = "\n".join(stderr.strip().splitlines()[-15:])
    return ErroFerramenta(f"Falha ao {descricao} (código {codigo}):\n{cauda}")


def executar(cmd: list[str], descricao: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise _erro(descricao, proc.returncode, proc.stderr)
    return proc


def ffmpeg(args: list[str], descricao: str, duracao: float | None = None, progresso=None) -> subprocess.CompletedProcess:
    """Roda o ffmpeg. Com `progresso` + `duracao`, chama progresso(fração) conforme o encode avança."""
    cmd = [binario("ffmpeg"), "-hide_banner", "-nostdin", "-y"]
    if progresso is None or not duracao:
        return executar(cmd + args, descricao)

    cmd += ["-progress", "pipe:1", "-nostats"] + args
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace")
        for linha in proc.stdout:
            if linha.startswith("out_time_us="):
                try:
                    progresso(min(int(linha.split("=", 1)[1]) / 1e6 / duracao, 1.0))
                except ValueError:
                    pass
        proc.wait()
        err.seek(0)
        stderr = err.read()
    if proc.returncode != 0:
        raise _erro(descricao, proc.returncode, stderr)
    return subprocess.CompletedProcess(cmd, 0, "", stderr)


def ffprobe(args: list[str], descricao: str) -> str:
    return executar([binario("ffprobe"), "-v", "error", *args], descricao).stdout
