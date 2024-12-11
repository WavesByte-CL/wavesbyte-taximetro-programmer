import os
import sys
from google.cloud import firestore
from google.oauth2.service_account import Credentials

def initialize_firestore_with_service_account():
    """
    Inicializa Firestore utilizando las credenciales de una cuenta de servicio desde el archivo credentials.json.
    Este método es compatible con PyInstaller.
    """
    try:
        # Asegúrate de que las credenciales estén en el mismo directorio que el script
        base_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        credentials_path = os.path.join(base_dir, "credentials.json")

        if not os.path.exists(credentials_path):
            raise FileNotFoundError(f"El archivo de credenciales no se encontró en {credentials_path}")

        # Cargar credenciales y proyecto desde el archivo credentials.json
        credentials = Credentials.from_service_account_file(credentials_path)
        project = credentials.project_id

        db = firestore.Client(credentials=credentials, project=project)
        print("Firestore inicializado correctamente con Service Account.")
        return db
    except Exception as e:
        print(f"Error inicializando Firestore con Service Account: {e}")
        return None

def get_most_recent_document_by_serial(serial_number):
    """
    Obtiene el documento más reciente (por 'timestamp') de todas las subcolecciones dinámicas
    bajo un documento principal identificado por su serial_number.
    """
    try:
        print(f"Obteniendo documento más reciente para el número serial {serial_number}...")
        db = initialize_firestore_with_service_account()
        if not db:
            raise Exception("No se pudo inicializar Firestore.")
        
        document_path = f"logs/{serial_number}"
        document_ref = db.document(document_path)
        subcollections = document_ref.collections()
        most_recent_doc = None

        for subcollection in subcollections:
            print(f"Procesando subcolección: {subcollection.id}")
            # Obtener el último documento ordenado por 'timestamp'
            docs = subcollection.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream()
            for doc in docs:
                doc_data = {
                    "subcollection_name": subcollection.id,
                    "document_id": doc.id,
                    "document_data": doc.to_dict()
                }
                # Comparar el timestamp del documento actual con el más reciente
                if not most_recent_doc or doc_data["document_data"]["timestamp"] > most_recent_doc["document_data"]["timestamp"]:
                    most_recent_doc = doc_data

        if most_recent_doc:
            return most_recent_doc["document_data"]
        else:
            return None
    except Exception as e:
        print(f"Error al obtener el documento más reciente: {e}")
        return None

# Ejemplo de uso
if __name__ == "__main__":
    serial_number = "000006"  # Este es el parámetro que irá variando
    result = get_most_recent_document_by_serial(serial_number)
    if result:
        print("Último Documento Global:")
        print(result)
    else:
        print("No se encontró ningún documento.")
