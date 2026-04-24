import socket
import json
import threading

class SignalingServer:
    """
    O Matchmaker que agora roda embutido (em background) no PC de quem "Hospeda" a rede.
    """
    def __init__(self, port=5000):
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.clientes = {}
        self.running = False

    def start(self):
        self.socket.bind(('0.0.0.0', self.port))
        self.running = True
        print(f"[Matchmaker] Servidor de sinalização local rodando na porta {self.port}")
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while self.running:
            try:
                dados, endereco = self.socket.recvfrom(1024)
                mensagem = json.loads(dados.decode('utf-8'))
                
                acao = mensagem.get("acao")
                nome_pc = mensagem.get("nome_pc")
                
                if acao == "registrar":
                    self.clientes[nome_pc] = endereco
                    print(f"[Matchmaker] {nome_pc} registrado: {endereco}")
                    resp = {"status": "ok", "mensagem": "Registrado."}
                    self.socket.sendto(json.dumps(resp).encode(), endereco)

                elif acao == "conectar_com":
                    alvo = mensagem.get("alvo")
                    if alvo in self.clientes:
                        end_alvo = self.clientes[alvo]
                        # Responde pro PC A
                        resp_a = {"status": "sucesso", "ip_alvo": end_alvo[0], "porta_alvo": end_alvo[1]}
                        self.socket.sendto(json.dumps(resp_a).encode(), endereco)
                        # Avisa o PC B para furar o buraco
                        resp_b = {"status": "buraco_solicitado", "ip_amigo": endereco[0], "porta_amigo": endereco[1]}
                        self.socket.sendto(json.dumps(resp_b).encode(), end_alvo)
                    else:
                        resp = {"status": "erro", "mensagem": "Alvo não encontrado."}
                        self.socket.sendto(json.dumps(resp).encode(), endereco)
            except Exception as e:
                print(f"[Matchmaker] Erro: {e}")