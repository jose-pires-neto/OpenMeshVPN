"""
OpenMeshVPN — Módulo: Game Boost
=================================
Funcionalidades reais:
  - Configuração de QoS UDP via netsh (Windows)
  - Priorização de processos de jogos detectados via psutil
  - Monitor contínuo que aplica boost automaticamente
  - Otimizações de TCP/IP stack para baixa latência
"""

import subprocess
import threading
import time
import sys

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    print("[Game Boost] AVISO: psutil não instalado. Instale com: pip install psutil")

# Processos de jogos conhecidos que receberão prioridade máxima
KNOWN_GAME_PROCESSES = [
    "javaw.exe",          # Minecraft Java Edition
    "minecraft.exe",      # Minecraft Bedrock
    "java.exe",           # Servidores Java (Minecraft, etc.)
    "cs2.exe",            # Counter-Strike 2
    "csgo.exe",           # CS:GO (legado)
    "valorant-win64-shipping.exe",
    "GTA5.exe",
    "RocketLeague.exe",
    "FortniteClient-Win64-Shipping.exe",
    "r5apex.exe",         # Apex Legends
    "LeagueOfLegends.exe",
    "overwatch.exe",
]

_active = False
_monitor_thread = None
_boosted_pids = set()


def _run_netsh(args: list, description: str) -> bool:
    """Helper para rodar comandos netsh com tratamento de erro."""
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            print(f"[Game Boost] ✅ {description}")
        else:
            print(f"[Game Boost] ⚠️  {description} (código {result.returncode})")
        return result.returncode == 0
    except FileNotFoundError:
        print(f"[Game Boost] ❌ netsh não encontrado. Execute como Administrador.")
        return False


def _apply_network_optimizations():
    """
    Aplica otimizações de rede via netsh para reduzir latência em jogos.
    Requer execução como Administrador para algumas configurações.
    """
    print("[Game Boost] Aplicando otimizações de rede...")

    # Habilita RSS (Receive Side Scaling) para melhor throughput UDP
    _run_netsh(
        ["int", "udp", "set", "global", "rss=enabled"],
        "RSS (Receive Side Scaling) habilitado"
    )

    # Habilita ECN para controle de congestionamento mais eficiente
    _run_netsh(
        ["int", "tcp", "set", "global", "ecncapability=enabled"],
        "ECN (Explicit Congestion Notification) habilitado"
    )

    # Desativa o algoritmo de Nagle para TCP (reduz latência a custo de overhead)
    _run_netsh(
        ["int", "tcp", "set", "global", "nagleAlgorithm=disabled"],
        "Algoritmo de Nagle desativado (menor latência TCP)"
    )

    # Aumenta o tamanho do buffer de recepção UDP no kernel
    _run_netsh(
        ["int", "udp", "set", "global", "urostatemachine=enabled"],
        "URO (UDP Receive Offload) habilitado"
    )

    # Habilita DCA (Direct Cache Access) se disponível
    _run_netsh(
        ["int", "tcp", "set", "global", "dca=enabled"],
        "DCA (Direct Cache Access) habilitado"
    )


def _restore_network_defaults():
    """Restaura configurações de rede padrão ao desativar o módulo."""
    print("[Game Boost] Restaurando configurações de rede padrão...")
    _run_netsh(["int", "tcp", "set", "global", "nagleAlgorithm=default"], "Nagle restaurado")
    _run_netsh(["int", "tcp", "set", "global", "ecncapability=default"], "ECN restaurado")


def _set_process_priority(pid: int, name: str) -> bool:
    """Aumenta a prioridade de CPU de um processo de jogo."""
    if not HAS_PSUTIL:
        return False
    try:
        proc = psutil.Process(pid)
        if sys.platform == "win32":
            proc.nice(psutil.HIGH_PRIORITY_CLASS)
        else:
            proc.nice(-10)  # Linux/Mac: nice negativo = alta prioridade
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _scan_and_boost_games():
    """
    Escaneia processos em execução e aplica boost nos jogos detectados.
    Retorna a lista de jogos encontrados.
    """
    if not HAS_PSUTIL:
        return []

    detected = []
    game_names_lower = {name.lower() for name in KNOWN_GAME_PROCESSES}

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            proc_name = proc.info.get("name") or ""
            proc_pid = proc.info.get("pid")

            if proc_name.lower() in game_names_lower and proc_pid not in _boosted_pids:
                if _set_process_priority(proc_pid, proc_name):
                    _boosted_pids.add(proc_pid)
                    detected.append(proc_name)
                    print(f"[Game Boost] 🎮 Jogo detectado e priorizado: {proc_name} (PID {proc_pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Limpar PIDs que não existem mais
    dead_pids = {pid for pid in _boosted_pids if not psutil.pid_exists(pid)}
    _boosted_pids.difference_update(dead_pids)

    return detected


def _monitor_loop():
    """Thread principal de monitoramento — roda enquanto o módulo estiver ativo."""
    print("[Game Boost] 👀 Monitor de jogos iniciado (scan a cada 15s)...")
    while _active:
        _scan_and_boost_games()
        # Sleep em intervalos curtos para responder ao stop rapidamente
        for _ in range(15):
            if not _active:
                break
            time.sleep(1)
    print("[Game Boost] Monitor encerrado.")


def init_module() -> bool:
    """Ponto de entrada — chamado pelo main.py ao ativar o módulo."""
    global _active, _monitor_thread, _boosted_pids

    print("[Game Boost] ═══════════════════════════════")
    print("[Game Boost] 🎮 Iniciando Game Boost...")
    _active = True
    _boosted_pids = set()

    # 1. Aplicar otimizações de rede
    _apply_network_optimizations()

    # 2. Scan imediato de jogos em execução
    found = _scan_and_boost_games()
    if found:
        print(f"[Game Boost] Jogos já em execução priorizados: {', '.join(found)}")
    else:
        print("[Game Boost] Nenhum jogo em execução no momento. Monitor ativo.")

    # 3. Iniciar thread de monitoramento contínuo
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    _monitor_thread.start()

    print("[Game Boost] ✅ Módulo ativo!")
    return True


def stop_module() -> bool:
    """Desativa o módulo e restaura configurações."""
    global _active

    print("[Game Boost] ═══════════════════════════════")
    print("[Game Boost] Desativando Game Boost...")
    _active = False

    _restore_network_defaults()

    print("[Game Boost] ✅ Módulo desativado.")
    return True


def get_status() -> dict:
    """Retorna o estado atual do módulo (usado pela API)."""
    return {
        "active": _active,
        "boosted_processes": len(_boosted_pids),
        "psutil_available": HAS_PSUTIL,
    }
