import csv
import ctypes
import ctypes.wintypes
import hashlib
import html
import ipaddress
import json
import os
import platform
import shutil
import sys
import socket
import subprocess
import tempfile
import threading
import time
import winreg
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
import psutil
from PIL import Image, ImageTk, ImageDraw

try:
    import pystray
    from pystray import MenuItem as TrayItem
except ImportError:
    pystray = None
    TrayItem = None


APP_NAME = "PC Helper"
APP_VERSION = "4.1.0"
APP_AUTHOR = "Mathieu"

# Configuration des mises à jour.
# À renseigner lorsque le serveur/GitHub de PC Helper sera choisi.
UPDATE_MANIFEST_URL = ""
UPDATE_CHECK_TIMEOUT = 8
UPDATE_DOWNLOAD_TIMEOUT = 60
HISTORY_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "PCHelper" / "history.json"
HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ============================================================
# Helpers
# ============================================================

def now_str():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def go(octets):
    return octets / (1024 ** 3)


def run_powershell(command, timeout=30):
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        return result.returncode, output, error
    except Exception as exc:
        return -1, "", str(exc)


def safe_read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def get_desktop():
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    return desktop if desktop.exists() else Path.home()


def system_drive():
    return os.environ.get("SystemDrive", "C:") + "\\"


def fmt_duration(seconds):
    seconds = int(max(seconds, 0))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    if days:
        return f"{days} j {hours} h {minutes} min"
    return f"{hours} h {minutes} min"


def log_action(action, details=""):
    history = safe_read_json(HISTORY_FILE, [])
    history.append({
        "date": now_str(),
        "action": action,
        "details": details,
    })
    save_json(HISTORY_FILE, history[-500:])


def popup(title, text, width=760, height=560):
    win = ctk.CTkToplevel(app)
    win.title(title)
    win.geometry(f"{width}x{height}")
    win.minsize(560, 400)
    win.transient(app)

    ctk.CTkLabel(win, text=title, font=("Arial", 24, "bold")).pack(pady=(18, 10))

    box = ctk.CTkTextbox(win, font=("Consolas", 13))
    box.pack(fill="both", expand=True, padx=18, pady=10)
    box.insert("1.0", text)
    box.configure(state="disabled")

    ctk.CTkButton(win, text="Fermer", width=180, command=win.destroy).pack(pady=(0, 18))
    return win


# ============================================================
# System / hardware
# ============================================================

def get_basic_info():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(system_drive())
    return {
        "system": f"{platform.system()} {platform.release()}",
        "version": platform.version(),
        "hostname": platform.node(),
        "cpu": platform.processor() or "Non détecté",
        "arch": platform.machine(),
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "logical_cores": psutil.cpu_count(logical=True) or 0,
        "ram_total": go(mem.total),
        "ram_used": go(mem.used),
        "ram_percent": mem.percent,
        "disk_total": go(disk.total),
        "disk_used": go(disk.used),
        "disk_free": go(disk.free),
        "uptime": fmt_duration(time.time() - psutil.boot_time()),
    }


def get_hardware():
    data = {}

    code, out, err = run_powershell(
        "(Get-CimInstance Win32_Processor | Select-Object -First 1 Name,MaxClockSpeed,NumberOfCores,NumberOfLogicalProcessors | ConvertTo-Json -Compress)"
    )
    if code == 0 and out:
        try:
            data["cpu"] = json.loads(out)
        except Exception:
            data["cpu"] = {}

    code, out, err = run_powershell(
        "(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterRAM | ConvertTo-Json -Compress)"
    )
    try:
        data["gpu"] = json.loads(out) if out else []
        if isinstance(data["gpu"], dict):
            data["gpu"] = [data["gpu"]]
    except Exception:
        data["gpu"] = []

    code, out, err = run_powershell(
        "(Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product,SerialNumber | ConvertTo-Json -Compress)"
    )
    try:
        data["board"] = json.loads(out) if out else {}
    except Exception:
        data["board"] = {}

    code, out, err = run_powershell(
        "(Get-CimInstance Win32_BIOS | Select-Object Manufacturer,SMBIOSBIOSVersion,ReleaseDate | ConvertTo-Json -Compress)"
    )
    try:
        data["bios"] = json.loads(out) if out else {}
    except Exception:
        data["bios"] = {}

    return data


def calculate_health_score():
    cpu = psutil.cpu_percent(interval=0.2)
    ram = psutil.virtual_memory().percent
    disk = psutil.disk_usage(system_drive()).percent
    score = 100

    if cpu > 90:
        score -= 20
    elif cpu > 75:
        score -= 10

    if ram > 92:
        score -= 20
    elif ram > 80:
        score -= 10

    if disk > 95:
        score -= 25
    elif disk > 85:
        score -= 10

    firewall = get_firewall_summary()
    if firewall and "Désactivé" in firewall:
        score -= 10

    return max(score, 0)


# ============================================================
# Dashboard + monitoring
# ============================================================

def open_dashboard():
    win = ctk.CTkToplevel(app)
    win.title("📈 Tableau de bord")
    win.geometry("760x650")
    win.transient(app)

    ctk.CTkLabel(win, text="📈 Tableau de bord", font=("Arial", 26, "bold")).pack(pady=(20, 5))
    state = ctk.CTkLabel(win, text="", font=("Arial", 20, "bold"))
    state.pack(pady=12)

    cards = ctk.CTkFrame(win)
    cards.pack(fill="both", expand=True, padx=25, pady=15)

    values = {}
    bars = {}
    for name in ("CPU", "RAM", "Disque"):
        values[name] = ctk.CTkLabel(cards, text="", font=("Arial", 18))
        values[name].pack(pady=(14, 5))
        bars[name] = ctk.CTkProgressBar(cards, width=520)
        bars[name].pack(pady=(0, 12))

    uptime = ctk.CTkLabel(cards, text="", font=("Arial", 17))
    uptime.pack(pady=14)

    def refresh():
        if not win.winfo_exists():
            return
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage(system_drive()).percent
        score = calculate_health_score()

        values["CPU"].configure(text=f"CPU : {cpu:.0f} %")
        values["RAM"].configure(text=f"RAM : {ram:.0f} %")
        values["Disque"].configure(text=f"Disque : {disk:.0f} % utilisé")
        bars["CPU"].set(cpu / 100)
        bars["RAM"].set(ram / 100)
        bars["Disque"].set(disk / 100)
        uptime.configure(text=f"Uptime : {fmt_duration(time.time() - psutil.boot_time())}")

        if score >= 85:
            state.configure(text=f"🟢 État du PC : BON — {score}/100")
        elif score >= 65:
            state.configure(text=f"🟠 État du PC : À SURVEILLER — {score}/100")
        else:
            state.configure(text=f"🔴 État du PC : ATTENTION — {score}/100")
        win.after(1500, refresh)

    refresh()


def open_realtime_monitor():
    win = ctk.CTkToplevel(app)
    win.title("📊 Moniteur en temps réel")
    win.geometry("760x590")
    win.transient(app)

    ctk.CTkLabel(win, text="📊 Moniteur en temps réel", font=("Arial", 26, "bold")).pack(pady=(20, 10))

    rows = {}
    for label in ("CPU", "RAM", "Disque", "Réseau réception", "Réseau envoi"):
        frame = ctk.CTkFrame(win)
        frame.pack(fill="x", padx=35, pady=7)
        rows[label] = {
            "label": ctk.CTkLabel(frame, text=label, font=("Arial", 16)),
            "bar": ctk.CTkProgressBar(frame, width=500),
        }
        rows[label]["label"].pack(side="left", padx=15, pady=12)
        rows[label]["bar"].pack(side="right", padx=15)

    net_prev = psutil.net_io_counters()
    t_prev = time.time()

    def update():
        nonlocal net_prev, t_prev
        if not win.winfo_exists():
            return

        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage(system_drive()).percent
        net = psutil.net_io_counters()
        now = time.time()
        dt = max(now - t_prev, 0.01)
        recv_mbps = (net.bytes_recv - net_prev.bytes_recv) / dt / 1024 / 1024
        send_mbps = (net.bytes_sent - net_prev.bytes_sent) / dt / 1024 / 1024
        net_prev, t_prev = net, now

        rows["CPU"]["label"].configure(text=f"CPU : {cpu:.0f} %")
        rows["RAM"]["label"].configure(text=f"RAM : {ram:.0f} %")
        rows["Disque"]["label"].configure(text=f"Disque : {disk:.0f} % utilisé")
        rows["Réseau réception"]["label"].configure(text=f"↓ Réception : {recv_mbps:.2f} MB/s")
        rows["Réseau envoi"]["label"].configure(text=f"↑ Envoi : {send_mbps:.2f} MB/s")

        rows["CPU"]["bar"].set(cpu / 100)
        rows["RAM"]["bar"].set(ram / 100)
        rows["Disque"]["bar"].set(disk / 100)
        rows["Réseau réception"]["bar"].set(min(recv_mbps / 100, 1))
        rows["Réseau envoi"]["bar"].set(min(send_mbps / 100, 1))

        win.after(1000, update)

    update()


def open_temperatures():
    lines = ["=== TEMPÉRATURES ===", ""]
    found = False

    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        for name, entries in temps.items():
            if entries:
                lines.append(name)
                for item in entries:
                    current = getattr(item, "current", None)
                    label = getattr(item, "label", "") or "Capteur"
                    lines.append(f"  {label}: {current} °C")
                    found = True
                lines.append("")
    except Exception:
        pass

    code, out, err = run_powershell(
        "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi | "
        "Select-Object CurrentTemperature | ConvertTo-Json -Compress"
    )
    if code == 0 and out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                value = item.get("CurrentTemperature")
                if value:
                    lines.append(f"Zone ACPI : {value / 10 - 273.15:.1f} °C")
                    found = True
        except Exception:
            pass

    if not found:
        lines += [
            "Aucun capteur exploitable n'a été trouvé.",
            "",
            "La lecture CPU/GPU dépend du matériel et des pilotes.",
            "Une prochaine version pourra intégrer un backend dédié",
            "(par ex. Libre Hardware Monitor/OpenHardwareMonitor).",
        ]

    popup("🌡️ Températures", "\n".join(lines), 650, 500)


