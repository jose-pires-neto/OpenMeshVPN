"""
OpenMeshVPN — Headless Flask API (Windows)
============================================
Ponto de entrada do backend Python.
Expõe uma API REST local (127.0.0.1:8080) consumida pela UI Electron.

Endpoints:
  GET  /api/status           → Estado atual da VPN e da rede
  POST /api/hospedar         → Inicia como Host (cria o Matchmaker local)
  POST /api/conectar         → Conecta na rede de um amigo
  POST /api/desconectar      → Desconecta a VPN
  GET  /api/modules          → Lista módulos disponíveis (auto-descoberta)
  POST /api/modules/toggle   → Ativa/desativa um módulo
  GET  /api/network/peers    → Lista peers online da rede
  GET  /api/network/info     → Informações da rede (token, etc.)
"""

import os
import sys
import json
import time
import threading
import importlib

from flask import Flask, jsonify, request
from flask_cors import CORS

# Adiciona o diretório pai ao path para encontrar a pasta 'modules'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core_vpn import OpenMeshEngine
from signaling_server import SignalingServer

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
#  Estado global
# ─────────────────────────────────────────────

vpn_engine: OpenMeshEngine | None = None
local_server: SignalingServer | None = None
PORTA_MATCHMAKER = 5000

# Estado persistente dos módulos (carregado/salvo em config.json)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
_module_states: dict[str, bool] = {}
_loaded_modules: dict[str, object] = {}  # módulos já importados

# ─────────────────────────────────────────────
#  Persistência de configuração
# ─────────────────────────────────────────────

def load_config():
    global _module_states
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _module_states = data.get("module_states", {})
    except Exception as e:
        print(f"[API] Aviso: não foi possível carregar config.json: {e}")


def save_config():
    try:
        data = {"module_states": _module_states, "updated_at": int(time.time())}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[API] Aviso: não foi possível salvar config.json: {e}")


# ─────────────────────────────────────────────
#  Auto-descoberta de módulos
# ─────────────────────────────────────────────

MODULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "modules")

# Metadados estáticos dos módulos conhecidos
MODULE_METADATA = {
    "game_boost": {
        "name": "Game Boost",
        "description": "Prioriza pacotes UDP, otimiza QoS e monitora jogos em execução para redução máxima de latência.",
        "icon": "🎮",
        "category": "performance",
        "requires_admin": True,
    },
    "file_share": {
        "name": "File Sharing Node",
        "description": "Servidor HTTP embutido para transferência de arquivos entre nós da rede Mesh.",
        "icon": "📁",
        "category": "tools",
        "requires_admin": False,
    },
    "corp_route": {
        "name": "Corporate Subnet",
        "description": "Roteia sub-redes corporativas pela Mesh para acesso remoto seguro.",
        "icon": "🏢",
        "category": "network",
        "requires_admin": True,
    },
}


