import os
import sys

# Obtener el directorio raíz del proyecto
current_dir = os.path.dirname(os.path.abspath(__file__))
esptool_dir = os.path.join(current_dir, "esptool")

# Asegurar que la carpeta `esptool` esté en sys.path
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Verificar si se puede importar esptool
try:
    import esptool
except ImportError as e:
    print(f"Error al importar esptool: {e}")
    print(f"Asegúrate de que la carpeta 'esptool' contiene un __init__.py válido.")
    sys.exit(1)


def resetcibtron(port, firmware_path, baud_rate=115200):
    """
    Programa un ESP32 utilizando esptool desde una carpeta local.
    """
    # Construir argumentos como si fueran de línea de comandos
    args = [
        "--chip", "esp32",
        "--port", port,
        "--baud", str(baud_rate),
        "write_flash", "-z",
        "0x1000", firmware_path.replace(".ino.bin", ".ino.bootloader.bin"),
        "0x8000", firmware_path.replace(".ino.bin", ".ino.partitions.bin"),
        "0x10000", firmware_path,
    ]

    # Ejecutar el comando de esptool
    try:
        esptool.main(args)
        print(f"ESP32 programado exitosamente en el puerto {port}.")
    except Exception as e:
        print(f"Error al programar el ESP32: {e}")


if __name__ == "__main__":
    # Valores predeterminados para pruebas
    port = "COM8"  # Cambia según tu configuración
    firmware_path = "leer_serial_memoria.ino.bin"  # Asegúrate de que este archivo exista
    baud_rate = 115200

    resetcibtron(port, firmware_path, baud_rate)
