import serial
import time

# Configuración del puerto serie
PORT = "/dev/cu.usbserial-110"  # Cambia por el puerto de tu ESP32
BAUDRATE = 115200
TIMEOUT = 2  # Tiempo de espera en segundos para leer datos

def restart_and_get_value(port, baudrate, timeout, keyword="NUMERO_SERIAL"):
    """
    Reinicia el ESP32 y extrae el valor asociado a una palabra clave del monitor serial.

    Args:
        port (str): Puerto serie al que está conectado el ESP32.
        baudrate (int): Velocidad de baudios del puerto serie.
        timeout (int): Tiempo máximo de espera en segundos.
        keyword (str): Palabra clave a buscar en la salida del Serial Monitor.

    Returns:
        str: Valor extraído del monitor serial asociado a la palabra clave.
    """
    try:
        # Configurar el puerto serie
        ser = serial.Serial(port, baudrate, timeout=timeout)
        print(f"Conectado al puerto {port} a {baudrate} baudios.")

        # Reiniciar el ESP32
        ser.dtr = False
        ser.rts = False
        time.sleep(0.1)
        ser.dtr = True
        ser.rts = True
        print("ESP32 reiniciado.")

        # Esperar 1 segundo para que el ESP32 inicie
        time.sleep(1)

        # Leer los datos impresos en el monitor serial
        print("Leyendo datos del monitor serial:")
        start_time = time.time()
        while time.time() - start_time < timeout:
            line = ser.readline().decode("utf-8").strip()
            if line and keyword in line:
                # Extraer el valor asociado a la palabra clave
                value = line.split(":")[1].strip()
                return value

        print("No se encontró el valor solicitado en el tiempo de espera.")
        return None

    except Exception as e:
        print(f"Error: {e}")
        return None

    finally:
        ser.close()
        print("Conexión serial cerrada.")

if __name__ == "__main__":
    # Llamar a la función para obtener el valor
    valor = restart_and_get_value(PORT, BAUDRATE, TIMEOUT)
    if valor:
        print(f"Valor encontrado: {valor}")
    else:
        print("No se obtuvo ningún valor.")
