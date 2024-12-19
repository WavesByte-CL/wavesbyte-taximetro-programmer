# app.py
from flask import Flask, render_template, request, jsonify, redirect
from flask_socketio import SocketIO, send
import os
import sys
from esptool import main as esptool_main
import serial.tools.list_ports
from firebase_admin import credentials, firestore, initialize_app, auth
from google.cloud import storage
import threading
import time
import subprocess
from datetime import datetime
from dotenv import load_dotenv
from serial_reader import restart_and_get_value
from eventlet.hubs import epolls, kqueue, selects
from dns import dnssec, e164, namedict, tsigkeyring, update, version, zone
import eventlet

def get_firestore_client():
    try:
        # Asegúrate de que no estás inicializando dos veces
        if not firestore.client():
            print("Inicializando cliente Firestore...")
            return firestore.client()
        return firestore.client()
    except Exception as e:
        print(f"Error obteniendo cliente Firestore: {e}")
        raise


def get_base_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    else:
        return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()

SERVICE_ACCOUNT_PATH = os.path.join(BASE_DIR, "credentials.json")
TEMP_BIN_PATH = os.path.join(BASE_DIR, "firmware.bin")
DOTENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(DOTENV_PATH)

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY")

numero_serial = None
current_job_status = "Listo"
monitor_thread = None
listeners = {}  # Diccionario para rastrear listeners activos
is_programming = False

try:
    if not os.path.isfile(SERVICE_ACCOUNT_PATH):
        raise FileNotFoundError(
            f"Archivo de credenciales no encontrado: {SERVICE_ACCOUNT_PATH}"
        )

    print("Inicializando Firebase Admin...")
    cred = credentials.Certificate(SERVICE_ACCOUNT_PATH)
    initialize_app(cred)
    print("Firebase Admin inicializado correctamente.")
except ValueError as e:
    print("Firebase Admin ya estaba inicializado:", e)
except Exception as e:
    print(f"Error al inicializar Firebase Admin: {e}")
    sys.exit(1)

try:
    db = get_firestore_client()
    print("Cliente Firestore obtenido correctamente.")
except Exception as e:
    print(f"Error al obtener cliente Firestore: {e}")
    sys.exit(1)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", logger=True, engineio_logger=True)

def reset_state():
    global current_job_status, is_programming, monitor_thread
    current_job_status = "Listo"
    is_programming = False
    if monitor_thread:
        if hasattr(monitor_thread, 'is_alive') and monitor_thread.is_alive():
            print(f"Cerrando el hilo")
            monitor_thread = None
        else:
             print(f"El hilo no esta vivo")
             monitor_thread= None
    print(f"Se limpio el estado")

def emit_status_update(status):
    global current_job_status
    current_job_status = status
    print(f"Emitiendo estado: {current_job_status}")
    socketio.emit("job_status_update", {"status": current_job_status}, to=None)

def list_serial_ports():
    try:
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001"]
        ports = serial.tools.list_ports.comports()
        filtered_ports = [
            {"device": port.device, "description": port.description, "hwid": port.hwid}
            for port in ports
            if any(id_conocido in port.hwid for id_conocido in ids_conocidos)
        ]
        return filtered_ports
    except Exception as e:
        print(f"Error al listar puertos: {e}")
        return []

def download_binary(gcs_path):
    try:
        if not gcs_path.startswith("gs://"):
            raise ValueError("La ruta no es válida para GCS.")

        gcs_path = gcs_path[5:]
        bucket_name, *file_path_parts = gcs_path.split("/")
        file_path = "/".join(file_path_parts)

        storage_client = storage.Client.from_service_account_json(SERVICE_ACCOUNT_PATH)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(file_path)
        blob.download_to_filename(TEMP_BIN_PATH)
        print(f"Binario descargado correctamente a {TEMP_BIN_PATH}")
        return TEMP_BIN_PATH
    except Exception as e:
        print(f"Error descargando binario desde GCS: {e}")
        raise