def open_battery():
    battery = psutil.sensors_battery()
    if battery is None:
        text = "🔋 Aucune batterie détectée.\n\nCette fonction est surtout destinée aux portables."
    else:
        plugged = "Oui" if battery.power_plugged else "Non"
        if battery.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED):
            remaining = fmt_duration(battery.secsleft)
        else:
            remaining = "Non disponible"

        text = (
            "🔋 BATTERIE\n\n"
            f"Niveau : {battery.percent:.0f} %\n"
            f"Secteur : {plugged}\n"
            f"Autonomie estimée : {remaining}\n\n"
            "Capacité / cycles détaillés :\n"
            "→ dépend du matériel et des pilotes Windows."
        )
    popup("🔋 Batterie", text, 620, 430)


def open_hardware():
    data = get_hardware()
    cpu = data.get("cpu", {})
    board = data.get("board", {})
    bios = data.get("bios", {})
    gpus = data.get("gpu", [])

    lines = [
        "🧩 CARTE MATÉRIEL",
        "",
        f"CPU : {cpu.get('Name', platform.processor() or 'Non détecté')}",
        f"Cœurs : {cpu.get('NumberOfCores', psutil.cpu_count(logical=False) or '?')} physiques / "
        f"{cpu.get('NumberOfLogicalProcessors', psutil.cpu_count() or '?')} logiques",
        f"RAM : {go(psutil.virtual_memory().total):.1f} Go",
        "",
        "GPU :",
    ]
    if gpus:
        for gpu in gpus:
            ram_mb = (gpu.get("AdapterRAM") or 0) / (1024 * 1024)
            lines.append(f"  - {gpu.get('Name', 'Inconnu')} | pilote {gpu.get('DriverVersion', '?')} | VRAM {ram_mb:.0f} Mo")
    else:
        lines.append("  - Non détecté")

    lines += [
        "",
        "CARTE MÈRE",
        f"Fabricant : {board.get('Manufacturer', '?')}",
        f"Modèle : {board.get('Product', '?')}",
        "",
        "BIOS",
        f"Fabricant : {bios.get('Manufacturer', '?')}",
        f"Version : {bios.get('SMBIOSBIOSVersion', '?')}",
        f"Date : {bios.get('ReleaseDate', '?')}",
    ]

    popup("🧩 Matériel", "\n".join(lines), 820, 650)


# ============================================================
# Storage
# ============================================================

def choose_folder():
    return filedialog.askdirectory(title="Choisir un dossier")


def scan_folder_sizes(folder, max_items=30):
    totals = []
    for item in Path(folder).iterdir():
        try:
            size = item.stat().st_size if item.is_file() else sum(
                f.stat().st_size for f in item.rglob("*") if f.is_file()
            )
            totals.append((size, item))
        except Exception:
            continue
    totals.sort(reverse=True, key=lambda x: x[0])
    return totals[:max_items]


def open_storage_analysis():
    folder = choose_folder()
    if not folder:
        return

    win = ctk.CTkToplevel(app)
    win.title("💾 Analyse du disque")
    win.geometry("820x650")
    win.transient(app)

    ctk.CTkLabel(win, text="💾 Analyse du disque", font=("Arial", 24, "bold")).pack(pady=15)
    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=10)

    def worker():
        result = [f"Dossier analysé : {folder}", ""]
        result.append("Plus gros éléments :\n")
        for size, path in scan_folder_sizes(folder):
            result.append(f"{size / (1024**3):8.2f} Go   {path}")
        box.after(0, lambda: (box.insert("1.0", "\n".join(result)), box.configure(state="disabled")))

    threading.Thread(target=worker, daemon=True).start()
    log_action("Analyse du stockage", folder)


def open_large_files():
    folder = choose_folder()
    if not folder:
        return

    win = ctk.CTkToplevel(app)
    win.title("📦 Gros fichiers")
    win.geometry("850x650")
    win.transient(app)

    top = ctk.CTkFrame(win)
    top.pack(fill="x", padx=18, pady=15)

    ctk.CTkLabel(top, text="Taille minimale (Go) :").pack(side="left", padx=10)
    entry = ctk.CTkEntry(top, width=100)
    entry.insert(0, "1")
    entry.pack(side="left")

    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=10)

    def search():
        try:
            min_bytes = float(entry.get().replace(",", ".")) * 1024**3
        except ValueError:
            messagebox.showerror("Valeur incorrecte", "Entre une taille en Go.")
            return

        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", "Recherche en cours...\n")
        box.configure(state="disabled")

        def worker():
            found = []
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d not in {"$Recycle.Bin", "System Volume Information"}]
                for name in files:
                    path = Path(root) / name
                    try:
                        size = path.stat().st_size
                        if size >= min_bytes:
                            found.append((size, path))
                    except Exception:
                        continue

            found.sort(reverse=True, key=lambda x: x[0])
            lines = [f"Dossier : {folder}", f"Seuil : {min_bytes / 1024**3:.2f} Go", ""]
            lines += [f"{s / 1024**3:8.2f} Go   {p}" for s, p in found[:200]]
            if not found:
                lines.append("Aucun fichier trouvé.")
            box.after(0, lambda: (box.configure(state="normal"), box.delete("1.0", "end"),
                                  box.insert("1.0", "\n".join(lines)), box.configure(state="disabled")))
            log_action("Recherche gros fichiers", f"{folder} | seuil={min_bytes/1024**3:.2f} Go")

        threading.Thread(target=worker, daemon=True).start()

    ctk.CTkButton(top, text="Rechercher", command=search).pack(side="left", padx=12)


def file_hash(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def open_duplicates():
    folder = choose_folder()
    if not folder:
        return

    win = ctk.CTkToplevel(app)
    win.title("🔁 Doublons")
    win.geometry("900x650")
    win.transient(app)
    ctk.CTkLabel(win, text="🔁 Recherche de doublons", font=("Arial", 24, "bold")).pack(pady=15)
    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=10)

    def worker():
        by_size = {}
        for root, dirs, files in os.walk(folder):
            for name in files:
                p = Path(root) / name
                try:
                    size = p.stat().st_size
                    if size >= 1:
                        by_size.setdefault(size, []).append(p)
                except Exception:
                    continue

        dupes = []
        for size, files in by_size.items():
            if len(files) < 2:
                continue
            groups = {}
            for p in files:
                try:
                    groups.setdefault(file_hash(p), []).append(p)
                except Exception:
                    continue
            for digest, paths in groups.items():
                if len(paths) > 1:
                    dupes.append((size, digest, paths))

        lines = [f"Dossier : {folder}", ""]
        if not dupes:
            lines.append("Aucun doublon détecté.")
        else:
            for size, digest, paths in dupes[:100]:
                lines.append(f"{size / 1024**2:.2f} Mo | SHA256 {digest[:16]}...")
                for p in paths:
                    lines.append(f"  - {p}")
                lines.append("")

        box.after(0, lambda: (box.configure(state="normal"), box.insert("1.0", "\n".join(lines)), box.configure(state="disabled")))
        log_action("Recherche doublons", folder)

    threading.Thread(target=worker, daemon=True).start()


def open_disk_state():
    code, out, err = run_powershell(
        "Get-Disk | Select-Object Number,FriendlyName,OperationalStatus,HealthStatus,Size,PartitionStyle | ConvertTo-Json -Compress"
    )
    try:
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = []

    lines = ["💾 ÉTAT DES DISQUES", ""]
    if data:
        for d in data:
            lines += [
                f"Disque {d.get('Number', '?')} : {d.get('FriendlyName', '?')}",
                f"  État : {d.get('OperationalStatus', '?')} / santé {d.get('HealthStatus', '?')}",
                f"  Taille : {go(d.get('Size', 0)):.1f} Go",
                f"  Partition : {d.get('PartitionStyle', '?')}",
                "",
            ]
    else:
        lines.append("Impossible de récupérer l'état des disques.")

    popup("💾 État des disques", "\n".join(lines), 760, 520)


