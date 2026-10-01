"""Interface web local (só biblioteca padrão do Python — nenhuma dependência extra).

    python -m editor.server      →  http://127.0.0.1:8765

Os vídeos são processados um de cada vez (fila), porque transcrição e render usam o processador inteiro.
"""
import json
import mimetypes
import queue
import sys
import re
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

from . import config, pipeline
from .video_input import EXTENSOES_ACEITAS

WEB = Path(__file__).resolve().parent.parent / "web"
CFG = config.carregar()
TRABALHO = config.pasta(CFG, "processamento", "pasta_trabalho")

trabalhos: dict[str, dict] = {}
fila: "queue.Queue[str]" = queue.Queue()
trava = threading.Lock()


def _atualizar(id_: str, **campos):
    with trava:
        trabalhos[id_].update(campos)


def _executor():
    while True:
        id_ = fila.get()
        _atualizar(id_, estado="processando", mensagem="Analisando o vídeo")
        t = trabalhos[id_]

        def progresso(etapa, fracao=None, mensagem=None):
            campos = {"etapa": etapa}
            if fracao is not None:
                campos["progresso"] = round(fracao, 3)
            if mensagem:
                campos["mensagem"] = mensagem
            _atualizar(id_, **campos)

        try:
            resultado = pipeline.processar(Path(t["arquivo_entrada"]), TRABALHO / id_, CFG, nome_original=t["nome"],
                                           progresso=progresso)
            _atualizar(id_, estado="pronto", etapa="finalizado", progresso=1.0, resultado=resultado)
        except Exception as erro:
            traceback.print_exc()
            _atualizar(id_, estado="erro", erro=str(erro))
        finally:
            fila.task_done()


class Manipulador(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        pass

    def _json(self, dados, status=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _arquivo(self, caminho: Path, baixar_como: str | None = None):
        if not caminho.is_file():
            return self._json({"erro": "arquivo não encontrado"}, 404)
        tamanho = caminho.stat().st_size
        ini, fim = 0, tamanho - 1
        faixa = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if faixa and (faixa.group(1) or faixa.group(2)):
            if faixa.group(1):
                ini = int(faixa.group(1))
                fim = int(faixa.group(2)) if faixa.group(2) else fim
            else:
                ini = max(tamanho - int(faixa.group(2)), 0)
            fim = min(fim, tamanho - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{tamanho}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(caminho.name)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1))
        if baixar_como:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(baixar_como)}")
        self.end_headers()
        with caminho.open("rb") as f:
            f.seek(ini)
            restante = fim - ini + 1
            try:
                while restante > 0:
                    bloco = f.read(min(1 << 20, restante))
                    if not bloco:
                        break
                    self.wfile.write(bloco)
                    restante -= len(bloco)
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass   # o player do navegador cancela pedidos parciais o tempo todo

    def _trabalho(self, id_: str) -> dict | None:
        with trava:
            t = trabalhos.get(id_)
            return dict(t) if t else None

    def do_GET(self):
        partes = [unquote(p) for p in self.path.split("?")[0].strip("/").split("/")]
        if partes == [""]:
            return self._arquivo(WEB / "index.html")
        if len(partes) == 3 and partes[0] == "api":
            acao, id_ = partes[1], partes[2]
            t = self._trabalho(id_)
            if not t:
                return self._json({"erro": "trabalho não encontrado"}, 404)
            if acao == "status":
                return self._json({k: v for k, v in t.items() if k != "arquivo_entrada"})
            if t.get("estado") != "pronto":
                return self._json({"erro": "o vídeo ainda não está pronto"}, 409)
            final = Path(t["resultado"]["arquivo"])
            if acao == "video":
                return self._arquivo(final)
            if acao == "baixar":
                return self._arquivo(final, baixar_como=final.name)
            if acao == "legenda" and t["resultado"].get("srt"):
                srt = Path(t["resultado"]["srt"])
                return self._arquivo(srt, baixar_como=srt.name)
        self._json({"erro": "não encontrado"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/enviar":
            return self._json({"erro": "não encontrado"}, 404)
        nome = Path(unquote(self.headers.get("X-Nome-Arquivo", "video.mp4"))).name
        extensao = Path(nome).suffix.lower()
        if extensao not in EXTENSOES_ACEITAS:
            return self._json({"erro": f"Formato não suportado ({extensao or 'sem extensão'})."}, 400)
        tamanho = int(self.headers.get("Content-Length", 0))
        if tamanho <= 0:
            return self._json({"erro": "arquivo vazio"}, 400)

        id_ = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        pasta = TRABALHO / id_
        pasta.mkdir(parents=True)
        destino = pasta / f"original{extensao}"
        restante = tamanho
        with destino.open("wb") as f:
            while restante > 0:
                bloco = self.rfile.read(min(1 << 20, restante))
                if not bloco:
                    break
                f.write(bloco)
                restante -= len(bloco)
        if restante:
            return self._json({"erro": "envio interrompido"}, 400)

        with trava:
            trabalhos[id_] = {"id": id_, "nome": nome, "estado": "na_fila", "etapa": "transcrevendo", "progresso": 0.0,
                              "mensagem": "Na fila — outro vídeo está sendo editado" if _ocupado() else "Começando",
                              "arquivo_entrada": str(destino), "etapas": pipeline.ETAPAS}
        fila.put(id_)
        self._json({"id": id_})


def _ocupado() -> bool:
    return any(t["estado"] in ("processando", "na_fila") for t in trabalhos.values())


def main():
    threading.Thread(target=_executor, daemon=True).start()
    host, porta = CFG["servidor"]["host"], CFG["servidor"]["porta"]
    servidor = ThreadingHTTPServer((host, porta), Manipulador)
    url = f"http://{host}:{porta}"
    print(f"Editor de vídeo rodando em {url}  (Ctrl+C para parar)")
    if "--sem-navegador" not in sys.argv:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
