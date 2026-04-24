"""
OpenMeshVPN — Signaling Server (Matchmaker)
=============================================
Servidor de sinalização que pode rodar embutido (modo Host) ou na nuvem.

Melhorias implementadas:
  - Alocação dinâmica de IPs virtuais (pool 10.144.0.2–254)
  - Autenticação por token de rede (senha da sala)
  - Distribuição de chave de criptografia entre peers
  - Detecção de clientes inativos (heartbeat)
  - Geração automática de chave simétrica de sessão
"""

import socket
import json
import threading
import time
import os
import secrets


class SignalingServer:
    """
    Matchmaker embutido: roda no background no PC do Host.
    Responsável por:
      1. Autenticar clientes com token de rede
      2. Alocar IPs virtuais únicos
      3. Trocar chaves de criptografia entre peers
      4. Intermediar o UDP Hole Punching
    """

    IP_POOL_START = 2   # 10.144.0.2
    IP_POOL_END   = 254  # 10.144.0.254
    SUBNET        = "10.144.0"
    HEARTBEAT_TIMEOUT = 60  # segundos sem heartbeat = cliente removido

    def __init__(self, port: int = 5000, network_token: str | None = None):
        self.port = port
        # Token de rede — se None, gera um aleatório (exibido no console para o host copiar)
        self.network_token = network_token or secrets.token_hex(8)

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # chave de criptografia compartilhada para a sessão (ChaCha20Poly1305)
        # 32 bytes = 256 bits
        self.session_key_hex: str = secrets.token_hex(32)

        # clientes registrados: nome → {address, vpn_ip, last_seen}
        self.clientes: dict[str, dict] = {}

        # Pool de IPs disponíveis
        self._ip_pool: set[str] = {
            f"{self.SUBNET}.{i}"
            for i in range(self.IP_POOL_START, self.IP_POOL_END + 1)
        }
        # IP do host sempre é .1
        self._host_ip = f"{self.SUBNET}.1"

        self.running = False
        self._cleanup_thread: threading.Thread | None = None

    # ──────────────────────────────────────
    #  Start / Stop
    # ──────────────────────────────────────

    def start(self):
        self.socket.bind(("0.0.0.0", self.port))
        self.running = True

        print(f"[Matchmaker] ═══════════════════════════════════════════")
        print(f"[Matchmaker] 🌐 Servidor de sinalização iniciado na porta {self.port}")
        print(f"[Matchmaker] 🔑 Token da Rede: {self.network_token}")
        print(f"[Matchmaker]    (Compartilhe este token com seus amigos)")
        print(f"[Matchmaker] 🔐 Chave de Sessão: {self.session_key_hex[:8]}... (gerada)")
        print(f"[Matchmaker] ═══════════════════════════════════════════")

        threading.Thread(target=self._loop, daemon=True).start()

        # Thread de limpeza de clientes inativos
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop(self):
        self.running = False
        try:
            self.socket.close()
        except Exception:
            pass
        print("[Matchmaker] Servidor encerrado.")

    # ──────────────────────────────────────
    #  Loop principal
    # ──────────────────────────────────────

    def _loop(self):
        while self.running:
            try:
                dados, endereco = self.socket.recvfrom(2048)
                threading.Thread(
                    target=self._handle_message,
                    args=(dados, endereco),
                    daemon=True
                ).start()
            except OSError:
                break  # Socket fechado
            except Exception as e:
                if self.running:
                    print(f"[Matchmaker] Erro no loop: {e}")

    def _handle_message(self, dados: bytes, endereco: tuple):
        try:
            mensagem = json.loads(dados.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return  # Pacote inválido — ignora

        acao = mensagem.get("acao")
        nome_pc = mensagem.get("nome_pc", "").strip()

        if not nome_pc:
            self._send({"status": "erro", "mensagem": "nome_pc obrigatório"}, endereco)
            return

        if acao == "registrar":
            self._handle_registrar(mensagem, nome_pc, endereco)

        elif acao == "conectar_com":
            self._handle_conectar(mensagem, nome_pc, endereco)

        elif acao == "heartbeat":
            self._handle_heartbeat(nome_pc, endereco)

        elif acao == "listar":
            # Retorna lista de clientes online (para a UI "Minha Rede")
            self._handle_listar(nome_pc, endereco)

        else:
            self._send({"status": "erro", "mensagem": f"Ação desconhecida: {acao}"}, endereco)

    # ──────────────────────────────────────
    #  Handlers de ação
    # ──────────────────────────────────────

    def _handle_registrar(self, msg: dict, nome_pc: str, endereco: tuple):
        """Registra um novo cliente na rede, alocando IP virtual."""
        token = msg.get("token", "")

        # Validação do token de rede
        if token != self.network_token:
            print(f"[Matchmaker] ❌ Token inválido de {endereco} (nome: {nome_pc})")
            self._send({"status": "erro", "mensagem": "Token de rede inválido."}, endereco)
            return

        # Se já estiver registrado, atualiza o endereço (reconexão)
        if nome_pc in self.clientes:
            old_ip = self.clientes[nome_pc]["vpn_ip"]
            self.clientes[nome_pc]["address"] = endereco
            self.clientes[nome_pc]["last_seen"] = time.time()
            print(f"[Matchmaker] 🔄 Reconexão: {nome_pc} @ {endereco} (IP mantido: {old_ip})")
            self._send({
                "status": "ok",
                "mensagem": "Reconectado.",
                "vpn_ip": old_ip,
                "session_key": self.session_key_hex,
                "network_token": self.network_token,
            }, endereco)
            return

        # Alocar IP virtual do pool
        if not self._ip_pool:
            self._send({"status": "erro", "mensagem": "Rede cheia (sem IPs disponíveis)."}, endereco)
            return

        vpn_ip = self._ip_pool.pop()
        self.clientes[nome_pc] = {
            "address": endereco,
            "vpn_ip": vpn_ip,
            "last_seen": time.time(),
        }

        print(f"[Matchmaker] ✅ {nome_pc} registrado: {endereco} → IP VPN: {vpn_ip}")
        self._send({
            "status": "ok",
            "mensagem": "Registrado com sucesso.",
            "vpn_ip": vpn_ip,
            "session_key": self.session_key_hex,
            "network_token": self.network_token,
        }, endereco)

    def _handle_conectar(self, msg: dict, nome_pc: str, endereco: tuple):
        """Troca endereços entre dois peers para UDP Hole Punching."""
        alvo = msg.get("alvo", "").strip()

        if alvo not in self.clientes:
            self._send({"status": "erro", "mensagem": f"'{alvo}' não encontrado na rede."}, endereco)
            return

        end_alvo = self.clientes[alvo]["address"]
        ip_vpn_alvo = self.clientes[alvo]["vpn_ip"]

        # Responde ao PC solicitante (A) com o endereço do alvo (B)
        resp_a = {
            "status": "sucesso",
            "ip_alvo": end_alvo[0],
            "porta_alvo": end_alvo[1],
            "vpn_ip_alvo": ip_vpn_alvo,
            "session_key": self.session_key_hex,
        }
        self._send(resp_a, endereco)

        # Notifica o alvo (B) para furar o firewall em direção a A
        resp_b = {
            "status": "buraco_solicitado",
            "ip_amigo": endereco[0],
            "porta_amigo": endereco[1],
            "nome_amigo": nome_pc,
        }
        self._send(resp_b, end_alvo)

        print(f"[Matchmaker] 🤝 Conexão intermediada: {nome_pc} ↔ {alvo}")

    def _handle_heartbeat(self, nome_pc: str, endereco: tuple):
        """Atualiza o timestamp de último contato do cliente."""
        if nome_pc in self.clientes:
            self.clientes[nome_pc]["last_seen"] = time.time()
            self.clientes[nome_pc]["address"] = endereco  # Atualiza se IP mudou (CGNAT)

    def _handle_listar(self, nome_pc: str, endereco: tuple):
        """Retorna lista de peers online (para a view 'Minha Rede')."""
        peers = [
            {
                "nome": nome,
                "vpn_ip": info["vpn_ip"],
                "online": (time.time() - info["last_seen"]) < self.HEARTBEAT_TIMEOUT,
            }
            for nome, info in self.clientes.items()
            if nome != nome_pc  # Não inclui o próprio cliente
        ]
        self._send({"status": "ok", "peers": peers}, endereco)

    # ──────────────────────────────────────
    #  Helpers
    # ──────────────────────────────────────

    def _send(self, data: dict, address: tuple):
        try:
            self.socket.sendto(json.dumps(data).encode("utf-8"), address)
        except OSError:
            pass

    def _cleanup_loop(self):
        """Remove clientes inativos a cada 30 segundos."""
        while self.running:
            time.sleep(30)
            now = time.time()
            to_remove = [
                nome for nome, info in self.clientes.items()
                if (now - info["last_seen"]) > self.HEARTBEAT_TIMEOUT
            ]
            for nome in to_remove:
                ip = self.clientes[nome]["vpn_ip"]
                self._ip_pool.add(ip)  # Devolve o IP ao pool
                del self.clientes[nome]
                print(f"[Matchmaker] 🔌 {nome} removido por inatividade (IP {ip} liberado)")

    def get_info(self) -> dict:
        """Retorna informações da rede para a API Flask."""
        return {
            "token": self.network_token,
            "clients_online": len(self.clientes),
            "peers": [
                {"nome": n, "vpn_ip": i["vpn_ip"]}
                for n, i in self.clientes.items()
            ],
        }