def open_advanced_cleanup():
    win = ctk.CTkToplevel(app)
    win.title("🧹 Nettoyage avancé")
    win.geometry("720x560")
    win.transient(app)

    ctk.CTkLabel(win, text="🧹 Nettoyage avancé", font=("Arial", 24, "bold")).pack(pady=(18, 8))
    ctk.CTkLabel(win, text="Aperçu avant suppression — seuls les éléments sélectionnés seront supprimés.").pack(pady=(0, 12))

    items = []

    temp_dir = Path(tempfile.gettempdir())
    candidates = [
        ("Fichiers temporaires utilisateur", temp_dir),
        ("Cache Chrome", Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data/Default/Cache"),
        ("Cache Edge", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/User Data/Default/Cache"),
        ("Cache Firefox", Path(os.environ.get("APPDATA", "")) / "Mozilla/Firefox/Profiles"),
    ]

    for label, path in candidates:
        var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(win, text=f"{label} — {path}", variable=var).pack(anchor="w", padx=22, pady=8)
        items.append((var, label, path))

    result = ctk.CTkLabel(win, text="Sélectionne un ou plusieurs éléments.")
    result.pack(pady=12)

    def preview():
        total = 0
        count = 0
        for var, label, path in items:
            if not var.get() or not path.exists():
                continue
            try:
                if path.is_file():
                    total += path.stat().st_size
                    count += 1
                else:
                    for f in path.rglob("*"):
                        try:
                            if f.is_file():
                                total += f.stat().st_size
                                count += 1
                        except Exception:
                            pass
            except Exception:
                pass
        result.configure(text=f"Aperçu : {count} fichiers • {total / 1024**2:.2f} Mo")

    def clean():
        if not messagebox.askyesno("Confirmation", "Supprimer les éléments sélectionnés ?"):
            return
        deleted = 0
        total = 0

        for var, label, path in items:
            if not var.get() or not path.exists():
                continue
            try:
                if path.is_file():
                    total += path.stat().st_size
                    path.unlink()
                    deleted += 1
                else:
                    for child in list(path.iterdir()):
                        try:
                            if child.is_file() or child.is_symlink():
                                total += child.stat().st_size
                                child.unlink()
                                deleted += 1
                            elif child.is_dir():
                                shutil.rmtree(child, ignore_errors=True)
                                deleted += 1
                        except Exception:
                            continue
            except Exception:
                continue

        result.configure(text=f"Nettoyage terminé : {deleted} éléments • {total / 1024**2:.2f} Mo")
        log_action("Nettoyage avancé", f"{deleted} éléments")

    ctk.CTkButton(win, text="🔎 Aperçu", command=preview).pack(pady=8)
    ctk.CTkButton(win, text="🧹 Supprimer la sélection", command=clean).pack(pady=8)


# ============================================================
# Network
# ============================================================

def get_network_info():
    lines = ["🌐 INFORMATIONS RÉSEAU", ""]
    hostname = socket.gethostname()
    lines.append(f"Nom : {hostname}")
    lines.append("")

    try:
        for nic, addrs in psutil.net_if_addrs().items():
            lines.append(f"[{nic}]")
            for addr in addrs:
                lines.append(f"  {addr.family}: {addr.address}")
            lines.append("")
    except Exception:
        pass

    code, out, err = run_powershell(
        "Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer | ConvertTo-Json -Compress"
    )
    if out:
        lines += ["Configuration IP : ", out, ""]
    return "\n".join(lines)


def ping_host(host):
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", host],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return (True, (time.perf_counter() - started) * 1000)
    except Exception:
        pass
    return (False, None)


def open_network_test():
    win = ctk.CTkToplevel(app)
    win.title("🌐 Test Internet")
    win.geometry("650x520")
    win.transient(app)

    box = ctk.CTkTextbox(win, font=("Consolas", 13))
    box.pack(fill="both", expand=True, padx=18, pady=18)

    def run():
        lines = ["🌐 TEST INTERNET", ""]
        for host in ("1.1.1.1", "8.8.8.8", "google.com"):
            ok, latency = ping_host(host)
            if ok:
                lines.append(f"✓ {host:<18} {latency:.1f} ms")
            else:
                lines.append(f"✗ {host:<18} échec")
        popup("Résultat réseau", "\n".join(lines), 650, 420)
        log_action("Test Internet")

    ctk.CTkButton(win, text="Lancer le test", command=run).pack(pady=(0, 18))


def open_network_info():
    popup("🌐 Informations réseau", get_network_info(), 900, 650)


def open_dns_test():
    win = ctk.CTkToplevel(app)
    win.title("🧭 Test DNS")
    win.geometry("700x500")
    win.transient(app)

    host_entry = ctk.CTkEntry(win, width=420, placeholder_text="exemple.com")
    host_entry.pack(pady=20)
    host_entry.insert(0, "google.com")

    result_label = ctk.CTkLabel(win, text="")
    result_label.pack(pady=10)

    def test():
        host = host_entry.get().strip()
        started = time.perf_counter()
        try:
            ip = socket.gethostbyname(host)
            latency = (time.perf_counter() - started) * 1000
            result_label.configure(text=f"✅ {host} → {ip}\nRésolution DNS : {latency:.1f} ms")
            log_action("Test DNS", f"{host} -> {ip}")
        except Exception as exc:
            result_label.configure(text=f"❌ Échec de résolution\n{exc}")

    ctk.CTkButton(win, text="Tester le DNS", command=test).pack(pady=10)


def get_local_ipv4():
    for _, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                return addr.address, addr.netmask
    return None, None


def open_lan_scanner():
    win = ctk.CTkToplevel(app)
    win.title("📡 Scanner réseau local")
    win.geometry("760x620")
    win.transient(app)

    ctk.CTkLabel(win, text="📡 Scanner réseau local", font=("Arial", 24, "bold")).pack(pady=15)
    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=10)

    def scan():
        ip, mask = get_local_ipv4()
        if not ip or not mask:
            box.insert("1.0", "Impossible de déterminer le réseau local.")
            return

        network = ipaddress.ip_network(f"{ip}/{mask}", strict=False)
        hosts = list(network.hosts())[:254]
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", f"Réseau : {network}\nScan en cours...\n\n")
        box.configure(state="disabled")

        def worker():
            found = []
            with ThreadPoolExecutor(max_workers=32) as executor:
                futures = {executor.submit(ping_host, str(h)): str(h) for h in hosts}
                for future in as_completed(futures):
                    host = futures[future]
                    try:
                        ok, latency = future.result()
                        if ok:
                            try:
                                name = socket.gethostbyaddr(host)[0]
                            except Exception:
                                name = "Nom inconnu"
                            found.append((host, latency, name))
                    except Exception:
                        continue

            found.sort(key=lambda x: ipaddress.ip_address(x[0]))
            lines = [f"Réseau : {network}", f"Appareils détectés : {len(found)}", ""]
            for host, latency, name in found:
                lines.append(f"{host:<16} {latency:>7.1f} ms   {name}")

            box.after(0, lambda: (box.configure(state="normal"), box.delete("1.0", "end"),
                                  box.insert("1.0", "\n".join(lines)), box.configure(state="disabled")))
            log_action("Scan réseau local", str(network))

        threading.Thread(target=worker, daemon=True).start()

    ctk.CTkButton(win, text="🔍 Scanner", command=scan).pack(pady=(0, 18))


def open_connection_history():
    history = safe_read_json(HISTORY_FILE, [])
    network_entries = [h for h in history if "réseau" in h.get("action", "").lower() or "DNS" in h.get("action", "")]
    lines = ["🌐 HISTORIQUE RÉSEAU", ""]
    if network_entries:
        for item in reversed(network_entries[-100:]):
            lines.append(f"{item['date']} | {item['action']} | {item['details']}")
    else:
        lines.append("Aucun test réseau enregistré.")
    popup("🌐 Historique réseau", "\n".join(lines), 850, 600)


# ============================================================
# Technician tools
# ============================================================

def open_sfc_dism():
    win = ctk.CTkToplevel(app)
    win.title("🛠 Vérification Windows")
    win.geometry("780x580")
    win.transient(app)

    ctk.CTkLabel(win, text="🛠 Vérification Windows", font=("Arial", 24, "bold")).pack(pady=15)
    ctk.CTkLabel(win, text="Ces commandes peuvent prendre plusieurs minutes et peuvent nécessiter les droits administrateur.").pack(pady=5)

    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=12)

    def run_command(command, label):
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", f"{label}\n\nCommande : {command}\n\nExécution en cours...\n")
        box.configure(state="disabled")

        def worker():
            code, out, err = run_powershell(command, timeout=900)
            text = out or err or f"Code de sortie : {code}"
            box.after(0, lambda: (box.configure(state="normal"), box.insert("end", "\n\n" + text),
                                  box.configure(state="disabled")))
            log_action(label)

        threading.Thread(target=worker, daemon=True).start()

    buttons = ctk.CTkFrame(win)
    buttons.pack(pady=(0, 16))

    ctk.CTkButton(buttons, text="SFC /scannow", command=lambda: run_command("sfc /scannow", "SFC /scannow")).pack(side="left", padx=8)
    ctk.CTkButton(buttons, text="DISM RestoreHealth", command=lambda: run_command("DISM /Online /Cleanup-Image /RestoreHealth", "DISM RestoreHealth")).pack(side="left", padx=8)


def open_chkdsk():
    text = (
        "🛠 VÉRIFICATION DU DISQUE — CHKDSK\n\n"
        "CHKDSK vérifie la structure du système de fichiers et peut rechercher des secteurs défectueux.\n\n"
        "Pour éviter toute modification involontaire, PC Helper ouvre ici l'outil Windows.\n\n"
        "Commande d'analyse simple :\n"
        "chkdsk C:\n\n"
        "Une réparation (/f) peut nécessiter un redémarrage et doit être utilisée uniquement "
        "lorsqu'elle est nécessaire."
    )
    popup("🛠 CHKDSK", text, 720, 520)


def get_startup_programs():
    results = []
    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ]
    for root, path in roots:
        try:
            with winreg.OpenKey(root, path) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        results.append((name, value))
                        i += 1
                    except OSError:
                        break
        except OSError:
            continue
    return results


def open_startup():
    items = get_startup_programs()
    lines = ["🚀 PROGRAMMES AU DÉMARRAGE", ""]
    if items:
        for name, value in items:
            lines.append(f"{name:<30} {value}")
    else:
        lines.append("Aucun élément détecté.")
    popup("🚀 Démarrage", "\n".join(lines), 1000, 650)


def open_services():
    code, out, err = run_powershell(
        "Get-Service | Where-Object {$_.Status -eq 'Running'} | "
        "Sort-Object DisplayName | Select-Object Status,Name,DisplayName | Format-Table -AutoSize | Out-String"
    )
    popup("⚙️ Services Windows", out or err or "Impossible de récupérer les services.", 900, 650)


def open_processes():
    win = ctk.CTkToplevel(app)
    win.title("📊 Processus")
    win.geometry("950x700")
    win.transient(app)

    ctk.CTkLabel(win, text="📊 Processus", font=("Arial", 24, "bold")).pack(pady=15)
    box = ctk.CTkTextbox(win, font=("Consolas", 11))
    box.pack(fill="both", expand=True, padx=18, pady=10)

    def refresh():
        processes = []
        for proc in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
            try:
                p = proc.info
                processes.append((
                    p["memory_percent"] or 0,
                    p["cpu_percent"] or 0,
                    p["pid"],
                    p["name"] or "?"
                ))
            except Exception:
                continue
        processes.sort(reverse=True)
        lines = [f"{'PID':>7}  {'CPU':>6}  {'RAM':>6}  NOM", ""]
        for ram, cpu, pid, name in processes[:80]:
            lines.append(f"{pid:>7}  {cpu:>5.1f}%  {ram:>5.1f}%  {name}")
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        win.after(2500, refresh)

    refresh()


def open_drivers():
    command = (
        "if (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue) { "
        "Get-PnpDevice | Select-Object Status,Class,FriendlyName,InstanceId | "
        "Sort-Object Status,Class,FriendlyName | Format-Table -AutoSize | Out-String "
        "} else { "
        "Get-CimInstance Win32_PnPEntity | Select-Object Status,PNPClass,Name,DeviceID | "
        "Sort-Object Status,PNPClass,Name | Format-Table -AutoSize | Out-String }"
    )
    code, out, err = run_powershell(command)
    popup("🧩 Gestionnaire de pilotes", out or err or "Impossible de récupérer les périphériques.", 1200, 700)


def open_windows_updates():
    code, out, err = run_powershell(
        "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 20 "
        "HotFixID,Description,InstalledOn | Format-Table -AutoSize | Out-String"
    )
    popup("🔄 Mises à jour Windows", out or err or "Impossible de récupérer l'historique des mises à jour.", 900, 650)


# ============================================================
# Security
# ============================================================

def get_firewall_summary():
    code, out, err = run_powershell(
        "Get-NetFirewallProfile | Select-Object Name,Enabled | ConvertTo-Json -Compress"
    )
    try:
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        text = "\n".join(f"{d.get('Name', '?'):<12} : {'Activé' if d.get('Enabled') else 'Désactivé'}" for d in data)
        return text
    except Exception:
        return out or err


def open_firewall():
    popup("🧱 Pare-feu", "État des profils Windows Firewall\n\n" + get_firewall_summary(), 620, 400)


