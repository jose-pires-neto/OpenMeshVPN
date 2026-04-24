import socket
import threading
import json
import time
import subprocess

try:
    import pytap2
    TEM_TAP = True
except ImportError:
    pytap2 = None
    TEM_TAP = False

class OpenMeshEngine:
    def __init__(self, server_ip, server_port, my_name):
        self.server_ip = server_ip
        self.server_port = server_port
        self.my_name = my_name
        self.vpn_ip = "10.144.0.2" # No futuro, o servidor de sinalização pode distribuir isso dinamicamente
        
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.tap_device = None
        self.peer_address = None
        self.conectado = False
        self.logs = []

    def log(self, msg):
        log_msg = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(log_msg)
        self.logs.append(log_msg)

    def iniciar_placa_virtual(self):
        if not TEM_TAP:
            self.log(f"AVISO: Suporte nativo TAP não carregado para {sys.platform}. Rodando em MODO SIMULAÇÃO.")
            return False

        try:
            self.tap_device = pytap2.TapDevice(name="openmesh0")
            self.tap_device.up()
            
            self.log(f"Configurando IP {self.vpn_ip} na interface 'openmesh0' do Linux...")
            cmd = f'ip addr add {self.vpn_ip}/24 dev openmesh0'
                
            subprocess.run(cmd, shell=True, capture_output=True)
            self.log("Placa de rede virtual (TAP) iniciada com sucesso!")
            return True
        except Exception as e:
            self.log(f"Erro ao criar interface TAP (Lembre de usar como Admin): {e}")
            return False

    def escutar_rede_p2p(self):
        while True:
            try:
                dados, endereco = self.socket.recvfrom(65535)
                if endereco == (self.server_ip, self.server_port) or dados == b"PING_BURACO":
                    continue
                if self.tap_device:
                    self.tap_device.write(dados)
            except Exception:
                pass

    def escutar_placa_virtual(self):
        while True:
            if not self.tap_device or not self.peer_address:
                time.sleep(1)
                continue
            try:
                pacote_so = self.tap_device.read()
                if pacote_so and self.conectado:
                    self.socket.sendto(pacote_so, self.peer_address)
            except Exception as e:
                time.sleep(1)

    def registrar(self):
        self.log(f"Conectando ao Matchmaker em {self.server_ip}:{self.server_port}...")
        msg = {"acao": "registrar", "nome_pc": self.my_name}
        self.socket.sendto(json.dumps(msg).encode(), (self.server_ip, self.server_port))
        
        threading.Thread(target=self.escutar_rede_p2p, daemon=True).start()
        threading.Thread(target=self.escutar_placa_virtual, daemon=True).start()
        self.iniciar_placa_virtual()

    def conectar_com(self, alvo):
        self.log(f"Buscando IP do amigo '{alvo}' no Matchmaker...")
        msg = {"acao": "conectar_com", "nome_pc": self.my_name, "alvo": alvo}
        self.socket.sendto(json.dumps(msg).encode(), (self.server_ip, self.server_port))
        
        # Timeout simples
        self.socket.settimeout(5.0)
        try:
            dados, _ = self.socket.recvfrom(1024)
            self.socket.settimeout(None)
            resposta = json.loads(dados.decode())
            
            if resposta.get("status") == "sucesso":
                self.peer_address = (resposta["ip_alvo"], resposta["porta_alvo"])
                self.log(f"Alvo encontrado! ({self.peer_address[0]}:{self.peer_address[1]})")
                
                self.log("Perfurando bloqueio do roteador (Hole Punching)...")
                for _ in range(5):
                    self.socket.sendto(b"PING_BURACO", self.peer_address)
                    time.sleep(0.5)
                    
                self.conectado = True
                self.log(f"Túnel VPN P2P Estabelecido com sucesso!")
                return True
            else:
                self.log(f"Matchmaker retornou: {resposta.get('mensagem')}")
                return False
        except socket.timeout:
            self.log("Erro: O Matchmaker não respondeu a tempo.")
            self.socket.settimeout(None)
            return False