def _discover_modules() -> list[dict]:
    """Escaneia a pasta modules/ e retorna lista de módulos disponíveis."""
    modules = []
    if not os.path.isdir(MODULES_DIR):
        return modules

    for filename in sorted(os.listdir(MODULES_DIR)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue

        mod_id = filename[:-3]  # remove .py
        meta = MODULE_METADATA.get(mod_id, {
            "name": mod_id.replace("_", " ").title(),
            "description": "Módulo personalizado.",
            "icon": "📦",
            "category": "custom",
            "requires_admin": False,
        })

        # Tentar obter status dinâmico do módulo (se já importado)
        extra = {}
        if mod_id in _loaded_modules:
            mod = _loaded_modules[mod_id]
            if hasattr(mod, "get_status"):
                try:
                    extra = mod.get_status()
                except Exception:
                    pass

        modules.append({
            "id": mod_id,
            "name": meta["name"],
            "description": meta["description"],
            "icon": meta["icon"],
            "category": meta.get("category", "custom"),
            "requires_admin": meta.get("requires_admin", False),
            "active": _module_states.get(mod_id, False),
            **extra,
        })

    return modules


def _load_and_call_module(mod_id: str, action: str) -> bool:
    """
    Importa o módulo dinamicamente e chama init_module() ou stop_module().
    Usa cache para não reimportar módulos já carregados.
    """
    try:
        if mod_id not in _loaded_modules:
            mod = importlib.import_module(f"modules.{mod_id}")
            _loaded_modules[mod_id] = mod
        else:
            # Recarregar para pegar mudanças em desenvolvimento
            mod = importlib.reload(_loaded_modules[mod_id])
            _loaded_modules[mod_id] = mod

        fn = getattr(mod, action, None)
        if fn is None:
            print(f"[API] Módulo '{mod_id}' não tem função '{action}'")
            return False

        return bool(fn())

    except ModuleNotFoundError:
        print(f"[API] ❌ Módulo '{mod_id}' não encontrado em {MODULES_DIR}")
        return False
    except Exception as e:
        print(f"[API] ❌ Erro ao executar {action} no módulo '{mod_id}': {e}")
        return False


# ─────────────────────────────────────────────
#  Endpoints da API
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({
        "status": "OpenMeshVPN API Running",
        "version": "1.1.0",
        "endpoints": [
            "/api/status", "/api/hospedar", "/api/conectar",
            "/api/desconectar", "/api/modules", "/api/modules/toggle",
            "/api/network/peers", "/api/network/info",
        ],
    })


@app.route("/api/status", methods=["GET"])
def get_status():
    if not vpn_engine:
        return jsonify({"status": "desligado", "logs": [], "vpn_ip": None})

    return jsonify({
        "status": "conectado" if vpn_engine.conectado else "online",
        "meu_nome": vpn_engine.my_name,
        "vpn_ip": vpn_engine.vpn_ip,
        "peer": (
            f"{vpn_engine.peer_address[0]}:{vpn_engine.peer_address[1]}"
            if vpn_engine.peer_address else "Nenhum"
        ),
        "logs": vpn_engine.logs[-15:],
        "is_host": local_server is not None,
        "encrypted": vpn_engine._chacha is not None,
    })


@app.route("/api/hospedar", methods=["POST"])
def hospedar_rede():
    """Modo Host: inicia o Matchmaker local e registra o próprio PC."""
    global vpn_engine, local_server

    dados = request.get_json(force=True, silent=True) or {}
    nome_pc = (dados.get("nome_pc") or "").strip()
    if not nome_pc:
        return jsonify({"erro": "nome_pc é obrigatório"}), 400

    # Encerrar instância anterior se existir
    if vpn_engine:
        vpn_engine.desconectar()

    # Iniciar Matchmaker local
    if not local_server:
        local_server = SignalingServer(port=PORTA_MATCHMAKER)
        local_server.start()

    vpn_engine = OpenMeshEngine("127.0.0.1", PORTA_MATCHMAKER, nome_pc)
    success = vpn_engine.registrar(network_token=local_server.network_token)

    if not success:
        return jsonify({"erro": "Falha ao registrar no Matchmaker local"}), 500

    return jsonify({
        "mensagem": "Rede hospedada com sucesso!",
        "network_token": local_server.network_token,
        "vpn_ip": vpn_engine.vpn_ip,
    })


@app.route("/api/conectar", methods=["POST"])
def conectar_rede():
    """Modo Cliente: conecta na rede hospedada por um amigo."""
    global vpn_engine

    dados = request.get_json(force=True, silent=True) or {}
    nome_pc = (dados.get("nome_pc") or "").strip()
    ip_host = (dados.get("ip_host") or "").strip()
    alvo = (dados.get("alvo") or "").strip()
    token = (dados.get("token") or "").strip()

    if not all([nome_pc, ip_host, alvo, token]):
        return jsonify({"erro": "Campos obrigatórios: nome_pc, ip_host, alvo, token"}), 400

    if vpn_engine:
        vpn_engine.desconectar()

    vpn_engine = OpenMeshEngine(ip_host, PORTA_MATCHMAKER, nome_pc)
    success = vpn_engine.registrar(network_token=token)

    if not success:
        return jsonify({"erro": "Não foi possível conectar ao Matchmaker do amigo"}), 500

    # Hole punching em background
    threading.Thread(
        target=vpn_engine.conectar_com,
        args=(alvo,),
        daemon=True
    ).start()

    return jsonify({"mensagem": "Conectando à rede do amigo...", "vpn_ip": vpn_engine.vpn_ip})


@app.route("/api/desconectar", methods=["POST"])
def desconectar():
    global vpn_engine, local_server

    if vpn_engine:
        vpn_engine.desconectar()
        vpn_engine = None

    if local_server:
        local_server.stop()
        local_server = None

    return jsonify({"mensagem": "Desconectado com sucesso."})


# ─────────────────────────────────────────────
#  Módulos
# ─────────────────────────────────────────────

@app.route("/api/modules", methods=["GET"])
def list_modules():
    return jsonify(_discover_modules())


@app.route("/api/modules/toggle", methods=["POST"])
def toggle_module():
    dados = request.get_json(force=True, silent=True) or {}
    mod_id = dados.get("id", "").strip()
    state = bool(dados.get("active", False))

    if not mod_id:
        return jsonify({"erro": "id é obrigatório"}), 400

    action = "init_module" if state else "stop_module"
    success = _load_and_call_module(mod_id, action)

    if success:
        _module_states[mod_id] = state
        save_config()
        return jsonify({"status": "ok", "module": mod_id, "active": state})
    else:
        return jsonify({"status": "erro", "mensagem": f"Falha ao {'ativar' if state else 'desativar'} módulo '{mod_id}'"}), 500


# ─────────────────────────────────────────────
#  Rede
# ─────────────────────────────────────────────

@app.route("/api/network/peers", methods=["GET"])
def network_peers():
    """Retorna lista de peers online (consulta o Matchmaker local se for host)."""
    if not local_server or not vpn_engine:
        return jsonify({"peers": []})

    peers = list(local_server.get_info().get("peers", []))
    return jsonify({"peers": peers})


@app.route("/api/network/info", methods=["GET"])
def network_info():
    """Retorna informações da rede para exibir na UI."""
    if not local_server:
        return jsonify({"is_host": False})

    info = local_server.get_info()
    return jsonify({
        "is_host": True,
        "token": info["token"],
        "clients_online": info["clients_online"],
    })


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("----------------------------------------")
    print("   OpenMeshVPN -- Core API v1.1.0       ")
    print("----------------------------------------")
    print("  UI:    http://127.0.0.1:8080          ")
    print("  ADMIN: Execute como Administrador     ")
    print("----------------------------------------")

    load_config()
    app.run(host="127.0.0.1", port=8080, debug=False, threaded=True)