def open_defender():
    code, out, err = run_powershell(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AMServiceEnabled,AntispywareEnabled,AntivirusSignatureLastUpdated | ConvertTo-Json -Compress"
    )
    try:
        d = json.loads(out) if out else {}
        text = (
            f"Antivirus activé : {'Oui' if d.get('AntivirusEnabled') else 'Non'}\n"
            f"Protection temps réel : {'Oui' if d.get('RealTimeProtectionEnabled') else 'Non'}\n"
            f"Service : {'Oui' if d.get('AMServiceEnabled') else 'Non'}\n"
            f"Antispyware : {'Oui' if d.get('AntispywareEnabled') else 'Non'}\n"
            f"Dernière mise à jour des signatures : {d.get('AntivirusSignatureLastUpdated', '?')}"
        )
    except Exception:
        text = out or err or "Impossible d'interroger Microsoft Defender."
    popup("🛡️ Microsoft Defender", text, 700, 450)


def open_security_updates():
    code, out, err = run_powershell(
        "Get-HotFix | Where-Object {$_.Description -match 'Security'} | "
        "Sort-Object InstalledOn -Descending | Select-Object -First 15 HotFixID,InstalledOn,Description | "
        "Format-Table -AutoSize | Out-String"
    )
    popup("🔐 Mises à jour de sécurité", out or err or "Aucune donnée disponible.", 900, 600)


def open_security_audit():
    lines = ["🔐 AUDIT DE SÉCURITÉ", ""]
    firewall = get_firewall_summary()
    lines += ["Pare-feu :", firewall, ""]

    code, out, err = run_powershell(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AMServiceEnabled | ConvertTo-Json -Compress"
    )
    try:
        d = json.loads(out) if out else {}
        lines += [
            "Microsoft Defender :",
            f"  Antivirus : {'OK' if d.get('AntivirusEnabled') else 'ATTENTION'}",
            f"  Temps réel : {'OK' if d.get('RealTimeProtectionEnabled') else 'ATTENTION'}",
            f"  Service : {'OK' if d.get('AMServiceEnabled') else 'ATTENTION'}",
            "",
        ]
    except Exception:
        lines += ["Microsoft Defender : données indisponibles.", ""]

    disk = psutil.disk_usage(system_drive())
    lines += [
        "Système :",
        f"  Windows : {platform.release()}",
        f"  Espace disque libre : {100 - disk.percent:.1f} %",
        f"  Architecture : {platform.machine()}",
    ]

    popup("🔐 Audit de sécurité", "\n".join(lines), 720, 620)


def security_score():
    score = 100
    firewall = get_firewall_summary()
    if firewall and "Désactivé" in firewall:
        score -= 25

    code, out, err = run_powershell(
        "Get-MpComputerStatus | Select-Object AntivirusEnabled,RealTimeProtectionEnabled | ConvertTo-Json -Compress"
    )
    try:
        d = json.loads(out) if out else {}
        if not d.get("AntivirusEnabled"):
            score -= 30
        if not d.get("RealTimeProtectionEnabled"):
            score -= 25
    except Exception:
        score -= 10

    return max(score, 0)


def open_security_score():
    score = security_score()
    if score >= 85:
        label = "🟢 BON"
    elif score >= 65:
        label = "🟠 À SURVEILLER"
    else:
        label = "🔴 ATTENTION"

    popup("🔐 Score de sécurité", f"Score : {score}/100\n\nÉtat : {label}", 500, 350)


# ============================================================
# Reports / history
# ============================================================

def build_full_report():
    info = get_basic_info()
    hardware = get_hardware()
    return (
        "==================================================\n"
        "                 PC HELPER\n"
        f"                 VERSION {APP_VERSION}\n"
        "                 RAPPORT COMPLET\n"
        "==================================================\n\n"
        f"Date : {now_str()}\n\n"
        "SYSTÈME\n"
        "-------\n"
        f"OS : {info['system']}\n"
        f"Version : {info['version']}\n"
        f"Nom du PC : {info['hostname']}\n"
        f"Architecture : {info['arch']}\n"
        f"Uptime : {info['uptime']}\n\n"
        "CPU / RAM / DISQUE\n"
        "------------------\n"
        f"CPU : {info['cpu']}\n"
        f"Cœurs : {info['physical_cores']} physiques / {info['logical_cores']} logiques\n"
        f"RAM : {info['ram_used']:.1f} / {info['ram_total']:.1f} Go ({info['ram_percent']:.0f} %)\n"
        f"Disque : {info['disk_used']:.1f} / {info['disk_total']:.1f} Go\n"
        f"Libre : {info['disk_free']:.1f} Go\n\n"
        f"Score de santé : {calculate_health_score()}/100\n"
        f"Score sécurité : {security_score()}/100\n\n"
        "MATÉRIEL\n"
        "--------\n"
        f"Carte mère : {hardware.get('board', {}).get('Manufacturer', '?')} "
        f"{hardware.get('board', {}).get('Product', '?')}\n"
        f"BIOS : {hardware.get('bios', {}).get('SMBIOSBIOSVersion', '?')}\n"
    )


def open_full_report():
    text = build_full_report()
    popup("📄 Rapport complet", text, 900, 700)


def export_report():
    text = build_full_report()
    desktop = get_desktop()
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    txt = desktop / f"PC_Helper_Rapport_{stamp}.txt"
    txt.write_text(text, encoding="utf-8")

    html_path = desktop / f"PC_Helper_Rapport_{stamp}.html"
    html_doc = (
        "<html><head><meta charset='utf-8'><title>PC Helper</title></head>"
        "<body><h1>PC Helper — Rapport</h1><pre>"
        + html.escape(text)
        + "</pre></body></html>"
    )
    html_path.write_text(html_doc, encoding="utf-8")

    csv_path = desktop / f"PC_Helper_Rapport_{stamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        for line in text.splitlines():
            writer.writerow([line])

    popup(
        "📤 Export",
        f"Exports créés sur le Bureau :\n\n{txt}\n{html_path}\n{csv_path}",
        760,
        430
    )
    log_action("Export rapport", str(txt))


def open_history():
    history = safe_read_json(HISTORY_FILE, [])
    lines = ["📚 HISTORIQUE DES ACTIONS", ""]
    for item in reversed(history[-150:]):
        lines.append(f"{item['date']} | {item['action']} | {item['details']}")
    if len(lines) == 2:
        lines.append("Aucune action enregistrée.")
    popup("📚 Historique", "\n".join(lines), 1100, 700)


def open_compare_before_after():
    info = get_basic_info()
    score = calculate_health_score()
    current = {
        "date": now_str(),
        "ram_percent": info["ram_percent"],
        "disk_free": info["disk_free"],
        "cpu": psutil.cpu_percent(interval=0.3),
        "score": score,
    }

    path = HISTORY_FILE.parent / "baseline.json"
    baseline = safe_read_json(path, None)

    if baseline is None:
        save_json(path, current)
        popup("↔️ Comparaison", "Un état de référence vient d'être enregistré.\n\nEffectue une réparation ou une maintenance, puis relance cette fonction.")
        log_action("Création état de référence")
        return

    text = (
        "↔️ COMPARAISON AVANT / APRÈS\n\n"
        f"Avant : {baseline['date']}\n"
        f"CPU : {baseline.get('cpu', 0):.1f} %\n"
        f"RAM : {baseline.get('ram_percent', 0):.1f} %\n"
        f"Disque libre : {baseline.get('disk_free', 0):.1f} Go\n"
        f"Score santé : {baseline.get('score', 0)}/100\n\n"
        f"Après : {current['date']}\n"
        f"CPU : {current['cpu']:.1f} %\n"
        f"RAM : {current['ram_percent']:.1f} %\n"
        f"Disque libre : {current['disk_free']:.1f} Go\n"
        f"Score santé : {current['score']}/100\n"
    )
    popup("↔️ Comparaison", text, 650, 470)
    save_json(path, current)
    log_action("Comparaison avant/après")


# ============================================================
# Fun / technician mode / troubleshooting
# ============================================================

def open_technician_mode():
    win = ctk.CTkToplevel(app)
    win.title("🔧 Mode Technicien")
    win.geometry("700x620")
    win.transient(app)

    ctk.CTkLabel(win, text="🔧 MODE TECHNICIEN", font=("Arial", 26, "bold")).pack(pady=18)

    client = ctk.CTkEntry(win, width=500, placeholder_text="Nom du client / dossier")
    client.pack(pady=10)

    issue = ctk.CTkEntry(win, width=500, placeholder_text="Problème signalé")
    issue.pack(pady=10)

    box = ctk.CTkTextbox(win, font=("Consolas", 12))
    box.pack(fill="both", expand=True, padx=18, pady=15)

    def generate():
        name = client.get().strip() or "Non renseigné"
        problem = issue.get().strip() or "Non renseigné"
        text = (
            f"FICHE TECHNICIEN — PC HELPER V{APP_VERSION}\n\n"
            f"Client : {name}\n"
            f"Problème : {problem}\n"
            f"Date : {now_str()}\n\n"
            f"{build_full_report()}\n\n"
            "OBSERVATIONS\n"
            "____________\n"
        )
        box.delete("1.0", "end")
        box.insert("1.0", text)
        log_action("Mode Technicien", name)

    ctk.CTkButton(win, text="Générer la fiche", command=generate).pack(pady=(0, 18))


def open_troubleshooter():
    win = ctk.CTkToplevel(app)
    win.title("🧰 Mode Dépannage")
    win.geometry("650x520")
    win.transient(app)

    ctk.CTkLabel(win, text="🧰 Mode Dépannage", font=("Arial", 24, "bold")).pack(pady=20)
    choices = [
        "PC lent",
        "Internet ne fonctionne pas",
        "Manque d'espace disque",
        "Windows instable",
        "Surchauffe",
        "Problème au démarrage",
    ]
    combo = ctk.CTkComboBox(win, values=choices, width=430)
    combo.set(choices[0])
    combo.pack(pady=10)

    box = ctk.CTkTextbox(win, font=("Consolas", 13))
    box.pack(fill="both", expand=True, padx=18, pady=15)

    steps = {
        "PC lent": "1. Vérifier CPU/RAM\n2. Vérifier les processus\n3. Vérifier le démarrage\n4. Vérifier l'espace disque",
        "Internet ne fonctionne pas": "1. Test Internet\n2. Test DNS\n3. Vérifier les interfaces réseau\n4. Vérifier la passerelle",
        "Manque d'espace disque": "1. Analyse du disque\n2. Gros fichiers\n3. Nettoyage avancé\n4. État des disques",
        "Windows instable": "1. SFC\n2. DISM\n3. Historique des mises à jour\n4. Diagnostic disque",
        "Surchauffe": "1. Températures\n2. CPU en temps réel\n3. Processus\n4. Vérifier ventilation/refroidissement",
        "Problème au démarrage": "1. Démarrage Windows\n2. Programmes au démarrage\n3. Services\n4. Historique système",
    }

    def show():
        box.delete("1.0", "end")
        box.insert("1.0", steps[combo.get()])

    ctk.CTkButton(win, text="Proposer une procédure", command=show).pack(pady=(0, 18))


