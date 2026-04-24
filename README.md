🌐 OpenMeshVPN (Nome Provisório)

Uma rede P2P (VPN Mesh) Open Source, leve e segura para conectar computadores como se estivessem na mesma rede LAN, sem precisar de configurações complexas de roteador. Ideal para jogos em LAN, compartilhamento seguro de arquivos e comunidades.

🎯 Objetivos do Projeto

Zero Configuração: O usuário instala, loga e já está na rede. Sem abrir portas em roteadores (Zero Port Forwarding).

Segurança Padrão (Secure by Default): Todo o tráfego P2P será criptografado de ponta a ponta. Nenhum PC expõe portas públicas desnecessárias.

Descentralização: O servidor central apenas "apresenta" os PCs. O tráfego de dados real (o jogo, os arquivos) vai diretamente de um PC para o outro (P2P).

🏗️ Como Funciona (Arquitetura)

O sistema é dividido em duas partes principais:

1. Control Plane (Servidor de Sinalização)

Um servidor leve rodando na nuvem. Sua única função é:

Autenticar os usuários.

Descobrir qual é o IP público e a porta que o roteador de cada PC está usando.

Trocar essas informações de IP e chaves públicas entre os computadores que querem se conectar.

2. Data Plane (Cliente no PC do Usuário)

O programa que roda no computador do usuário.

Comunica-se com o Control Plane para anunciar sua presença.

Usa a técnica de UDP Hole Punching para forçar o roteador a permitir a conexão direta com o PC do amigo.

(Futuro) Cria uma interface de rede virtual (TUN) para que os jogos reconheçam a conexão como uma placa de rede física.

🚀 Como Contribuir

Este é um projeto Open Source! Precisamos de ajuda com:

Segurança (Implementação de chaves criptográficas).

Desenvolvimento do Cliente (Interface TUN/TAP).

UI/UX (Criar um painel simples e bonito para os usuários).