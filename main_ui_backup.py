import csv
import hashlib
import html
import ipaddress
import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import winreg
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import psutil


APP_NAME = "PC Helper"
APP_VERSION = "3.0"
APP_AUTHOR = "Mathieu"
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
    code, out, err = run_powershell(
        "Get-PnpDevice | Select-Object Status,Class,FriendlyName,InstanceId | "
        "Sort-Object Status,Class,FriendlyName | Format-Table -AutoSize | Out-String"
    )
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
# Main UI
# ============================================================

app = ctk.CTk()
app.title(f"{APP_NAME} — Créé par {APP_AUTHOR}")
app.geometry("920x820")
app.minsize(820, 700)

header = ctk.CTkFrame(app)
header.pack(fill="x", padx=20, pady=18)

ctk.CTkLabel(
    header,
    text="PC HELPER",
    font=("Arial", 34, "bold")
).pack(pady=(18, 3))

ctk.CTkLabel(
    header,
    text=f"Assistant de maintenance informatique • V{APP_VERSION}",
    font=("Arial", 15)
).pack(pady=(0, 6))

ctk.CTkLabel(
    header,
    text=f"© 2026 {APP_AUTHOR} — Créateur de PC Helper",
    font=("Arial", 14, "bold")
).pack(pady=(0, 18))

tabs = ctk.CTkTabview(app)
tabs.pack(fill="both", expand=True, padx=20, pady=(0, 20))

tab_monitor = tabs.add("🖥️ Surveillance")
tab_storage = tabs.add("💾 Stockage")
tab_network = tabs.add("🌐 Réseau")
tab_tools = tabs.add("🛠️ Technicien")
tab_security = tabs.add("🔐 Sécurité")
tab_reports = tabs.add("📄 Rapports")
tab_fun = tabs.add("😎 Utilitaires")


def add_button(parent, text, command, row, col=0, colspan=1):
    btn = ctk.CTkButton(parent, text=text, command=command, height=48, width=280)
    btn.grid(row=row, column=col, columnspan=colspan, padx=12, pady=10, sticky="ew")
    return btn


for frame in (tab_monitor, tab_storage, tab_network, tab_tools, tab_security, tab_reports, tab_fun):
    frame.grid_columnconfigure(0, weight=1)
    frame.grid_columnconfigure(1, weight=1)


# Surveillance
add_button(tab_monitor, "📈 Tableau de bord", open_dashboard, 0, 0)
add_button(tab_monitor, "📊 Moniteur en temps réel", open_realtime_monitor, 0, 1)
add_button(tab_monitor, "💻 Informations du PC", lambda: popup("💻 Infos PC", json.dumps(get_basic_info(), indent=2, ensure_ascii=False), 650, 520), 1, 0)
add_button(tab_monitor, "🌡️ Températures", open_temperatures, 1, 1)
add_button(tab_monitor, "🔋 Batterie", open_battery, 2, 0)
add_button(tab_monitor, "🧩 Carte matériel", open_hardware, 2, 1)
add_button(tab_monitor, "❤️ Score de santé", lambda: popup("❤️ Score de santé", f"Score indicatif : {calculate_health_score()}/100", 500, 330), 3, 0, 2)

# Stockage
add_button(tab_storage, "💾 Analyse du disque", open_storage_analysis, 0, 0)
add_button(tab_storage, "📦 Gros fichiers", open_large_files, 0, 1)
add_button(tab_storage, "🔁 Doublons", open_duplicates, 1, 0)
add_button(tab_storage, "🧹 Nettoyage avancé", open_advanced_cleanup, 1, 1)
add_button(tab_storage, "💿 État des disques", open_disk_state, 2, 0, 2)

# Network
add_button(tab_network, "🌐 Test Internet", open_network_test, 0, 0)
add_button(tab_network, "📡 Informations réseau", open_network_info, 0, 1)
add_button(tab_network, "🧭 Test DNS", open_dns_test, 1, 0)
add_button(tab_network, "📡 Scanner réseau local", open_lan_scanner, 1, 1)
add_button(tab_network, "📚 Historique réseau", open_connection_history, 2, 0, 2)

# Tools
add_button(tab_tools, "🛠️ SFC / DISM", open_sfc_dism, 0, 0)
add_button(tab_tools, "💿 CHKDSK", open_chkdsk, 0, 1)
add_button(tab_tools, "🚀 Programmes au démarrage", open_startup, 1, 0)
add_button(tab_tools, "⚙️ Services Windows", open_services, 1, 1)
add_button(tab_tools, "📊 Processus", open_processes, 2, 0)
add_button(tab_tools, "🧩 Pilotes / périphériques", open_drivers, 2, 1)
add_button(tab_tools, "🔄 Mises à jour Windows", open_windows_updates, 3, 0, 2)

# Security
add_button(tab_security, "🧱 État du pare-feu", open_firewall, 0, 0)
add_button(tab_security, "🛡️ Microsoft Defender", open_defender, 0, 1)
add_button(tab_security, "🔐 Mises à jour de sécurité", open_security_updates, 1, 0)
add_button(tab_security, "🔎 Audit sécurité", open_security_audit, 1, 1)
add_button(tab_security, "🛡️ Score sécurité /100", open_security_score, 2, 0, 2)

# Reports
add_button(tab_reports, "📄 Rapport complet", open_full_report, 0, 0)
add_button(tab_reports, "📤 Export TXT / CSV / HTML", export_report, 0, 1)
add_button(tab_reports, "📚 Historique des diagnostics", open_history, 1, 0)
add_button(tab_reports, "↔️ Comparaison avant / après", open_compare_before_after, 1, 1)

# Fun / technician extras
add_button(tab_fun, "🔧 Mode Technicien", open_technician_mode, 0, 0)
add_button(tab_fun, "🧰 Mode Dépannage", open_troubleshooter, 0, 1)
add_button(tab_fun, "☑️ Checklist réparation", open_checklist, 1, 0)
add_button(tab_fun, "⏱️ Minuteur maintenance", open_timer, 1, 1)
add_button(tab_fun, "🌗 Thème clair / sombre", toggle_theme, 2, 0)
add_button(tab_fun, "ℹ️ À propos", open_about, 2, 1)


log_action("Démarrage PC Helper", f"V{APP_VERSION}")
app.mainloop()
