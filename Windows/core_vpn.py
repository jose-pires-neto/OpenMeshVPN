import socket
import threading
import json
import time
import subprocess

import win32file
import pywintypes
import struct
import winreg

TAP_IOCTL_SET_MEDIA_STATUS = (34 << 16) | (0 << 14) | (6 << 2) | 0

def get_tap_adapters():
    adapters = []
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    with winreg.OpenKey(key, subkey_name) as subkey:
                        try:
                            component_id, _ = winreg.QueryValueEx(subkey, "ComponentId")
                            if "tap0901" in component_id.lower() or "tap0801" in component_id.lower():
                                net_cfg_instance_id, _ = winreg.QueryValueEx(subkey, "NetCfgInstanceId")
                                name_path = rf"SYSTEM\CurrentControlSet\Control\Network\{{4D36E972-E325-11CE-BFC1-08002BE10318}}\{net_cfg_instance_id}\Connection"
                                try:
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, name_path) as name_key:
                                        friendly_name, _ = winreg.QueryValueEx(name_key, "Name")
                                    adapters.append((net_cfg_instance_id, friendly_name))
                                except FileNotFoundError:
                                    pass
                        except FileNotFoundError:
                            pass
                except OSError:
                    pass
    except OSError:
        pass
    return adapters

class TapDeviceClass:
    def __init__(self, name=None):
        self.name = name
        self.guid = None
        
        adapters = get_tap_adapters()
        if not adapters:
            raise Exception("Nenhum adaptador TAP-Windows encontrado. Instale o OpenVPN.")
            
        for guid, friendly_name in adapters:
            if name and name == friendly_name:
                self.guid = guid
                self.name = friendly_name
                break
        
        if not self.guid:
            self.guid, self.name = adapters[0]
            
        self.handle = win32file.CreateFile(
            rf"\\.\Global\{self.guid}.tap",
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None, win32file.OPEN_EXISTING, win32file.FILE_ATTRIBUTE_SYSTEM, None
        )

    def up(self):
        win32file.DeviceIoControl(
            self.handle, TAP_IOCTL_SET_MEDIA_STATUS, struct.pack('I', 1), 4, None
        )

    def read(self):
        try:
            hr, data = win32file.ReadFile(self.handle, 2048)
            return data
        except pywintypes.error:
            return None

    def write(self, data):
        try:
            win32file.WriteFile(self.handle, data)
        except pywintypes.error:
            pass

TEM_TAP = True

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
            self.tap_device = TapDeviceClass(name=None)
            self.tap_device.up()
            
            self.log(f"Configurando IP {self.vpn_ip} na interface '{self.tap_device.name}' do Windows...")
            cmd = f'netsh interface ip set address name="{self.tap_device.name}" static {self.vpn_ip} 255.255.255.0'
                
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