def run_cloud_run_job_with_env(project_id, region, job_name, parameters, args=None):
    try:
        subprocess.run(
            [
                "gcloud",
                "auth",
                "activate-service-account",
                f"--key-file={SERVICE_ACCOUNT_PATH}",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        env_vars = ",".join([f"{key}={value}" for key, value in parameters.items()])
        command = [
            "gcloud",
            "run",
            "jobs",
            "execute",
            job_name,
            f"--project={project_id}",
            f"--region={region}",
            f"--update-env-vars={env_vars}",
        ]
        if args:
            command.append(f"--args={','.join(args)}")

        print("Ejecutando comando:", " ".join(command))

        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode != 0:
            print(f"Error ejecutando el comando: {result.stderr}")
            raise Exception(result.stderr)
        return result.stdout
    except Exception as e:
        print(f"Error al ejecutar el Cloud Run Job: {e}")
        raise

def verify_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"Error verificando el token: {e}")
        return None

def listen_to_job_status(user, uuid, port):
    global numero_serial
    global current_job_status
    document_path = f"logs/{numero_serial}/{user}/{uuid}"
    print(f"Escuchando logs en: {document_path}")

    # Verifica si ya existe un listener activo para este documento
    if document_path in listeners:
        print(f"Eliminando listener previo para {document_path}")
        listeners[document_path].unsubscribe()
        del listeners[document_path]

    def on_snapshot(doc_snapshot, changes, read_time):
        global current_job_status

        with app.app_context():
            for doc in doc_snapshot:
                if doc.exists:
                    log_data = doc.to_dict()
                    job_status = log_data.get("status", "unknown")
                    current_job_status = job_status
                    print(f"Estado actualizado del job: {job_status}")

                    try:
                        if job_status == "success":
                            current_job_status = "Success"
                        elif job_status == "completed":
                            print("El proceso se completó con éxito.")
                            emit_status_update("completed")
                            if port:
                                program_device_thread(port, log_data)

                    except Exception as e:
                        error_message = f"Error en la programación del taxímetro WavesByte Cibtron WB-001: {str(e)}"
                        emit_status_update(error_message)
                else:
                    print("El documento ya no existe.")

    # Crea un nuevo listener y guárdalo en el diccionario
    listener = db.document(document_path).on_snapshot(on_snapshot)
    listeners[document_path] = listener

def program_device_thread(port, log_data):
    global current_job_status
    global is_programming
    try:
        with app.app_context():
            binary_path = log_data.get("path")
            if binary_path:
                emit_status_update("Descargando binario...")
                current_job_status = "Downloading binary..."
                download_binary(binary_path)

            current_job_status = "Programming WavesByte Cibtron WB-001..."
            program_status = program_esp32(port)
            current_job_status = program_status
            current_job_status = "Programación completa."
            current_job_status = "Finalizado"
            time.sleep(3)
            reset_state()

    except Exception as e:
        with app.app_context():
            error_message = f"Error en la programación del taxímetro WavesByte Cibtron WB-001: {str(e)}"
            emit_status_update(error_message)
            emit_status_update("Error")
            reset_state()

def program_esp32(port, baud_rate="115200"):
    global current_job_status
    try:
        if not os.path.isfile(TEMP_BIN_PATH):
            raise FileNotFoundError(
                f"El archivo firmware.bin no se encuentra en la ruta especificada: {TEMP_BIN_PATH}"
            )

        command = [
            "--port",
            port,
            "--baud",
            baud_rate,
            "write_flash",
            "--flash_mode",
            "dio",
            "--flash_size",
            "4MB",
            "0x10000",
            TEMP_BIN_PATH,
        ]
        print("Ejecutando esptool con los siguientes argumentos:", command)
        sys.argv = ["esptool.py"] + command
        esptool_main()

        return "WavesByte Cibtron WB-001 programado exitosamente."
    except Exception as e:
        raise


@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html", api_key=FIREBASE_API_KEY)


@app.route("/")
def index():
    id_token = request.cookies.get("idToken")
    if not id_token:
        return redirect("/login")

    user = verify_token(id_token)
    if user:
        email = user["email"]
        return render_template("index.html", email=email, uid=user["uid"])
    else:
        return redirect("/login")

@app.route("/set_token", methods=["POST"])
def set_token():
    data = request.json
    id_token = data.get("idToken")
    if not id_token:
        return jsonify({"status": "error", "message": "Falta el token"}), 400

    try:
        decoded_token = verify_token(id_token)
        if decoded_token:
            response = jsonify({"status": "success", "message": "Token establecido"})
            response.set_cookie("idToken", id_token, httponly=True)
            return response
        else:
            return jsonify({"status": "error", "message": "Token inválido"}), 401
    except Exception as e:
        print(f"Error al establecer el token: {e}")
        return (
            jsonify({"status": "error", "message": "Error al procesar el token"}),
            500,
        )


@app.route("/logout", methods=["POST"])
def logout():
    response = jsonify({"status": "success", "message": "Sesión cerrada"})
    response.set_cookie("idToken", "", httponly=True, expires=0)  # Eliminar la cookie
    return response


@app.route("/execute_and_program", methods=["POST"])
def execute_and_program():
    global current_job_status, monitor_thread, is_programming
    if is_programming:
        return jsonify({"status": "error", "message": "Ya hay un trabajo en curso."})
    try:
        is_programming = True
        current_job_status = "Listo"
        parameters = request.form.to_dict()
        project_id = "wavesbyte-taximetro"
        region = "us-central1"
        job_name = "esp32-compiler"
        user = parameters.get("USER")
        uuid_val = parameters.get("UUID")
        args = ["/workspace/compile_and_upload.py"]

        port = parameters.get("port")
        if not port:
            return jsonify(
                {
                    "status": "error",
                    "message": "Debe seleccionar un puerto antes de ejecutar el trabajo.",
                }
            )

        emit_status_update("Compilando WavesByte Cibtron WB-001...")
        run_cloud_run_job_with_env(project_id, region, job_name, parameters, args)
        monitor_thread = socketio.start_background_task(listen_to_job_status, user, uuid_val, port)
        return jsonify(
            {
                "status": "success",
                "message": "Cloud Run Job iniciado y monitoreando Firebase.",
            }
        )
    except Exception as e:
        error_message = f"Error al ejecutar el trabajo: {str(e)}"
        print(error_message)
        reset_state()
        return jsonify({"status": "error", "message": error_message})


@app.route("/search_serial", methods=["GET"])
def search_serial():
    global numero_serial
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return (
            jsonify({"status": "error", "message": "Número serial no proporcionado."}),
            400,
        )

    try:
        from lector_firestore import get_most_recent_document_by_serial

        result = get_most_recent_document_by_serial(serial_number)
        numero_serial = serial_number
        if result:
            return jsonify({"status": "success", "data": result})
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No se encontró información para este número serial.",
                    }
                ),
                404,
            )
    except Exception as e:
        print(f"Error al buscar número serial: {e}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500


@app.route("/search_certificates", methods=["GET"])
def search_certificates():
    serial_number = request.args.get("serial_number")
    if not serial_number:
        return (
            jsonify({"status": "error", "message": "Número serial no proporcionado."}),
            400,
        )

    try:
        from lector_firestore2 import get_all_documents_by_serial

        print(f"Buscando certificados para el número serial: {serial_number}")

        result = get_all_documents_by_serial(serial_number)
        print(f"Resultados obtenidos: {result}")

        if result:
            return jsonify({"status": "success", "data": result})
        else:
            return jsonify({"status": "success", "data": []})
    except Exception as e:
        print(f"Error al buscar certificados: {e}")
        return jsonify({"status": "error", "message": f"Error interno: {str(e)}"}), 500

@app.route("/get_ports", methods=["GET"])
def get_ports():
    try:
        ports = list_serial_ports()
        ids_conocidos = ["1A86:7523", "10C4:EA60", "0403:6001"]
        current_ports = [
            {
                "device": port["device"],
                "description": port["description"],
                "hwid": port["hwid"],
            }
            for port in ports
            if any(id_conocido in port["hwid"] for id_conocido in ids_conocidos)
        ]
        return jsonify({"status": "success", "ports": current_ports})
    except Exception as e:
        print(f"Error al obtener los puertos: {e}")
        return jsonify({"status": "error", "message": str(e)})

@app.route("/get_serial_number", methods=["POST"])
def get_serial_number():
    data = request.json
    port = data.get("port")

    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400

    try:
        BAUDRATE = 115200
        TIMEOUT = 5

        serial_number = restart_and_get_value(port, BAUDRATE, TIMEOUT)

        if serial_number:
            return jsonify({"status": "success", "serial_number": serial_number})
        else:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "No se pudo obtener el número de serie.",
                    }
                ),
                404,
            )
    except Exception as e:
        print(f"Error al obtener el número de serie: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/check_port_status", methods=["POST"])
def check_port_status():
    data = request.json
    port = data.get("port")

    if not port:
        return jsonify({"status": "error", "message": "Puerto no proporcionado."}), 400

    try:
        ports = list_serial_ports()
        port_devices = [p["device"] for p in ports]

        if port in port_devices:
            return jsonify({"status": "success", "connected": True})
        else:
            return jsonify({"status": "success", "connected": False})
    except Exception as e:
        print(f"Error al verificar el puerto: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/get_job_status", methods=["GET"])
def get_job_status():
    global current_job_status
    return jsonify({"status": current_job_status})

@app.route("/get_user_data", methods=["GET"])
def get_user_data():
    id_token = request.cookies.get("idToken")
    user = verify_token(id_token)
    if user:
        return jsonify({"email": user["email"]}), 200
    return jsonify({"error": "Usuario no autenticado"}), 401

if __name__ == "__main__":
    socketio.run(
        app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True
    )