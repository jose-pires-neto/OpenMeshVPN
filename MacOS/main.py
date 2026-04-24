from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from core_vpn import OpenMeshEngine
from signaling_server import SignalingServer
import threading

app = Flask(__name__)
CORS(app)

# Variáveis globais
vpn_engine = None
local_server = None
PORTA_NUVEM = 5000

@app.route('/')
def index():
    return jsonify({"status": "OpenMeshVPN Headless API Running", "version": "1.0"})

@app.route('/api/status', methods=['GET'])
def get_status():
    if not vpn_engine:
        return jsonify({"status": "desligado", "logs": []})
    
    return jsonify({
        "status": "conectado" if vpn_engine.conectado else "online",
        "meu_nome": vpn_engine.my_name,
        "vpn_ip": vpn_engine.vpn_ip,
        "peer": f"{vpn_engine.peer_address[0]}:{vpn_engine.peer_address[1]}" if vpn_engine.peer_address else "Nenhum",
        "logs": vpn_engine.logs[-12:],
        "is_host": local_server is not None
    })

@app.route('/api/hospedar', methods=['POST'])
def hospedar_rede():
    """Modo 1: O usuário decide ser o 'Servidor' (Matchmaker) da jogatina"""
    global vpn_engine, local_server
    dados = request.json
    nome_pc = dados.get("nome_pc")
    
    if not nome_pc: return jsonify({"erro": "Nome obrigatório"}), 400
        
    # Inicia o servidor invisível no fundo
    if not local_server:
        local_server = SignalingServer(port=PORTA_NUVEM)
        local_server.start()
    
    # Inicia o motor VPN conectando no próprio PC (127.0.0.1)
    vpn_engine = OpenMeshEngine('127.0.0.1', PORTA_NUVEM, nome_pc)
    vpn_engine.registrar()
    
    return jsonify({"mensagem": "Rede hospedada com sucesso!"})

@app.route('/api/conectar', methods=['POST'])
def conectar_rede():
    """Modo 2: O usuário se conecta na rede de um amigo"""
    global vpn_engine
    dados = request.json
    nome_pc = dados.get("nome_pc")
    ip_host = dados.get("ip_host") # IP real do amigo (Radmin/Hamachi style)
    alvo = dados.get("alvo")       # Nome do amigo no sistema
    
    if not nome_pc or not ip_host or not alvo:
        return jsonify({"erro": "Preencha todos os campos"}), 400
        
    # Inicia o motor VPN apontando para o IP público/real do amigo
    vpn_engine = OpenMeshEngine(ip_host, PORTA_NUVEM, nome_pc)
    vpn_engine.registrar()
    
    # Pede para o Matchmaker (lá no amigo) conectar a gente com ele
    threading.Thread(target=vpn_engine.conectar_com, args=(alvo,)).start()
    
    return jsonify({"mensagem": "Conectando à rede do amigo..."})

# Módulos ativos na memória
active_modules = {}

@app.route('/api/modules', methods=['GET'])
def list_modules():
    return jsonify([
        {
            "id": "game_boost",
            "name": "Game Boost (UDP)",
            "description": "Prioriza pacotes UDP e bypass em NAT estrito para redução máxima de latência em jogos LAN.",
            "icon": "🎮",
            "active": active_modules.get("game_boost", False)
        },
        {
            "id": "file_share",
            "name": "File Sharing Node",
            "description": "Habilita um servidor SMB local para transferência ultrarrápida de arquivos na Mesh.",
            "icon": "📁",
            "active": active_modules.get("file_share", False)
        },
        {
            "id": "corp_route",
            "name": "Corporate Subnet",
            "description": "Permite que outros nós da rede acessem roteadores e sub-redes da sua empresa de forma segura.",
            "icon": "🏢",
            "active": active_modules.get("corp_route", False)
        }
    ])

@app.route('/api/modules/toggle', methods=['POST'])
def toggle_module():
    data = request.json
    mod_id = data.get("id")
    state = data.get("active")
    active_modules[mod_id] = state
    # Aqui chamaríamos a função de inicialização do script do módulo correspondente
    return jsonify({"status": "ok"})

if __name__ == '__main__':
    print("=== OpenMeshVPN - Core ===")
    print("UI rodando em: http://127.0.0.1:8080")
    print("Execute como Administrador se quiser usar a Placa de Rede Real.")
    app.run(host='127.0.0.1', port=8080, debug=False)