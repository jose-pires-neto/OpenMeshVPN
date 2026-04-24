"""
OpenMeshVPN — Core VPN Engine (Windows)
=========================================
Motor principal responsável por:
  - Criar e gerenciar a interface TAP virtual
  - Registrar-se no Matchmaker (Signaling Server)
  - Estabelecer túnel P2P via UDP Hole Punching
  - Criptografar/descriptografar todo o tráfego (ChaCha20Poly1305)
  - Enviar heartbeats para manter a conexão ativa
"""

import socket
import threading
import json
import time
import subprocess
import sys
import os
import struct

import win32file
import pywintypes
import winreg

# ─────────────────────────────────────────────
#  Criptografia — ChaCha20Poly1305
# ─────────────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    import secrets as _secrets
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    print("[VPN Core] AVISO: biblioteca 'cryptography' não instalada.")
    print("[VPN Core]   Execute: pip install cryptography")
    print("[VPN Core]   Rodando SEM criptografia — NÃO USE em produção!")

# ─────────────────────────────────────────────
#  TAP-Windows
# ─────────────────────────────────────────────
TAP_IOCTL_SET_MEDIA_STATUS = (34 << 16) | (0 << 14) | (6 << 2) | 0


def get_tap_adapters() -> list[tuple[str, str]]:
    """Retorna lista de adaptadores TAP instalados: [(guid, friendly_name)]."""
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
                                guid, _ = winreg.QueryValueEx(subkey, "NetCfgInstanceId")
                                name_path = (
                                    rf"SYSTEM\CurrentControlSet\Control\Network"
                                    rf"\{{4D36E972-E325-11CE-BFC1-08002BE10318}}\{guid}\Connection"
                                )
                                try:
                                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, name_path) as nk:
                                        friendly_name, _ = winreg.QueryValueEx(nk, "Name")
                                    adapters.append((guid, friendly_name))
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
    """Interface com o driver TAP-Windows para ler/escrever pacotes Ethernet."""

    def __init__(self, name: str | None = None):
        self.name = name
        self.guid: str | None = None

        adapters = get_tap_adapters()
        if not adapters:
            raise RuntimeError(
                "Nenhum adaptador TAP-Windows encontrado.\n"
                "Instale o TAP-Windows executando o instalador do OpenVPN."
            )

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
            0, None,
            win32file.OPEN_EXISTING,
            win32file.FILE_ATTRIBUTE_SYSTEM | win32file.FILE_FLAG_OVERLAPPED,
            None,
        )

    def up(self):
        """Liga a interface TAP."""
        win32file.DeviceIoControl(
            self.handle, TAP_IOCTL_SET_MEDIA_STATUS,
            struct.pack("I", 1), 4, None
        )

    def down(self):
        """Desliga a interface TAP."""
        try:
            win32file.DeviceIoControl(
                self.handle, TAP_IOCTL_SET_MEDIA_STATUS,
                struct.pack("I", 0), 4, None
            )
        except pywintypes.error:
            pass

    def read(self) -> bytes | None:
        """Lê um pacote da interface TAP (bloqueante)."""
        try:
            _hr, data = win32file.ReadFile(self.handle, 4096)
            return data if data else None
        except pywintypes.error:
            return None

    def write(self, data: bytes):
        """Escreve um pacote na interface TAP (injetando na pilha de rede do SO)."""
        try:
            win32file.WriteFile(self.handle, data)
        except pywintypes.error:
            pass

    def close(self):
        try:
            self.down()
            win32file.CloseHandle(self.handle)
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Engine Principal
# ─────────────────────────────────────────────

