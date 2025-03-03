# -*- mode: python ; coding: utf-8 -*-
import certifi
import os
from pathlib import Path


def get_all_files_in_dir(dir):
    """Helper function to recursively get all file paths in a directory."""
    file_paths = []
    for root, _, files in os.walk(dir):
        for file in files:
            file_paths.append(str(Path(root) / file))
    return file_paths

# Obtener todos los archivos de esptool
esptool_dir = Path("esptool")
esptool_files = get_all_files_in_dir(esptool_dir)
# Ajustar las rutas relativas para incluirlas correctamente
esptool_datas = [(str(file), f"esptool/{Path(file).relative_to(esptool_dir).parent}") for file in esptool_files]

a = Analysis(
    ['app.py'],  # Script principal
    pathex=[],  # Rutas adicionales (vacío si no hay más)
    binaries=[],  # Binarios adicionales (vacío si no hay más)
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('cibtron.txt', '.'),  # Archivo adicional
        ('.env', '.'),  # Variables de entorno
        *esptool_datas,  # Incluir todos los archivos de esptool
        ('lector_firestore.py', '.'),  # Otros scripts adicionales
        ('lector_firestore2.py', '.'),
        ('serial_reader.py', '.'),
        ('resetcibtron.py', '.'),
        ('leer_serial_memoria.ino.bin', '.'),
        ('leer_serial_memoria.ino.bootloader.bin', '.'),
        ('leer_serial_memoria.ino.partitions.bin', '.'),
        ('logo.ico', '.'),  # Icono para el ejecutable
        (certifi.where(), 'certifi')  # Certificados SSL
    ],
    hiddenimports=[
        'socketio',
        'engineio',
        'engineio.async_drivers.eventlet',
        'flask_socketio',
        'dns.versioned',
        'dns.hash',
        'dns.dnssec',
        'dns.tsigkeyring',
        'dns.namedict',
        'dns.update',
        'dns.zone',
        'dns.asyncbackend',
        'dns.e164',
        'eventlet',
        'gevent',
        'grpc'
    ],
    hookspath=[],  # Rutas a hooks personalizados, si los tienes
    hooksconfig={},  # Configuración adicional para hooks
    runtime_hooks=[],  # Hooks en tiempo de ejecución
    excludes=[],  # Módulos a excluir
    noarchive=False,  # Desactivar archivo zip (False por defecto)
    optimize=0,  # Optimización: 0 (sin), 1 (básica), 2 (máxima)
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Programador WavesByte Cibtron WB-001',  # Nombre del ejecutable
    debug=False,  # Modo debug desactivado
    bootloader_ignore_signals=False,
    strip=False,  # No elimina símbolos (útil para debug)
    upx=True,  # Comprime binarios con UPX
    upx_exclude=[],  # Excluir binarios de UPX
    runtime_tmpdir=None,  # Directorio temporal para tiempo de ejecución
    console=True,  # Muestra la consola (True para CLI, False para GUI)
    onefile=True,  # Generar un solo archivo ejecutable
    icon='./logo.ico',  # Icono para el ejecutable
    disable_windowed_traceback=False,  # Permite rastreo en ventanas
    argv_emulation=False,
    target_arch=None,  # Arquitectura específica (None para la actual)
    codesign_identity=None,  # Solo en macOS
    entitlements_file=None,  # Solo en macOS
)
