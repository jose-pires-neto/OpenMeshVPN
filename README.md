# 🌐 OpenMeshVPN

OpenMeshVPN é uma solução de rede P2P (Mesh VPN) de código aberto, projetada para ser leve, segura e extremamente simples de usar. Conecte seus dispositivos como se estivessem na mesma rede local (LAN), independente de onde estejam no mundo.

---

## 🚀 Funcionalidades Atuais

- **Conexão P2P Criptografada**: Tráfego seguro entre peers usando ChaCha20Poly1305.
- **UDP Hole Punching**: Conecte-se através de firewalls e NAT sem abrir portas no roteador.
- **Sistema de Módulos**: Expanda as funcionalidades com módulos como *Game Boost* e *File Sharing*.
- **Interface Premium**: Dashboard moderno construído com Electron e Tailwind CSS.

---

## 🛠️ Como Gerar os Executáveis (Build)

O projeto é composto por um backend em **Python** e um frontend em **Electron**. Para gerar o executável final autônomo, seguimos dois passos:

### Pré-requisitos
- [Node.js](https://nodejs.org/) (v18+)
- [Python 3.11+](https://www.python.org/)
- Pip (gerenciador de pacotes do Python)

### 1. Compilar o Backend (Python)
Transformamos o backend em um executável autônomo para que o usuário final não precise instalar Python.
```bash
# Instale as dependências do Python
pip install -r Windows/requirements.txt pyinstaller

# Gere o backend.exe
npm run build:backend
```
*O executável será gerado em `dist_py/backend.exe`.*

### 2. Gerar o Instalador Final (Electron)
O Electron Builder irá empacotar a interface e o `backend.exe` em um instalador `.exe`.
```bash
# Instale as dependências do Node
npm install

# Gere o instalador (NSIS)
npm run build:win
```
*O resultado final estará na pasta `release/`.*

---

## 💻 Desenvolvimento Local

Para rodar o projeto em modo de desenvolvimento:

1. Inicie o backend Python:
   ```bash
   cd Windows
   python main.py
   ```
2. Em outro terminal, inicie o Electron:
   ```bash
   npm start
   ```

---

## 📂 Estrutura do Projeto

- `Windows/`: Core do sistema e API Flask para Windows.
- `ui/`: Interface HTML/CSS/JS.
- `modules/`: Plugins e extensões de funcionalidade.
- `main.js`: Ponto de entrada do Electron.
- `backend.spec`: Configuração do PyInstaller.

---

## 📝 Licença

Este projeto é open-source sob a licença [MIT](LICENSE).