class OpenMeshEngine:
    """
    Motor VPN P2P da OpenMeshVPN.
    Orquestra: registro, hole punching, criptografia e roteamento de pacotes.
    """

    HEARTBEAT_INTERVAL = 20  # segundos entre heartbeats para o Matchmaker

    def __init__(self, server_ip: str, server_port: int, my_name: str):
        self.server_ip = server_ip
        self.server_port = server_port
        self.my_name = my_name

        self.vpn_ip: str | None = None       # Alocado dinamicamente pelo Matchmaker
        self.peer_address: tuple | None = None
        self.conectado = False

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)

        self.tap_device: TapDeviceClass | None = None
        self.logs: list[str] = []
        self._running = False

        # Criptografia
        self._chacha: ChaCha20Poly1305 | None = None

    # ─────────────────────────────────
    #  Logging
    # ─────────────────────────────────

    def log(self, msg: str):
        entry = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(entry)
        self.logs.append(entry)
        # Mantém apenas os últimos 200 logs em memória
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]

    # ─────────────────────────────────
    #  Criptografia
    # ─────────────────────────────────

    def set_session_key(self, key_hex: str):
        """Configura a chave ChaCha20Poly1305 da sessão (recebida via Matchmaker)."""
        if not HAS_CRYPTO:
            self.log("AVISO: Criptografia desabilitada (biblioteca não instalada).")
            return
        try:
            key_bytes = bytes.fromhex(key_hex)
            self._chacha = ChaCha20Poly1305(key_bytes)
            self.log("🔐 Criptografia ChaCha20Poly1305 ativada.")
        except Exception as e:
            self.log(f"❌ Erro ao configurar chave de criptografia: {e}")

    def _encrypt(self, plaintext: bytes) -> bytes:
        """Criptografa um pacote. Nonce aleatório de 12 bytes prefixado no payload."""
        if not self._chacha:
            return plaintext
        nonce = _secrets.token_bytes(12)
        ciphertext = self._chacha.encrypt(nonce, plaintext, None)
        return nonce + ciphertext  # 12 bytes nonce + payload cifrado + 16 bytes tag

    def _decrypt(self, data: bytes) -> bytes | None:
        """Descriptografa um pacote. Retorna None se inválido (tag errada = intruso)."""
        if not self._chacha:
            return data
        if len(data) < 29:  # 12 nonce + 1 dado + 16 tag
            return None
        nonce = data[:12]
        ciphertext = data[12:]
        try:
            return self._chacha.decrypt(nonce, ciphertext, None)
        except Exception:
            return None  # Pacote inválido ou adulterado

    # ─────────────────────────────────
    #  Interface TAP
    # ─────────────────────────────────

    def iniciar_placa_virtual(self) -> bool:
        """Abre o driver TAP e configura o IP virtual na interface."""
        if not self.vpn_ip:
            self.log("❌ IP virtual não atribuído ainda. Registre-se primeiro.")
            return False

        try:
            self.tap_device = TapDeviceClass(name=None)
            self.tap_device.up()

            self.log(f"Configurando IP {self.vpn_ip}/24 na interface '{self.tap_device.name}'...")
            cmd = (
                f'netsh interface ip set address '
                f'name="{self.tap_device.name}" static {self.vpn_ip} 255.255.255.0'
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                self.log(f"⚠️  netsh retornou erro: {result.stderr.strip()}")
            else:
                self.log(f"✅ Interface TAP '{self.tap_device.name}' ativa com IP {self.vpn_ip}")
            return True

        except RuntimeError as e:
            self.log(f"❌ Erro ao abrir TAP: {e}")
            self.log("   → Execute como Administrador e certifique-se que o TAP-Windows está instalado.")
            return False
        except Exception as e:
            self.log(f"❌ Erro inesperado ao iniciar interface virtual: {e}")
            return False

    # ─────────────────────────────────
    #  Threads de I/O
    # ─────────────────────────────────

    def _escutar_rede_p2p(self):
        """Recebe pacotes UDP da rede e os injeta na interface TAP."""
        self.log("Thread de recepção P2P iniciada.")
        while self._running:
            try:
                dados, endereco = self.socket.recvfrom(65535)

                # Pacotes de controle do Matchmaker (JSON)
                if endereco == (self.server_ip, self.server_port):
                    try:
                        msg = json.loads(dados.decode("utf-8"))
                        # Tratar notificação de hole-punch vinda do Matchmaker
                        if msg.get("status") == "buraco_solicitado":
                            amigo_ip = msg["ip_amigo"]
                            amigo_porta = msg["porta_amigo"]
                            self.log(f"📥 Requisição de conexão de {msg.get('nome_amigo')} ({amigo_ip}:{amigo_porta})")
                            # Furar o buraco de volta
                            for _ in range(5):
                                self.socket.sendto(b"PING_BURACO", (amigo_ip, amigo_porta))
                                time.sleep(0.1)
                    except (json.JSONDecodeError, KeyError):
                        pass
                    continue

                # Filtrar pacotes de hole-punch
                if dados == b"PING_BURACO":
                    if not self.peer_address:
                        self.peer_address = endereco
                        self.conectado = True
                        self.log(f"🤝 Peer conectado automaticamente: {endereco}")
                    continue

                # Pacote de dados — descriptografar e injetar no TAP
                plaintext = self._decrypt(dados)
                if plaintext and self.tap_device:
                    self.tap_device.write(plaintext)

            except OSError:
                break
            except Exception as e:
                if self._running:
                    self.log(f"Erro na thread P2P: {e}")
                    time.sleep(0.5)

    def _escutar_placa_virtual(self):
        """Lê pacotes do TAP e os envia cifrados para o peer."""
        self.log("Thread de leitura TAP iniciada.")
        while self._running:
            if not self.tap_device or not self.peer_address or not self.conectado:
                time.sleep(0.5)
                continue
            try:
                pacote = self.tap_device.read()
                if pacote:
                    encrypted = self._encrypt(pacote)
                    self.socket.sendto(encrypted, self.peer_address)
            except OSError:
                break
            except Exception as e:
                if self._running:
                    self.log(f"Erro na thread TAP: {e}")
                    time.sleep(0.5)

    def _heartbeat_loop(self):
        """Envia heartbeats periódicos para o Matchmaker evitar timeout."""
        while self._running:
            time.sleep(self.HEARTBEAT_INTERVAL)
            if not self._running:
                break
            try:
                msg = json.dumps({"acao": "heartbeat", "nome_pc": self.my_name})
                self.socket.sendto(msg.encode(), (self.server_ip, self.server_port))
            except OSError:
                break

    # ─────────────────────────────────
    #  API Pública
    # ─────────────────────────────────

    def registrar(self, network_token: str = "") -> bool:
        """
        Registra este peer no Matchmaker.
        Recebe o IP virtual alocado dinamicamente e a chave de sessão.
        """
        self.log(f"Conectando ao Matchmaker em {self.server_ip}:{self.server_port}...")

        msg = json.dumps({
            "acao": "registrar",
            "nome_pc": self.my_name,
            "token": network_token,
        })
        self.socket.sendto(msg.encode(), (self.server_ip, self.server_port))

        # Aguarda resposta de registro (timeout 10s)
        self.socket.settimeout(10.0)
        try:
            dados, _ = self.socket.recvfrom(2048)
            self.socket.settimeout(None)
            resposta = json.loads(dados.decode())

            if resposta.get("status") == "ok":
                self.vpn_ip = resposta.get("vpn_ip")
                session_key = resposta.get("session_key", "")
                if session_key:
                    self.set_session_key(session_key)
                self.log(f"✅ Registrado! IP Virtual atribuído: {self.vpn_ip}")
            else:
                self.log(f"❌ Matchmaker rejeitou: {resposta.get('mensagem')}")
                return False

        except socket.timeout:
            self.log("❌ Timeout: Matchmaker não respondeu em 10s.")
            self.socket.settimeout(None)
            return False
        except Exception as e:
            self.log(f"❌ Erro ao registrar: {e}")
            self.socket.settimeout(None)
            return False

        # Iniciar threads de I/O
        self._running = True
        threading.Thread(target=self._escutar_rede_p2p, daemon=True).start()
        threading.Thread(target=self._escutar_placa_virtual, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

        # Iniciar interface TAP
        self.iniciar_placa_virtual()
        return True

    def conectar_com(self, alvo: str) -> bool:
        """
        Solicita ao Matchmaker a conexão com outro peer (UDP Hole Punching).
        """
        self.log(f"Buscando '{alvo}' no Matchmaker...")
        msg = json.dumps({
            "acao": "conectar_com",
            "nome_pc": self.my_name,
            "alvo": alvo,
        })
        self.socket.sendto(msg.encode(), (self.server_ip, self.server_port))

        self.socket.settimeout(10.0)
        try:
            dados, _ = self.socket.recvfrom(2048)
            self.socket.settimeout(None)
            resposta = json.loads(dados.decode())

            if resposta.get("status") == "sucesso":
                peer_ip = resposta["ip_alvo"]
                peer_porta = resposta["porta_alvo"]
                self.peer_address = (peer_ip, peer_porta)
                self.log(f"Peer encontrado: {peer_ip}:{peer_porta}")

                # Hole punching: enviar vários pacotes para abrir o NAT
                self.log("Perfurando NAT (Hole Punching)...")
                for _ in range(10):
                    self.socket.sendto(b"PING_BURACO", self.peer_address)
                    time.sleep(0.2)

                self.conectado = True
                self.log("✅ Túnel P2P estabelecido com sucesso!")
                return True
            else:
                self.log(f"❌ {resposta.get('mensagem', 'Erro desconhecido')}")
                return False

        except socket.timeout:
            self.log("❌ Timeout: peer não encontrado em 10s.")
            self.socket.settimeout(None)
            return False

    def desconectar(self):
        """Desliga o motor VPN e libera recursos."""
        self.log("Desconectando...")
        self._running = False
        self.conectado = False
        self.peer_address = None

        if self.tap_device:
            self.tap_device.close()
            self.tap_device = None

        try:
            self.socket.close()
        except OSError:
            pass

        # Recria o socket para permitir reconexão posterior
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.log("✅ Desconectado.")