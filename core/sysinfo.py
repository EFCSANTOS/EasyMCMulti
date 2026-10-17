"""Infos systeme : RAM totale (pour proposer une allocation raisonnable)."""
import ctypes


def total_ram_mb():
    """RAM physique totale en Mo (Windows). 0 si indeterminable."""
    try:
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MemoryStatusEx()
        stat.dwLength = ctypes.sizeof(MemoryStatusEx)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return int(stat.ullTotalPhys // (1024 * 1024))
    except (OSError, AttributeError):
        return 0


def recommended_ram_mb():
    """Allocation conseillee : ~60% de la RAM totale, bornee a [1024, 12288] Mo."""
    total = total_ram_mb()
    if total <= 0:
        return 4096
    reco = int(total * 0.6)
    return max(1024, min(reco, 12288))


def max_safe_ram_mb():
    """Plafond a ne pas depasser : RAM totale - 2 Go pour l'OS (min 1024)."""
    total = total_ram_mb()
    if total <= 0:
        return 8192
    return max(1024, total - 2048)