def open_checklist():
    win = ctk.CTkToplevel(app)
    win.title("☑️ Checklist réparation")
    win.geometry("650x650")
    win.transient(app)

    ctk.CTkLabel(win, text="☑️ CHECKLIST TECHNICIEN", font=("Arial", 24, "bold")).pack(pady=18)

    checks = [
        "État physique vérifié",
        "Températures vérifiées",
        "RAM vérifiée",
        "Stockage vérifié",
        "Réseau testé",
        "Windows vérifié",
        "Mises à jour vérifiées",
        "Antivirus / pare-feu vérifiés",
        "Tests finaux effectués",
        "Rapport remis",
    ]

    vars_ = []
    for label in checks:
        var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(win, text=label, variable=var).pack(anchor="w", padx=30, pady=7)
        vars_.append(var)

    result = ctk.CTkLabel(win, text="")
    result.pack(pady=15)

    def progress():
        done = sum(v.get() for v in vars_)
        result.configure(text=f"Progression : {done}/{len(vars_)} ({done/len(vars_)*100:.0f} %)")

    ctk.CTkButton(win, text="Mettre à jour", command=progress).pack()


def open_timer():
    win = ctk.CTkToplevel(app)
    win.title("⏱️ Minuteur")
    win.geometry("430x360")
    win.transient(app)

    ctk.CTkLabel(win, text="⏱️ Minuteur de maintenance", font=("Arial", 22, "bold")).pack(pady=20)
    entry = ctk.CTkEntry(win, width=180)
    entry.insert(0, "10")
    entry.pack(pady=10)
    ctk.CTkLabel(win, text="minutes").pack()

    label = ctk.CTkLabel(win, text="10:00", font=("Arial", 42, "bold"))
    label.pack(pady=20)

    running = {"value": False}
    remaining = {"value": 600}

    def start():
        try:
            remaining["value"] = int(float(entry.get()) * 60)
        except ValueError:
            messagebox.showerror("Valeur", "Entre un nombre de minutes.")
            return
        running["value"] = True
        tick()

    def tick():
        if not running["value"]:
            return
        sec = remaining["value"]
        label.configure(text=f"{sec // 60:02d}:{sec % 60:02d}")
        if sec <= 0:
            running["value"] = False
            messagebox.showinfo("PC Helper", "Minuteur terminé.")
            return
        remaining["value"] -= 1
        win.after(1000, tick)

    def stop():
        running["value"] = False

    ctk.CTkButton(win, text="Démarrer", command=start).pack(pady=5)
    ctk.CTkButton(win, text="Arrêter", command=stop).pack(pady=5)


def open_about():
    popup(
        "ℹ️ À propos",
        f"PC HELPER\n\nVersion {APP_VERSION}\n\n"
        f"Logiciel créé par Mathieu.\n\n"
        "Assistant de maintenance informatique.\n"
        "Projet personnel.",
        500,
        350
    )


def toggle_theme():
    mode = ctk.get_appearance_mode()
    ctk.set_appearance_mode("light" if mode == "Dark" else "dark")
    log_action("Changement thème", ctk.get_appearance_mode())



# ============================================================
# Zone de notification Windows + mises à jour
# ============================================================

def get_ram_speed_mhz():
    """Retourne la fréquence RAM configurée, en MHz, via WMI/CIM."""
    code, out, err = run_powershell(
        "Get-CimInstance Win32_PhysicalMemory | "
        "Select-Object -ExpandProperty ConfiguredClockSpeed | "
        "ConvertTo-Json -Compress"
    )
    if code != 0 or not out:
        return None
    try:
        data = json.loads(out)
        if isinstance(data, list):
            values = [int(v) for v in data if v]
            return int(round(sum(values) / len(values))) if values else None
        return int(data) if data else None
    except Exception:
        return None


def get_cpu_temperature():
    """Retourne une température exploitable en °C, si Windows expose un capteur."""
    try:
        temps = psutil.sensors_temperatures(fahrenheit=False)
        candidates = []
        for entries in temps.values():
            for item in entries:
                value = getattr(item, "current", None)
                if value is not None and 0 < float(value) < 120:
                    candidates.append(float(value))
        if candidates:
            return max(candidates)
    except Exception:
        pass

    code, out, err = run_powershell(
        "Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi | "
        "Select-Object -ExpandProperty CurrentTemperature | ConvertTo-Json -Compress"
    )
    if code == 0 and out:
        try:
            data = json.loads(out)
            if not isinstance(data, list):
                data = [data]
            values = [float(v) / 10 - 273.15 for v in data if v]
            values = [v for v in values if 0 < v < 120]
            if values:
                return max(values)
        except Exception:
            pass
    return None


def get_tray_status_text():
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    speed = get_ram_speed_mhz()
    temp = get_cpu_temperature()
    speed_text = f"{speed} MHz" if speed else "N/D"
    temp_text = f"{temp:.0f} °C" if temp is not None else "N/D"
    return (
        f"PC Helper V{APP_VERSION}\n"
        f"CPU : {cpu:.0f} %  •  RAM : {ram:.0f} %\n"
        f"Température : {temp_text}  •  RAM : {speed_text}"
    )


def show_update_window():
    """Fenêtre dédiée au contrôle et à l'installation des mises à jour."""
    win = ctk.CTkToplevel(app)
    win.title("🔄 Mises à jour PC Helper")
    win.geometry("720x520")
    win.minsize(620, 430)
    win.transient(app)

    ctk.CTkLabel(win, text="🔄 Mises à jour PC Helper", font=("Arial", 25, "bold")).pack(pady=(24, 8))
    ctk.CTkLabel(
        win,
        text=f"Version installée : V{APP_VERSION}",
        text_color=MUTED,
        font=("Arial", 13),
    ).pack(pady=(0, 18))

    status = ctk.CTkLabel(win, text="Prêt à rechercher une nouvelle version.", font=("Arial", 14), wraplength=620)
    status.pack(padx=25, pady=15)

    latest = {"version": None, "url": None, "sha256": None, "notes": ""}

    details = ctk.CTkTextbox(win, height=190, font=("Consolas", 11))
    details.pack(fill="both", expand=True, padx=25, pady=12)
    details.insert("1.0", "PC Helper vérifiera le serveur de mise à jour lorsqu'une adresse sera configurée.\n")
    details.configure(state="disabled")

    def set_details(text):
        details.configure(state="normal")
        details.delete("1.0", "end")
        details.insert("1.0", text)
        details.configure(state="disabled")

    def check_updates():
        if not UPDATE_MANIFEST_URL:
            status.configure(text="⚙️ Le système de mise à jour est prêt, mais l'adresse du serveur n'est pas encore configurée.")
            set_details(
                "Pour activer les mises à jour à distance, il faudra renseigner UPDATE_MANIFEST_URL\n"
                "dans PC Helper avec l'adresse d'un fichier update.json hébergé sur ton serveur ou GitHub.\n\n"
                "Le format prévu est :\n"
                '{"version":"4.1.1","url":"https://.../PC_Helper_Setup_V4.1.1.exe",'
                '"notes":"Corrections et améliorations"}\n'
            )
            return

        status.configure(text="🔎 Recherche d'une nouvelle version…")
        set_details("Connexion au serveur de mise à jour…")

        def worker():
            import urllib.request
            try:
                request = urllib.request.Request(UPDATE_MANIFEST_URL, headers={"User-Agent": "PC-Helper-Updater"})
                with urllib.request.urlopen(request, timeout=UPDATE_CHECK_TIMEOUT) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                remote_version = str(payload.get("version", "")).strip()
                download_url = str(payload.get("url", "")).strip()
                notes = str(payload.get("notes", "Aucune note de version."))
                if not remote_version or not download_url:
                    raise ValueError("Manifest de mise à jour incomplet.")

                def compare_versions(a, b):
                    def parts(v):
                        return tuple(int(x) if x.isdigit() else 0 for x in v.split(".")[:4])
                    return parts(a) < parts(b)

                if compare_versions(APP_VERSION, remote_version):
                    latest.update({"version": remote_version, "url": download_url, "sha256": str(payload.get("sha256", "")).strip().lower() or None, "notes": notes})
                    text = (
                        f"Nouvelle version disponible : V{remote_version}\n\n"
                        f"Version actuelle : V{APP_VERSION}\n\n"
                        f"Nouveautés :\n{notes}\n\n"
                        "Clique sur « Installer la mise à jour » pour télécharger le nouvel installateur."
                    )
                    win.after(0, lambda: (status.configure(text=f"🟢 V{remote_version} est disponible."), set_details(text), install_btn.configure(state="normal")))
                else:
                    win.after(0, lambda: (status.configure(text="✅ PC Helper est déjà à jour."), set_details(f"Version actuelle : V{APP_VERSION}\nDernière version publiée : V{remote_version}\n\n{notes}")))
            except Exception as exc:
                win.after(0, lambda: (status.configure(text="❌ Impossible de vérifier les mises à jour."), set_details(f"Erreur : {exc}\n\nVérifie l'adresse du serveur et ta connexion Internet.")))

        threading.Thread(target=worker, daemon=True).start()

    def install_update():
        if not latest.get("url"):
            messagebox.showinfo("Mises à jour", "Aucune nouvelle version prête à être installée. Lance d'abord une vérification.")
            return
        if not messagebox.askyesno("Installer la mise à jour", f"Télécharger et installer PC Helper V{latest['version']} ?\n\nPC Helper sera fermé pendant l'installation."):
            return

        status.configure(text="⬇️ Téléchargement de la mise à jour…")
        install_btn.configure(state="disabled")

        def worker():
            import hashlib as _hashlib
            import urllib.request
            try:
                filename = f"PC_Helper_Setup_V{latest['version']}.exe"
                target = Path(tempfile.gettempdir()) / filename
                request = urllib.request.Request(latest["url"], headers={"User-Agent": "PC-Helper-Updater"})
                with urllib.request.urlopen(request, timeout=UPDATE_DOWNLOAD_TIMEOUT) as response, open(target, "wb") as out_file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        out_file.write(chunk)

                expected = latest.get("sha256")
                if expected:
                    digest = _hashlib.sha256(target.read_bytes()).hexdigest().lower()
                    if digest != expected:
                        target.unlink(missing_ok=True)
                        raise ValueError("La vérification SHA-256 de l'installateur a échoué.")

                win.after(0, lambda: status.configure(text="🚀 Lancement de l'installateur…"))
                subprocess.Popen([str(target)], close_fds=True)
                win.after(700, quit_pc_helper)
            except Exception as exc:
                win.after(0, lambda: (status.configure(text="❌ Échec de l'installation."), set_details(f"Erreur : {exc}"), install_btn.configure(state="normal")))

        threading.Thread(target=worker, daemon=True).start()

    def open_download_page():
        import webbrowser
        if UPDATE_MANIFEST_URL:
            webbrowser.open(UPDATE_MANIFEST_URL)
        else:
            messagebox.showinfo("Mises à jour", "L'adresse de mise à jour n'est pas encore configurée.")

    buttons = ctk.CTkFrame(win, fg_color="transparent")
    buttons.pack(pady=(4, 22))
    ctk.CTkButton(buttons, text="🔎 Vérifier les mises à jour", width=230, height=42, command=check_updates).pack(side="left", padx=7)
    install_btn = ctk.CTkButton(buttons, text="⬇️ Installer la mise à jour", width=210, height=42, command=install_update, state="disabled", fg_color=ACCENT_2, text_color="#08111f")
    install_btn.pack(side="left", padx=7)
    ctk.CTkButton(buttons, text="Fermer", width=120, height=42, command=win.destroy).pack(side="left", padx=7)
    ctk.CTkButton(buttons, text="Fermer", width=120, height=42, command=win.destroy).pack(side="left", padx=7)


