"""
OpenMeshVPN — Módulo: File Share
==================================
Servidor HTTP leve para transferência de arquivos dentro da rede Mesh.

Funcionalidades:
  - Listar arquivos compartilhados (GET /files)
  - Download de arquivos (GET /download/<filename>)
  - Upload de arquivos (POST /upload)
  - Pasta compartilhada configurável (~\OpenMeshShare)
  - CORS habilitado para acesso da UI
"""

import threading
import os
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

SHARE_PORT = 9876
SHARE_DIR = os.path.join(os.path.expanduser("~"), "OpenMeshShare")

_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None


class _FileShareHandler(BaseHTTPRequestHandler):
    """Handler HTTP para o servidor de compartilhamento de arquivos."""

    def log_message(self, format, *args):
        # Substitui o log padrão do HTTPServer pelo nosso prefixo
        print(f"[File Share] {format % args}")

    # ────────────────────────────────────────────────────────
    #  CORS — necessário para a UI (index.html) acessar a API
    # ────────────────────────────────────────────────────────
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-File-Size")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    # ─────────────────────────────
    #  GET — Listar e baixar
    # ─────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/files":
            self._handle_list_files()
        elif path.startswith("/download/"):
            filename = unquote(path[len("/download/"):])
            self._handle_download(filename)
        elif path == "/info":
            self._respond_json({"port": SHARE_PORT, "dir": SHARE_DIR})
        else:
            self.send_error(404, "Rota não encontrada")

    def _handle_list_files(self):
        """Lista todos os arquivos na pasta compartilhada."""
        files = []
        if os.path.exists(SHARE_DIR):
            for entry in os.scandir(SHARE_DIR):
                if entry.is_file():
                    files.append({
                        "name": entry.name,
                        "size": entry.stat().st_size,
                        "size_human": _human_size(entry.stat().st_size),
                        "modified": int(entry.stat().st_mtime),
                        "url": f"/download/{entry.name}",
                    })
        files.sort(key=lambda x: x["modified"], reverse=True)
        self._respond_json(files)

    def _handle_download(self, filename: str):
        """Envia um arquivo para download."""
        # Proteção contra path traversal (../../etc/passwd)
        safe_path = os.path.realpath(os.path.join(SHARE_DIR, filename))
        if not safe_path.startswith(os.path.realpath(SHARE_DIR)):
            self.send_error(403, "Acesso negado")
            return

        if not os.path.isfile(safe_path):
            self.send_error(404, "Arquivo não encontrado")
            return

        file_size = os.path.getsize(safe_path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(file_size))
        self._send_cors_headers()
        self.end_headers()

        # Envia em chunks de 64KB para não travar memória com arquivos grandes
        with open(safe_path, "rb") as f:
            while chunk := f.read(65536):
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break  # Cliente cancelou o download

        print(f"[File Share] 📥 Download: {filename} ({_human_size(file_size)})")

    # ─────────────────────────────
    #  POST — Upload
    # ─────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/upload":
            self._handle_upload()
        else:
            self.send_error(404, "Rota não encontrada")

    def _handle_upload(self):
        """Recebe um arquivo enviado pela UI ou por outro nó da rede."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Sem conteúdo")
            return

        # O nome do arquivo vem no header X-Filename
        filename = unquote(self.headers.get("X-Filename", "arquivo_sem_nome"))
        # Sanitiza o nome: remove separadores de diretório
        filename = os.path.basename(filename)

        os.makedirs(SHARE_DIR, exist_ok=True)
        dest_path = os.path.join(SHARE_DIR, filename)

        # Se já existir, adiciona sufixo numérico
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(SHARE_DIR, f"{base}_{counter}{ext}")
                counter += 1
            filename = os.path.basename(dest_path)

        received = 0
        with open(dest_path, "wb") as f:
            remaining = content_length
            while remaining > 0:
                chunk_size = min(remaining, 65536)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                remaining -= len(chunk)

        print(f"[File Share] 📤 Upload recebido: {filename} ({_human_size(received)})")
        self._respond_json({
            "status": "ok",
            "file": filename,
            "size": received,
            "path": dest_path,
        })

    # ─────────────────────────────
    #  Helpers
    # ─────────────────────────────
    def _respond_json(self, data: dict | list):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)


def _human_size(size_bytes: int) -> str:
    """Converte bytes em representação legível."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ─────────────────────────────────────────────
#  Interface pública do módulo
# ─────────────────────────────────────────────

def init_module() -> bool:
    """Inicia o servidor HTTP de compartilhamento de arquivos."""
    global _server, _server_thread

    if _server is not None:
        print("[File Share] Servidor já está em execução.")
        return True

    print("[File Share] ═══════════════════════════════")
    print(f"[File Share] 📁 Iniciando servidor na porta {SHARE_PORT}...")

    os.makedirs(SHARE_DIR, exist_ok=True)
    print(f"[File Share] Pasta compartilhada: {SHARE_DIR}")

    try:
        _server = HTTPServer(("0.0.0.0", SHARE_PORT), _FileShareHandler)
    except OSError as e:
        print(f"[File Share] ❌ Erro ao iniciar servidor: {e}")
        print(f"[File Share]    Verifique se a porta {SHARE_PORT} não está em uso.")
        return False

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()

    print(f"[File Share] ✅ Servidor ativo! Acesse de outros nós em:")
    print(f"[File Share]    http://<SEU_IP_MESH>:{SHARE_PORT}/files")
    return True


def stop_module() -> bool:
    """Para o servidor HTTP."""
    global _server, _server_thread

    if _server is None:
        return True

    print("[File Share] Desligando servidor...")
    _server.shutdown()
    _server = None
    _server_thread = None
    print("[File Share] ✅ Servidor desligado.")
    return True


def get_status() -> dict:
    """Retorna informações do módulo para a API."""
    return {
        "active": _server is not None,
        "port": SHARE_PORT,
        "share_dir": SHARE_DIR,
        "file_count": len(os.listdir(SHARE_DIR)) if os.path.exists(SHARE_DIR) else 0,
    }
