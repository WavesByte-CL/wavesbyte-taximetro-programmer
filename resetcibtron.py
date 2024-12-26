import os
import subprocess

def resetcibtron(port, firmware_path, baud_rate=115200):
    """
    Programa un ESP32 utilizando esptool sin sobrescribir la partición NVS.
    
    :param port: Puerto COM donde está conectado el ESP32 (ej: "COM3" o "/dev/ttyUSB0").
    :param firmware_path: Ruta al archivo .ino.bin del firmware.
    :param baud_rate: Velocidad de transmisión (por defecto, 115200).
    :return: None
    """

    # Archivos asociados generados por Arduino IDE
    bootloader_path = firmware_path.replace(".ino.bin", ".ino.bootloader.bin")
    partitions_path = firmware_path.replace(".ino.bin", ".ino.partitions.bin")

    print(firmware_path)
    print(bootloader_path)
    print(partitions_path)

    if not (os.path.exists(firmware_path) and os.path.exists(bootloader_path) and os.path.exists(partitions_path)):
        print(f"Error: No se encontraron todos los archivos necesarios.\n"
              f"Verifica que existan: {firmware_path}, {bootloader_path}, {partitions_path}")
        return

    try:
        # Comando completo para programar sin sobrescribir NVS
        command = [
            "esptool",
            "--chip", "esp32",
            "--port", port,
            "--baud", str(baud_rate),
            "write_flash", "-z",
            "0x1000", bootloader_path,      # Offset para el bootloader
            "0x8000", partitions_path,     # Offset para las particiones
            "0x10000", firmware_path       # Offset para el firmware principal
        ]

        # Ejecuta el comando en el shell
        subprocess.run(command, check=True)
        print(f"ESP32 programado exitosamente en el puerto {port}.")
    except subprocess.CalledProcessError as e:
        print(f"Error al programar el ESP32: {e}")
    except Exception as e:
        print(f"Error inesperado: {e}")

if __name__ == "__main__":
    print("Probador de Programación ESP32 con configuración completa")
    port = "COM8"
    firmware_path = "leer_serial_memoria.ino.bin"

    try:
        resetcibtron(port, firmware_path)
    except Exception as e:
        print(f"Error en la ejecución: {e}")