def make_tray_image():
    image = Image.new("RGBA", (64, 64), (8, 17, 31, 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(18, 35, 58, 255), outline=(103, 232, 249, 255), width=3)
    draw.text((11, 17), "PC", fill=(103, 232, 249, 255), font=None)
    return image


def start_tray_icon():
    """Démarre l'icône de zone de notification sur son propre thread."""
    if pystray is None:
        return

    def show_app(icon=None, item=None):
        app.after(0, restore_from_tray)

    def update_now(icon=None, item=None):
        app.after(0, show_update_window)

    def quit_app(icon=None, item=None):
        app.after(0, quit_pc_helper)

    def tray_stats_text(item=None):
        try:
            return "📊 " + get_tray_status_text().replace("\n", "  |  ")
        except Exception:
            return f"📊 PC Helper V{APP_VERSION}"

    menu = pystray.Menu(
        TrayItem("🟢 PC Helper actif", show_app, enabled=False),
        TrayItem(tray_stats_text, None, enabled=False),
        TrayItem("📊 Ouvrir PC Helper", show_app),
        TrayItem("🔄 Vérifier les mises à jour", update_now),
        TrayItem("❌ Quitter PC Helper", quit_app),
    )
    tray = pystray.Icon("PC Helper", make_tray_image(), f"PC Helper V{APP_VERSION}", menu)
    tray.run_detached()
    return tray


def refresh_tray_title():
    if not app.winfo_exists():
        return
    if tray_icon is not None:
        try:
            tray_icon.title = get_tray_status_text()
        except Exception:
            pass
    app.after(5000, refresh_tray_title)


def restore_from_tray():
    app.deiconify()
    app.state("normal")
    app.lift()
    app.focus_force()


def hide_to_tray():
    app.withdraw()


def quit_pc_helper():
    global tray_icon
    try:
        if tray_icon is not None:
            tray_icon.stop()
    except Exception:
        pass
    app.destroy()


# ============================================================
# ============================================================
# Modern glass dashboard UI — PC Helper
# ============================================================
app = ctk.CTk()
app.title(f"{APP_NAME} — Créé par {APP_AUTHOR}")
app.geometry("1180x760")
app.minsize(1050, 680)

# Palette inspirée de l'interface de référence : verre sombre + accents cyan/violet.
ACCENT = "#67E8F9"
ACCENT_2 = "#A78BFA"
TEXT = "#F8FAFC"
MUTED = "#B8C2D1"
GLASS = "transparent"
GLASS_SOLID = "#101827"
GLASS_2 = "#172033"
BORDER = "#34445F"
SUCCESS = "#34D399"

# ---------- wallpaper / glass effect ----------
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
BG_PATH = BASE_DIR / "pc_background.png"
_bg_original = None
_bg_photo = None
bg_label = None

def _build_glass_wallpaper(width, height):
    """Crée un fond photo assombri avec des zones de verre intégrées.

    CustomTkinter ne gère pas directement l'alpha sur les CTkFrame.
    Les zones translucides sont donc pré-composées dans le wallpaper,
    tandis que les widgets restent transparents au-dessus.
    """
    if _bg_original is None or width <= 0 or height <= 0:
        return None
    base = _bg_original.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")

    # Assombrissement général pour conserver une excellente lisibilité.
    shade = Image.new("RGBA", (width, height), (5, 10, 18, 78))
    base = Image.alpha_composite(base, shade)

    # Voile sombre sur la barre latérale.
    sidebar_w = max(220, min(260, int(width * 0.20)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, sidebar_w, height), fill=(8, 15, 28, 205))

    # Grand voile très léger sur la zone principale.
    draw.rectangle((sidebar_w, 0, width, height), fill=(8, 12, 20, 48))

    # Cartes principales : fond semi-transparent + bordure lumineuse.
    content_x = sidebar_w + int(width * 0.025)
    content_w = width - content_x - int(width * 0.025)
    gap = max(8, int(width * 0.008))
    card_y = int(height * 0.46)
    card_h = int(height * 0.145)
    card_w = int((content_w - 3 * gap) / 4)
    for i in range(4):
        x1 = content_x + i * (card_w + gap)
        x2 = x1 + card_w
        draw.rounded_rectangle((x1, card_y, x2, card_y + card_h), radius=16, fill=(9, 18, 32, 158), outline=(80, 220, 255, 105), width=1)

    lower_y = int(height * 0.635)
    lower_h = height - lower_y - int(height * 0.025)
    left_w = int(content_w * 0.62)
    right_x = content_x + left_w + gap
    right_w = content_w - left_w - gap
    draw.rounded_rectangle((content_x, lower_y, content_x + left_w, lower_y + lower_h), radius=18, fill=(9, 18, 32, 165), outline=(80, 220, 255, 95), width=1)
    draw.rounded_rectangle((right_x, lower_y, right_x + right_w, lower_y + lower_h), radius=18, fill=(9, 18, 32, 165), outline=(167, 139, 250, 95), width=1)

    # Légère lumière cyan/violette pour l'identité visuelle.
    draw.ellipse((int(width*0.62), int(height*0.12), int(width*0.92), int(height*0.55)), fill=(20, 120, 180, 18))
    draw.ellipse((int(width*0.72), int(height*0.45), int(width*1.02), int(height*0.90)), fill=(130, 70, 210, 15))

    return Image.alpha_composite(base, overlay).convert("RGB")

if BG_PATH.is_file():
    try:
        _bg_original = Image.open(BG_PATH).convert("RGB")
        _bg_photo = ImageTk.PhotoImage(_build_glass_wallpaper(1180, 760))
        bg_label = tk.Label(app, image=_bg_photo, borderwidth=0, highlightthickness=0)
        bg_label.place(x=0, y=0, relwidth=1, relheight=1)
        bg_label.lower()

        def _resize_background(event):
            global _bg_photo
            if event.widget is app and event.width > 0 and event.height > 0 and bg_label is not None:
                rendered = _build_glass_wallpaper(event.width, event.height)
                if rendered is not None:
                    _bg_photo = ImageTk.PhotoImage(rendered)
                    bg_label.configure(image=_bg_photo)

        app.bind("<Configure>", _resize_background, add="+")
    except Exception as exc:
        print(f"Impossible de charger le fond {BG_PATH}: {exc}")
else:
    print(f"Fond introuvable : {BG_PATH}")

# ---------- helpers for glass panels ----------
def glass_card(parent, radius=18, border=True):
    return ctk.CTkFrame(
        parent,
        corner_radius=radius,
        fg_color="transparent",
        border_width=1 if border else 0,
        border_color=BORDER,
    )


def clear_content():
    for child in content.winfo_children():
        child.destroy()


def section_title(parent, title, subtitle=""):
    head = ctk.CTkFrame(parent, fg_color="transparent")
    head.pack(fill="x", padx=30, pady=(26, 10))
    ctk.CTkLabel(head, text=title, text_color=TEXT, font=("Arial", 28, "bold")).pack(anchor="w")
    if subtitle:
        ctk.CTkLabel(head, text=subtitle, text_color=MUTED, font=("Arial", 12)).pack(anchor="w", pady=(3, 0))
    return head


# ---------- shell ----------
sidebar = ctk.CTkFrame(app, width=236, corner_radius=0, fg_color="transparent", border_width=1, border_color=BORDER)
sidebar.pack(side="left", fill="y")
sidebar.pack_propagate(False)

content = ctk.CTkFrame(app, corner_radius=0, fg_color="transparent")
content.pack(side="right", fill="both", expand=True)

brand = ctk.CTkFrame(sidebar, fg_color="transparent")
brand.pack(fill="x", padx=18, pady=(20, 5))
ctk.CTkLabel(brand, text="◈", text_color=ACCENT, font=("Arial", 30, "bold")).pack(side="left")
brand_text = ctk.CTkFrame(brand, fg_color="transparent")
brand_text.pack(side="left", padx=9)
ctk.CTkLabel(brand_text, text="PC", text_color=TEXT, font=("Arial", 22, "bold"), anchor="w").pack(anchor="w")
ctk.CTkLabel(brand_text, text="Helper", text_color=ACCENT, font=("Arial", 18, "bold"), anchor="w").pack(anchor="w")
ctk.CTkLabel(sidebar, text=f"V{APP_VERSION}  •  Maintenance & optimisation", text_color=MUTED, font=("Arial", 9)).pack(anchor="w", padx=22, pady=(0, 18))

nav_buttons = {}

def nav_button(label, command):
    btn = ctk.CTkButton(
        sidebar, text=label, command=command, height=40, corner_radius=10,
        anchor="w", fg_color="transparent", hover_color="#243149",
        text_color=TEXT, font=("Arial", 11, "bold")
    )
    btn.pack(fill="x", padx=12, pady=3)
    nav_buttons[label] = btn
    return btn


def set_active(label):
    for key, btn in nav_buttons.items():
        btn.configure(fg_color="#263650" if key == label else "transparent", text_color=TEXT)



def lancer_diagnostic():
    """Diagnostic rapide et non destructif du PC."""
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(system_drive())
        boot = psutil.boot_time()
        net = psutil.net_io_counters()
        issues = []
        if cpu >= 90:
            issues.append(f"CPU élevé : {cpu:.0f} %")
        if ram.percent >= 90:
            issues.append(f"Mémoire élevée : {ram.percent:.0f} %")
        if disk.percent >= 90:
            issues.append(f"Disque système presque plein : {disk.percent:.0f} % utilisé")
        score = calculate_health_score()
        status = "Aucun problème critique détecté." if not issues else "Points à surveiller :\n• " + "\n• ".join(issues)
        text = (
            "DIAGNOSTIC PC HELPER 4.0\n"
            "=" * 34 + "\n\n"
            f"Système : {platform.system()} {platform.release()}\n"
            f"Ordinateur : {platform.node()}\n"
            f"CPU : {cpu:.0f} %\n"
            f"RAM : {ram.percent:.0f} % ({go(ram.available):.1f} Go disponibles)\n"
            f"Disque système : {disk.percent:.0f} % utilisé ({go(disk.free):.1f} Go libres)\n"
            f"Démarré depuis : {fmt_duration(time.time() - boot)}\n"
            f"Réseau : {'Actif' if psutil.net_if_stats() else 'Inactif'}\n\n"
            f"Score de santé : {score}/100\n\n"
            f"{status}"
        )
        log_action("Diagnostic rapide", f"Score {score}/100")
        # La zone "details" de l'accueil est locale à show_home(); le diagnostic
        # doit fonctionner depuis n'importe quel menu. Le résultat complet est
        # déjà affiché dans la fenêtre de diagnostic.
        popup("🔍 Diagnostic PC", text, 780, 600)
    except Exception as exc:
        messagebox.showerror("Diagnostic", f"Le diagnostic a rencontré une erreur :\n{exc}")


def lancer_nettoyage():
    """Nettoyage prudent du dossier temporaire de l'utilisateur."""
    temp_dir = Path(tempfile.gettempdir())
    removed = 0
    freed = 0
    errors = 0
    for item in temp_dir.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                size = item.stat().st_size
                item.unlink()
                removed += 1
                freed += size
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
                removed += 1
        except Exception:
            errors += 1
    log_action("Nettoyage rapide", f"Éléments supprimés : {removed}; erreurs : {errors}")
    popup(
        "🧹 Nettoyage rapide",
        f"Nettoyage du dossier temporaire terminé.\n\nÉléments supprimés : {removed}\nEspace libéré : {go(freed):.2f} Go\nÉléments non accessibles : {errors}\n\nLes fichiers utilisés par Windows ou les applications ont été ignorés.",
        700, 460,
    )


def afficher_infos_pc():
    info = get_basic_info()
    lines = [
        "INFORMATIONS DU PC",
        "=" * 30,
        f"Système : {info.get('system', 'N/A')}",
        f"Version : {info.get('version', 'N/A')}",
        f"Nom du PC : {info.get('hostname', 'N/A')}",
        f"Processeur : {info.get('cpu', 'N/A')}",
        f"Cœurs logiques : {psutil.cpu_count(logical=True) or 'N/A'}",
        f"RAM totale : {go(psutil.virtual_memory().total):.1f} Go",
        f"Disque système : {go(psutil.disk_usage(system_drive()).total):.1f} Go",
    ]
    log_action("Informations système")
    popup("💻 Informations du PC", "\n".join(lines), 760, 560)


def optimize_ram():
    """Réduit la mémoire résidente des processus sans fermer les applications."""
    before = psutil.virtual_memory()
    processed = 0
    failed = 0
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_SET_QUOTA = 0x0100
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
        OpenProcess.restype = ctypes.wintypes.HANDLE
        CloseHandle = kernel32.CloseHandle
        EmptyWorkingSet = psapi.EmptyWorkingSet
        EmptyWorkingSet.argtypes = [ctypes.wintypes.HANDLE]
        EmptyWorkingSet.restype = ctypes.wintypes.BOOL
        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info["pid"] == os.getpid():
                continue
            handle = None
            try:
                handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA, False, proc.info["pid"])
                if handle and EmptyWorkingSet(handle):
                    processed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            finally:
                if handle:
                    CloseHandle(handle)
    after = psutil.virtual_memory()
    released = max(0, before.available - after.available)
    log_action("Optimisation RAM", f"Processus traités : {processed}; gain estimé : {go(released):.2f} Go")
    popup(
        "🚀 Optimisation RAM",
        f"Optimisation terminée.\n\nMémoire disponible avant : {go(before.available):.2f} Go\nMémoire disponible après : {go(after.available):.2f} Go\nGain constaté : {go(released):.2f} Go\n\nProcessus traités : {processed}\nProcessus non accessibles : {failed}\n\nAucune application n'a été fermée.",
        760, 560,
    )

