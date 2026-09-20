"""Read a GPS fix without writing to, reconfiguring, or claiming generic ports."""
from __future__ import annotations

import math
import time


def parse_gga(line: str) -> dict | None:
    """Accept checksum-verified GGA fixes only; never log a raw GPS sentence."""
    try:
        body, checksum = line.strip().removeprefix("$").split("*")
        value = 0
        for char in body:
            value ^= ord(char)
        if value != int(checksum, 16):
            return None
        fields = body.split(",")
        if fields[0] not in ("GPGGA", "GNGGA") or int(fields[6]) <= 0 or fields[10] != "M":
            return None
        def coord(raw: str, hemisphere: str, positive: str, negative: str) -> float:
            if hemisphere not in (positive, negative):
                raise ValueError("hemisphere")
            n = float(raw)
            degrees = int(n // 100)
            minutes = n - degrees * 100
            if n < 0 or not 0 <= minutes < 60:
                raise ValueError("coordinate")
            return (degrees + minutes / 60) * (1 if hemisphere == positive else -1)
        lat = coord(fields[2], fields[3], "N", "S")
        lon = coord(fields[4], fields[5], "E", "W")
        elev = float(fields[9])
        if not all(math.isfinite(v) for v in (lat, lon, elev)) or not (-90 <= lat <= 90 and -180 <= lon <= 180 and -430 <= elev <= 9000):
            return None
        return {"available": True, "source": "usb", "latitude": lat,
                "longitude": lon, "elevation_m": elev}
    except (ValueError, IndexError, OverflowError):
        return None


def read_usb_gps() -> dict:
    """Bounded read of identified GPS receivers at standard NMEA baud rates.

    Generic USB/serial bridges may be mount controllers and are never opened.
    Busy receivers are left alone. No serial bytes are transmitted.
    """
    import serial
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if p.vid == 0x1546 or any(
        word in f"{p.description} {p.manufacturer} {p.product}".lower()
        for word in ("gps", "gnss", "u-blox", "ublox"))]
    deadline = time.monotonic() + 5
    for port in ports[:2]:
        for baud in (9600, 4800):
            if time.monotonic() >= deadline:
                break
            try:
                # Do not assert DTR/RTS: a serial open must not reset hardware.
                device = serial.Serial(port=None, baudrate=baud, timeout=.2)
                try:
                    device.dtr = False
                    device.rts = False
                    device.port = port.device
                    device.open()
                    end = min(deadline, time.monotonic() + 1.2)
                    while time.monotonic() < end:
                        fix = parse_gga(device.readline(256).decode("ascii", errors="ignore"))
                        if fix:
                            return fix
                finally:
                    device.close()
            except (OSError, serial.SerialException):
                continue
    return {"available": False, "detected": bool(ports),
            "detail": "GPS detected; waiting for a fix" if ports else "No GPS detected"}