def show_home():
    """Accueil glassmorphism : photo visible à travers de vrais panneaux en verre."""
    clear_content(); set_active("⌂  Accueil")

    canvas = tk.Canvas(content, highlightthickness=0, bd=0, bg="#08111f")
    canvas.pack(fill="both", expand=True)
    state = {"photo": None, "w": 0, "h": 0}

    def crop_background(w, h):
        if _bg_original is None or w <= 0 or h <= 0:
            return Image.new("RGB", (w, h), "#08111f")
        src = _bg_original.copy().convert("RGB")
        ratio = max(w / src.width, h / src.height)
        nw, nh = max(w, int(src.width * ratio)), max(h, int(src.height * ratio))
        src = src.resize((nw, nh), Image.Resampling.LANCZOS)
        left, top = (nw - w) // 2, (nh - h) // 2
        return src.crop((left, top, left + w, top + h))

    def glass_panel(base, box, radius=20, tint=(7, 18, 34), alpha=108, blur=3, border=(103, 232, 249, 120)):
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(base.width, x2), min(base.height, y2)
        if x2 <= x1 or y2 <= y1:
            return
        # Verre véritable : on reprend la photo située sous la carte, on la floute
        # légèrement, puis on lui applique une teinte semi-transparente.
        from PIL import ImageDraw, ImageFilter
        region = base.crop((x1, y1, x2, y2)).convert("RGBA")
        if blur:
            region = region.filter(ImageFilter.GaussianBlur(blur))
        veil = Image.new("RGBA", region.size, (*tint, alpha))
        region = Image.alpha_composite(region, veil)
        base.paste(region.convert("RGB"), (x1, y1))
        draw = ImageDraw.Draw(base, "RGBA")
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, outline=border, width=1)

    def render():
        w = max(900, canvas.winfo_width())
        h = max(600, canvas.winfo_height())
        base = crop_background(w, h).convert("RGBA")
        # Assombrissement global léger : la photo reste bien lisible.
        veil = Image.new("RGBA", (w, h), (3, 7, 15, 38))
        base = Image.alpha_composite(base, veil).convert("RGB")

        # Voile translucide du panneau latéral.
        glass_panel(base, (0, 0, 236, h), radius=0, tint=(5, 12, 25), alpha=108, blur=3,
                    border=(103, 232, 249, 70))

        left = 258
        right = w - 28
        gap = 12
        card_y = 105
        card_h = 112
        card_w = (right - left - gap * 3) / 4
        for i in range(4):
            x = left + i * (card_w + gap)
            glass_panel(base, (x, card_y, x + card_w, card_y + card_h), radius=18,
                        tint=(7, 18, 34), alpha=76, blur=2,
                        border=(103, 232, 249, 105))

        lower_y = 232
        lower_bottom = h - 24
        lower_w = (right - left - gap) * 0.64
        glass_panel(base, (left, lower_y, left + lower_w, lower_bottom), radius=20,
                    tint=(7, 18, 34), alpha=82, blur=3,
                    border=(103, 232, 249, 105))
        glass_panel(base, (left + lower_w + gap, lower_y, right, lower_bottom), radius=20,
                    tint=(7, 18, 34), alpha=82, blur=3,
                    border=(167, 139, 250, 110))

        # Zone de titre : verre très léger pour laisser respirer la photo.
        glass_panel(base, (left - 8, 18, min(right, left + 560), 91), radius=14,
                    tint=(4, 12, 24), alpha=34, blur=2,
                    border=(103, 232, 249, 40))

        state["photo"] = ImageTk.PhotoImage(base)
        canvas.delete("all")
        canvas.create_image(0, 0, image=state["photo"], anchor="nw")

        # ---------- contenu textuel ----------
        # Titre en deux couleurs, avec un espacement fixe pour éviter tout chevauchement.
        canvas.create_text(left, 43, text="Bienvenue sur", anchor="w", fill=TEXT,
                           font=("Arial", 28, "bold"))
        title_offset = 285
        canvas.create_text(left + title_offset, 43, text="PC Helper", anchor="w", fill="#38D5FF",
                           font=("Arial", 28, "bold"))
        canvas.create_text(left, 76, text="Surveillez, entretenez et optimisez votre PC.",
                           anchor="w", fill=MUTED, font=("Arial", 12))
        canvas.create_text(right, 40, text=datetime.now().strftime("%A %d %B %Y  •  %H:%M"),
                           anchor="e", fill=TEXT, font=("Arial", 9))

        stats = [("⚙", "CPU", "cpu"), ("▦", "RAM", "ram"), ("▣", "DISQUE", "disk"), ("⌁", "RÉSEAU", "net")]
        info = get_basic_info()
        vals = {"cpu": f"{psutil.cpu_percent(interval=None):.0f} %",
                "ram": f"{psutil.virtual_memory().percent:.0f} %",
                "disk": f"{go(psutil.disk_usage(system_drive()).free):.0f} Go libres",
                "net": "Connecté" if psutil.net_if_stats() else "Inactif"}
        for i, (icon, label, key) in enumerate(stats):
            x = left + i * (card_w + gap)
            canvas.create_text(x + 18, card_y + 25, text=icon, anchor="w", fill=ACCENT,
                               font=("Arial", 21, "bold"))
            canvas.create_text(x + 18, card_y + 54, text=label, anchor="w", fill=MUTED,
                               font=("Arial", 9, "bold"))
            canvas.create_text(x + 18, card_y + 84, text=vals[key], anchor="w", fill=TEXT,
                               font=("Arial", 20, "bold"))

        # ---------- état général ----------
        lx1, ly1 = left + 22, lower_y + 31
        canvas.create_text(lx1, ly1, text="État général du PC", anchor="w", fill=TEXT,
                           font=("Arial", 20, "bold"))
        score = calculate_health_score()
        canvas.create_text(lx1, ly1 + 40,
                           text=(f"●  Tout est en bon état !   {score}/100" if score >= 85
                                 else f"●  À surveiller   {score}/100"),
                           anchor="w", fill=SUCCESS if score >= 85 else "#FBBF24",
                           font=("Arial", 16, "bold"))
        ram = psutil.virtual_memory(); disk = psutil.disk_usage(system_drive())
        details = (f"CPU : {psutil.cpu_percent(interval=None):.0f} %    •    "
                   f"RAM : {ram.percent:.0f} %   ({go(ram.available):.1f} Go disponibles)\n"
                   f"Disque : {go(disk.free):.1f} Go libres    •    "
                   f"Uptime : {fmt_duration(time.time() - psutil.boot_time())}\n"
                   f"Réseau : {'Connecté' if psutil.net_if_stats() else 'Inactif'}")
        canvas.create_text(lx1, ly1 + 84, text=details, anchor="nw", fill=MUTED,
                           font=("Arial", 12))

        # Boutons d'action : zones intégrées au canvas, donc aucun cadre opaque ne masque la photo.
        def action_button(x1, y1, x2, y2, text, tag, fill_rgba=(12, 35, 58, 205), accent=None):
            from PIL import ImageDraw
            # Le bouton est dessiné sur une nouvelle image locale pour obtenir un vrai alpha.
            overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(overlay, "RGBA")
            d.rounded_rectangle((x1, y1, x2, y2), radius=11, fill=fill_rgba,
                                outline=(*((accent or (103,232,249))), 150), width=1)
            base_rgba = base.convert("RGBA")
            base_rgba.alpha_composite(overlay)
            state["photo"] = ImageTk.PhotoImage(base_rgba.convert("RGB"))
            canvas.itemconfigure("BGIMAGE", image=state["photo"]) if canvas.find_withtag("BGIMAGE") else None
            canvas.create_text((x1+x2)//2, (y1+y2)//2, text=text, fill=TEXT,
                               font=("Arial", 11, "bold"), tags=(tag, "ACTION"))
            canvas.tag_bind(tag, "<Enter>", lambda e: canvas.configure(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda e: canvas.configure(cursor=""))

        # Réécriture propre des deux boutons principaux directement sur le canvas.
        bx = lx1
        by = lower_bottom - 105
        bw = min(300, int(lower_w / 2) - 18)
        bh = 52
        canvas.create_rectangle(bx, by, bx + bw, by + bh, fill="#0D8FEA",
                                outline="#38D5FF", width=1, tags="diag_btn")
        canvas.create_text(bx + bw/2, by + bh/2, text="⌕  Lancer un diagnostic", fill="white",
                           font=("Arial", 12, "bold"), tags="diag_btn")
        canvas.tag_bind("diag_btn", "<Button-1>", lambda e: lancer_diagnostic())
        canvas.tag_bind("diag_btn", "<Enter>", lambda e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("diag_btn", "<Leave>", lambda e: canvas.configure(cursor=""))

        bx2 = bx + bw + 18
        canvas.create_rectangle(bx2, by, bx2 + bw, by + bh, fill="#8F63F4",
                                outline="#BFA8FF", width=1, tags="ram_btn")
        canvas.create_text(bx2 + bw/2, by + bh/2, text="🚀  Optimiser la RAM", fill="#08111f",
                           font=("Arial", 12, "bold"), tags="ram_btn")
        canvas.tag_bind("ram_btn", "<Button-1>", lambda e: optimize_ram())
        canvas.tag_bind("ram_btn", "<Enter>", lambda e: canvas.configure(cursor="hand2"))
        canvas.tag_bind("ram_btn", "<Leave>", lambda e: canvas.configure(cursor=""))

        # ---------- actions rapides ----------
        rx1 = int(left + lower_w + gap + 22)
        ry = lower_y + 31
        canvas.create_text(rx1, ry, text="Actions rapides", anchor="w", fill=TEXT,
                           font=("Arial", 20, "bold"))
        quick = [("🧹  Nettoyage rapide", lancer_nettoyage),
                 ("💻  Informations système", afficher_infos_pc),
                 ("📄  Rapport complet", open_full_report),
                 ("📊  Surveillance temps réel", open_realtime_monitor),
                 ("🔄  Vérifier les mises à jour", show_update_window)]
        for i, (label, cmd) in enumerate(quick):
            yy = ry + 48 + i * 56
            tag = f"quick_{i}"
            canvas.create_rectangle(rx1, yy, right - 22, yy + 40, outline=BORDER,
                                    fill="#0B1A2C", width=1, tags=tag)
            canvas.create_text(rx1 + 15, yy + 20, text=label, anchor="w", fill=TEXT,
                               font=("Arial", 10), tags=tag)
            canvas.create_text(right - 38, yy + 20, text="›", fill=ACCENT,
                               font=("Arial", 18, "bold"), tags=tag)
            canvas.tag_bind(tag, "<Button-1>", lambda e, f=cmd: f())
            canvas.tag_bind(tag, "<Enter>", lambda e: canvas.configure(cursor="hand2"))
            canvas.tag_bind(tag, "<Leave>", lambda e: canvas.configure(cursor=""))

        canvas.create_text(right - 20, lower_bottom - 43, text="« Un PC bien entretenu\ndure plus longtemps. »",
                           anchor="e", fill=MUTED, font=("Arial", 10, "italic"), justify="right")

    canvas.bind("<Configure>", lambda e: render())
    render()


def show_monitor_menu():

    set_active("🖥  Surveillance")
    show_menu("🖥️  Surveillance","Performances, températures et état du matériel",[("PERFORMANCES",[("📈  Tableau de bord",open_dashboard),("📊  Moniteur temps réel",open_realtime_monitor),("🚀  Optimisation RAM",optimize_ram)]),("MATÉRIEL",[("💻  Informations du PC",afficher_infos_pc),("🌡️  Températures",open_temperatures),("🔋  Batterie",open_battery),("🧩  Carte matériel",open_hardware),("❤️  Score de santé",lambda:popup("❤️ Score de santé",f"Score indicatif : {calculate_health_score()}/100",500,330))])])


def show_menu(title,subtitle,groups):
    clear_content(); section_title(content,title,subtitle)
    box=ctk.CTkScrollableFrame(content,fg_color="transparent"); box.pack(fill="both",expand=True,padx=25,pady=(0,22))
    for group_title,buttons in groups:
        ctk.CTkLabel(box,text=group_title,text_color=ACCENT,font=("Arial",10,"bold")).pack(anchor="w",padx=8,pady=(8,7))
        panel=glass_card(box,16); panel.pack(fill="x",pady=(0,14))
        for i,(txt,cmd) in enumerate(buttons):
            panel.grid_columnconfigure(i%2,weight=1)
            ctk.CTkButton(panel,text=txt,command=cmd,height=46,corner_radius=10,fg_color="transparent",border_width=1,border_color=BORDER,hover_color="#243149",anchor="w").grid(row=i//2,column=i%2,padx=9,pady=8,sticky="ew")

nav_button("⌂  Accueil",show_home)
nav_button("🖥  Surveillance",show_monitor_menu)
nav_button("💾  Stockage",lambda:(set_active("💾  Stockage"),show_menu("💾  Stockage","Analyse, nettoyage et gestion de l'espace disque",[("STOCKAGE",[("💾  Analyse du disque",open_storage_analysis),("📦  Gros fichiers",open_large_files),("🔁  Doublons",open_duplicates),("🧹  Nettoyage avancé",open_advanced_cleanup),("💿  État des disques",open_disk_state)])])))
nav_button("⌁  Réseau",lambda:(set_active("⌁  Réseau"),show_menu("🌐  Réseau","Diagnostic de la connexion et des interfaces réseau",[("RÉSEAU",[("🌐  Test Internet",open_network_test),("📡  Informations réseau",open_network_info),("🧭  Test DNS",open_dns_test),("📡  Scanner LAN",open_lan_scanner),("📚  Historique réseau",open_connection_history)])])))
nav_button("🛠  Technicien",lambda:(set_active("🛠  Technicien"),show_menu("🛠️  Technicien","Outils avancés pour le dépannage et la maintenance Windows",[("WINDOWS",[("🛠️  SFC / DISM",open_sfc_dism),("💿  CHKDSK",open_chkdsk),("🚀  Démarrage",open_startup),("⚙️  Services",open_services),("📊  Processus",open_processes),("🧩  Pilotes",open_drivers),("🔄  Mises à jour",open_windows_updates)])])))
nav_button("🛡  Sécurité",lambda:(set_active("🛡  Sécurité"),show_menu("🔐  Sécurité","Vérifiez les protections essentielles de Windows",[("PROTECTION",[("🧱  Pare-feu",open_firewall),("🛡️  Defender",open_defender),("🔐  Mises à jour sécurité",open_security_updates),("🔎  Audit sécurité",open_security_audit),("🛡️  Score /100",open_security_score)])])))
nav_button("📄  Rapports",lambda:(set_active("📄  Rapports"),show_menu("📄  Rapports","Historique, export et comparaison des interventions",[("RAPPORTS",[("📄  Rapport complet",open_full_report),("📤  Export TXT / CSV / HTML",export_report),("📚  Historique",open_history),("↔️  Avant / Après",open_compare_before_after)])])))
nav_button("🧰  Utilitaires",lambda:(set_active("🧰  Utilitaires"),show_menu("🧰  Utilitaires","Outils complémentaires de PC Helper",[("OUTILS",[("🔧  Mode Technicien",open_technician_mode),("🧰  Mode Dépannage",open_troubleshooter),("☑️  Checklist",open_checklist),("⏱️  Minuteur",open_timer),("🌗  Thème clair / sombre",toggle_theme),("ℹ️  À propos",open_about)])])))
nav_button("🔄  Mises à jour",show_update_window)

ctk.CTkLabel(sidebar,text="Créé par Mathieu\nMaintenance • Diagnostic • Optimisation",text_color="#8290A8",font=("Arial",9),wraplength=190,justify="left").pack(side="bottom",anchor="w",padx=22,pady=18)

# ---------- zone de notification ----------
tray_icon = None
if pystray is not None:
    try:
        tray_icon = start_tray_icon()
    except Exception as exc:
        print(f"Zone de notification indisponible : {exc}")

app.protocol("WM_DELETE_WINDOW", hide_to_tray)
show_home()
app.after(5000, refresh_tray_title)
log_action("Démarrage PC Helper", f"V{APP_VERSION}")
app.mainloop()
