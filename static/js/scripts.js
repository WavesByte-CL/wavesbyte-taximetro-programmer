const socket = io();

let isProgramFinished = false;
let isReloading = false;
let lastStatus = null; // Para rastrear el ultimo estado.

async function initializeForm() {
  try {
    const response = await fetch("/get_user_data");
    if (response.ok) {
      const data = await response.json();
      document.getElementById("USER").value = data.email.split("@")[0];
      document.getElementById("UUID").value = generateUUID();
      document.getElementById("MARCA_TAXIMETRO").value = "CIBTRON";
      document.getElementById("MODELO_TAXIMETRO").value = "WB-001";
      executeButton.disabled = true;
    } else {
      throw new Error("No se pudo obtener el usuario");
    }
  } catch (error) {
    console.error("Error inicializando formulario:", error);
  }
}

async function fetchUser() {
  try {
    const response = await fetch("/get_user_data");
    const user = await response.json();
    document.getElementById("USER").value = user.email.split("@")[0];
  } catch (error) {
    console.error("Error fetching user data:", error);
  }
}
fetchUser();

function checkFormValidity() {
  const port = document.getElementById("port").value;
  if (jobForm.checkValidity() && port !== "") {
    executeButton.disabled = false;
  } else {
    executeButton.disabled = true;
  }
}

function updatePortStatus(connected) {
  const statusElement = document.getElementById("port-status");
  if (connected) {
    statusElement.textContent = "Conectado";
    statusElement.style.color = "green";
  } else {
    statusElement.textContent = "Desconectado";
    statusElement.style.color = "red";
  }
}

function generateUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

socket.on("connect", () => {
  console.log("Conexión establecida con el servidor.");
  addLogMessage("Conexión establecida con el servidor.");
});

socket.on("disconnect", () => {
  console.log("Conexión perdida con el servidor.");
});

socket.on("connect_error", (error) => {
  console.error("Error al conectar con el servidor:", error);
});

function clearForm() {
  const form = document.getElementById("jobForm");
  const userField = document.getElementById("USER");
  const userValue = userField.value;
  const uuidField = document.getElementById("UUID");
  const uuidValue = uuidField.value;
  const marcaField = document.getElementById("MARCA_TAXIMETRO");
  const marcaValue = marcaField.value;
  const modeloField = document.getElementById("MODELO_TAXIMETRO");
  const modeloValue = modeloField.value;
  form.reset();
  userField.value = userValue;
  uuidField.value = uuidValue;
  marcaField.value = marcaValue;
  modeloField.value = modeloValue;
  document.getElementById("port").innerHTML =
    '<option value="" disabled selected>Esperando detección...</option>';
}

socket.on("update_ports", (ports) => {
  console.log("Evento 'update_ports' recibido:", ports);

  const portSelect = document.getElementById("port");
  if (portSelect) {
    portSelect.innerHTML = "";

    if (ports.length > 0) {
      ports.forEach((port) => {
        const option = document.createElement("option");
        option.value = port.device;
        option.textContent = `${port.description} (${port.device})`;
        portSelect.appendChild(option);
      });
    } else {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No se detectó el taxímetro";
      portSelect.appendChild(option);
    }
  }
});

function restartProgram() {
  clearForm();
  clearLogs();
  const executeButton = document.getElementById("executeButton");
  executeButton.disabled = true;
  executeButton.textContent = "Programar WavesByte Cibtron WB-001";
  const cancelButton = document.getElementById("cancelButton");
  cancelButton.style.display = "none";
  document.getElementById("port-status").textContent = "Desconocido";
  document.getElementById("port-status").style.color = "gray";
  selectedPort = null;
  updateJobStatus("Listo");
  isProgramFinished = false;
  initializeForm();
  isReloading = false; // Resetear el flag de recarga
}

async function executeAndProgram(event) {
  event.preventDefault();
  const formData = new FormData(document.getElementById("jobForm"));
  const selectedPort = document.getElementById("port").value;

  if (!selectedPort) {
    alert("Debe conectar el taxímetro antes de ejecutar el trabajo.");
    return;
  }

  formData.append("port", selectedPort);
  addLogMessage(
    "Comenzó la ejecución del trabajo y la programación, espere unos minutos..."
  );

  const executeButton = document.getElementById("executeButton");
  executeButton.disabled = true;
  executeButton.textContent = "Cargando...";
  isProgramFinished = false; // Reiniciar el flag al iniciar un nuevo proceso
  isReloading = false;

  try {
    const response = await fetch("/execute_and_program", {
      method: "POST",
      body: formData,
    });
    const result = await response.json();

    if (result.status === "success") {
      addLogMessage(
        "Comenzó la ejecución del trabajo y la programación, espere unos minutos..."
      );
    } else {
      addLogMessage(`Error: ${result.message}`);
      alert(`Error: ${result.message}`);
      executeButton.textContent = "Programar WavesByte Cibtron WB-001";
      executeButton.disabled = false;
    }
  } catch (error) {
    addLogMessage(`Error al ejecutar el trabajo: ${error.message}`);
    alert(`Error al ejecutar el trabajo: ${error.message}`);
    executeButton.textContent = "Programar WavesByte Cibtron WB-001";
    executeButton.disabled = false;
  }
}

function logout() {
  if (!confirm("¿Estás seguro de que deseas cerrar sesión?")) {
    return;
  }
  fetch("/logout", {
    method: "POST",
    credentials: "include",
  })
    .then((response) => response.json())
    .then((data) => {
      if (data.status === "success") {
        alert("Sesión cerrada correctamente.");
        window.location.href = "/login";
      } else {
        alert("Error al cerrar sesión.");
      }
    })
    .catch((error) => {
      console.error("Error al cerrar sesión:", error);
      alert("Error al cerrar sesión.");
    });
}

const jobForm = document.getElementById("jobForm");
const executeButton = document.getElementById("executeButton");

jobForm.addEventListener("input", checkFormValidity);
jobForm.addEventListener("change", checkFormValidity);

async function checkStatus() {
  if (isProgramFinished) return;

  try {
    const response = await fetch("/get_job_status");
    const data = await response.json();
    const currentStatus = data.status;

    if (currentStatus) {
      console.log("Estado actualizado desde el servidor:", currentStatus);

      if (currentStatus !== lastStatus) {
        // Verificar si el estado ha cambiado
        addLogMessage(`<strong>Status:</strong> ${currentStatus}`);
        lastStatus = currentStatus; // Actualizar el último estado
      }

      if (currentStatus === "Finalizado") {
        isProgramFinished = true;
        if (!isReloading) {
          alert(
            "Programación completada para el numero serial " +
              document.getElementById("NUMERO_SERIAL").value +
              ". Desconecte el taxímetro."
          );
          isReloading = true;
          location.reload();
        }
      }
    } else {
      addLogMessage("No se pudo obtener el estado actual.");
    }
  } catch (error) {
    console.error("Error al obtener el estado actual:", error);
    addLogMessage("Error al obtener el estado actual.");
  }
}

setInterval(checkStatus, 1000);

document
  .getElementById("searchSerialBtn")
  .addEventListener("click", async () => {
    const serialNumber = document.getElementById("NUMERO_SERIAL").value;

    if (!serialNumber) {
      alert("Por favor, ingresa un número serial.");
      return;
    }

    try {
      const response = await fetch(
        `/search_serial?serial_number=${serialNumber}`
      );
      const result = await response.json();

      if (result.status === "success") {
        initializeForm();

        const data = result.data.env_vars;
        document.getElementById("MARCA_TAXIMETRO").value =
          data.MARCA_TAXIMETRO || "";
        document.getElementById("MODELO_TAXIMETRO").value =
          data.MODELO_TAXIMETRO || "";
        document.getElementById("NUMERO_SELLO").value = data.NUMERO_SELLO || "";
        document.getElementById("NOMBRE_PROPIETARIO").value =
          data.NOMBRE_PROPIETARIO || "";
        document.getElementById("APELLIDO_PROPIETARIO").value =
          data.APELLIDO_PROPIETARIO || "";
        document.getElementById("MARCA_VEHICULO").value =
          data.MARCA_VEHICULO || "";
        document.getElementById("YEAR_VEHICULO").value =
          data.YEAR_VEHICULO || "";
        document.getElementById("PATENTE").value = data.PATENTE || "";
        document.getElementById("RESOLUCION").value = data.RESOLUCION || "";
        document.getElementById("CANTIDAD_PULSOS").value =
          data.CANTIDAD_PULSOS || "";
        document.getElementById("TARIFA_INICIAL").value =
          data.TARIFA_INICIAL || "";
        document.getElementById("TARIFA_CAIDA_PARCIAL_METROS").value =
          data.TARIFA_CAIDA_PARCIAL_METROS || "";
        document.getElementById("TARIFA_CAIDA_PARCIAL_MINUTO").value =
          data.TARIFA_CAIDA_PARCIAL_MINUTO || "";
        document.getElementById("MOSTRAR_VELOCIDAD_EN_PANTALLA").value =
          data.MOSTRAR_VELOCIDAD_EN_PANTALLA || "";
        document.getElementById("COLOR_FONDO_PANTALLA").value =
          data.COLOR_FONDO_PANTALLA || "";
        document.getElementById("COLOR_LETRAS_PANTALLA").value =
          data.COLOR_LETRAS_PANTALLA || "";
        document.getElementById("COLOR_PRECIO_PANTALLA").value =
          data.COLOR_PRECIO_PANTALLA || "";
        document.getElementById("PROPAGANDA_1").value = data.PROPAGANDA_1 || "";
        document.getElementById("PROPAGANDA_2").value = data.PROPAGANDA_2 || "";
        document.getElementById("PROPAGANDA_3").value = data.PROPAGANDA_3 || "";
        document.getElementById("PROPAGANDA_4").value = data.PROPAGANDA_4 || "";

        alert("Datos completados exitosamente.");
      } else {
        alert(result.message || "No se encontró información.");
      }
    } catch (error) {
      console.error("Error al buscar el número serial:", error);
      alert("Ocurrió un error al buscar el número serial.");
    }
  });

document
  .getElementById("searchCertificateBtn")
  .addEventListener("click", async () => {
    const serialNumber = document.getElementById("NUMERO_SERIAL").value;

    if (!serialNumber) {
      alert("Por favor, asegúrate de que el número serial esté presente.");
      return;
    }

    try {
      const response = await fetch(
        `/search_certificates?serial_number=${serialNumber}`
      );
      const result = await response.json();
      const certificatesList = document.getElementById("certificatesList");
      certificatesList.innerHTML = "";

      if (
        result.status === "success" &&
        Array.isArray(result.data) &&
        result.data.length > 0
      ) {
        result.data.forEach((cert) => {
          const data = cert.document_data;
          const card = document.createElement("div");
          card.className = "card";

          card.innerHTML = `
                    <div class="card-body">
                        <h5 class="card-title">ID: ${cert.document_id}</h5>
                        <p class="card-text">
                            <strong>Fecha:</strong> ${new Date(
                              data.date
                            ).toLocaleString()}<br>
                            <strong>Nombre:</strong> ${
                              data.env_vars?.NOMBRE_PROPIETARIO || "N/A"
                            }<br>
                            <strong>Apellido:</strong> ${
                              data.env_vars?.APELLIDO_PROPIETARIO || "N/A"
                            }<br>
                            <strong>Patente:</strong> ${
                              data.env_vars?.PATENTE || "N/A"
                            }
                        </p>
                        <button class="btn btn-primary btn-sm" onclick='showDetails(${JSON.stringify(
                          data
                        )})'>
                            <i class="fas fa-eye"></i> Ver detalles
                        </button>
                    </div>
                `;
          certificatesList.appendChild(card);
        });
      } else if (result.status === "success" && result.data.length === 0) {
        certificatesList.innerHTML =
          "<p>No se encontraron programaciones previas para este número serial.</p>";
      } else {
        certificatesList.innerHTML = `<p>Error en la respuesta: ${JSON.stringify(
          result
        )}</p>`;
      }
    } catch (error) {
      console.error("Error al buscar programaciones previas:", error);
      certificatesList.innerHTML = `<p>Ocurrió un error: ${error.message}</p>`;
    }
  });

function showDetails(data) {
  const modalContent = document.getElementById("modalContent");
  modalContent.innerHTML = "";

  if (data.env_vars && typeof data.env_vars === "object") {
    const fieldMapping = {
      UUID: "UUID",
      USER: "PROGRAMADOR",
      DATE: "FECHA DE PROGRAMACIÓN",
      NUMERO_SERIAL: "NÚMERO DE SERIE",
      NUMERO_SELLO: "NÚMERO DE SELLO",
      MARCA_TAXIMETRO: "MARCA DEL TAXÍMETRO",
      MODELO_TAXIMETRO: "MODELO DEL TAXÍMETRO",
      NOMBRE_PROPIETARIO: "NOMBRE DEL PROPIETARIO",
      APELLIDO_PROPIETARIO: "APELLIDO DEL PROPIETARIO",
      MARCA_VEHICULO: "MARCA DEL VEHÍCULO",
      YEAR_VEHICULO: "AÑO DEL VEHÍCULO",
      PATENTE: "PATENTE",
      RESOLUCION: "RESOLUCIÓN",
      CANTIDAD_PULSOS: "DIVISOR",
      TARIFA_INICIAL: "TARIFA INICIAL",
      TARIFA_CAIDA_PARCIAL_METROS: "TARIFA CAÍDA PARCIAL METROS",
      TARIFA_CAIDA_PARCIAL_MINUTO: "TARIFA CAÍDA PARCIAL MINUTOS",
      MOSTRAR_VELOCIDAD_EN_PANTALLA: "MOSTRAR METROS EN PANTALLA",
      COLOR_FONDO_PANTALLA: "COLOR DE FONDO EN PANTALLA",
      COLOR_LETRAS_PANTALLA: "COLOR DE LETRAS EN PANTALLA",
      COLOR_PRECIO_PANTALLA: "COLOR DE PRECIO EN PANTALLA",
      PROPAGANDA_1: "PROPAGANDA Nº1",
      PROPAGANDA_2: "PROPAGANDA Nº2",
      PROPAGANDA_3: "PROPAGANDA Nº3",
      PROPAGANDA_4: "PROPAGANDA Nº4",
    };
    for (const [key, title] of Object.entries(fieldMapping)) {
      const value = data.env_vars[key] || "N/A";
      modalContent.innerHTML += `<p><strong>${title}:</strong> ${value}</p>`;
    }
  } else {
    modalContent.innerHTML = `<p>No hay variables de entorno disponibles para mostrar.</p>`;
  }
  const detailsModal = new bootstrap.Modal(
    document.getElementById("detailsModal")
  );
  detailsModal.show();
}

document
  .getElementById("updatePortsBtn")
  .addEventListener("click", async () => {
    initializeForm();
    const portSelect = document.getElementById("port");
    const updatePortsBtn = document.getElementById("updatePortsBtn");
    const serialInput = document.getElementById("NUMERO_SERIAL");

    updatePortsBtn.disabled = true;
    updatePortsBtn.textContent = "Detectando...";

    try {
      const response = await fetch("/get_ports");
      const data = await response.json();

      if (data.status === "success") {
        portSelect.innerHTML =
          '<option value="" disabled selected>Selecciona un puerto...</option>';
        data.ports.forEach((port) => {
          const option = document.createElement("option");
          option.value = port.device;
          option.textContent = `${port.device} (${port.description})`;
          portSelect.appendChild(option);
        });

        updatePortsBtn.disabled = false;
        updatePortsBtn.textContent = "Actualizar";

        portSelect.addEventListener("change", async () => {
          const selectedPort = portSelect.value;
          if (selectedPort) {
            try {
              const serialResponse = await fetch("/get_serial_number", {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({
                  port: selectedPort,
                }),
              });
              const serialData = await serialResponse.json();

              if (serialData.status === "success") {
                serialInput.value = serialData.serial_number;
              } else {
                alert(
                  `Error al detectar el número serial: ${serialData.message}`
                );
              }
            } catch (error) {
              console.error("Error al obtener el número de serie:", error);
              alert("Hubo un problema al detectar el número serial.");
            }
          }
        });
      } else {
        console.error("Error al detectar puertos:", data.message);
        alert("Error al detectar puertos: " + data.message);
      }
    } catch (error) {
      console.error("Error al realizar la solicitud:", error);
      alert("Hubo un problema al detectar los puertos. Inténtalo de nuevo.");
    } finally {
      updatePortsBtn.disabled = false;
      updatePortsBtn.textContent = "Actualizar";
    }
  });

let selectedPort = null;

document.getElementById("port").addEventListener("change", (event) => {
  selectedPort = event.target.value;
});

setInterval(async () => {
  if (!selectedPort) return;

  try {
    const response = await fetch("/check_port_status", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        port: selectedPort,
      }),
    });
    const data = await response.json();

    if (data.status === "success") {
      const statusLabel = document.getElementById("port-status");
      if (data.connected) {
        statusLabel.textContent = "Conectado";
        statusLabel.style.color = "green";
      } else {
        statusLabel.textContent = "Desconectado";
        statusLabel.style.color = "red";
        location.reload();
      }
    } else {
      console.error("Error al verificar estado del puerto:", data.message);
    }
  } catch (error) {
    console.error("Error al realizar la solicitud:", error);
  }
}, 5000);

let lastLogMessage = null;

function addLogMessage(message, isStatus = false) {
  const logContainer = document.getElementById("jobLogs");
  const logEntry = document.createElement("p");
  const serialNumber = document.getElementById("NUMERO_SERIAL").value;

  const now = new Date();
  const timestamp = now.toLocaleString();

  if (isStatus) {
    logEntry.innerHTML = `<b>${serialNumber}:</b> ${message} - ${timestamp} `;
  } else {
    logEntry.innerHTML = `${timestamp} - ${
      document.getElementById("NUMERO_SERIAL").value
    } - ${message} `;
  }
  logContainer.appendChild(logEntry);
  logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLogs() {
  const logContainer = document.getElementById("jobLogs");
  logContainer.innerHTML = "";
}

document
  .getElementById("resetButton")
  .addEventListener("click", async () => {
    if (!selectedPort) {
      alert(
        "Debe seleccionar/conectar un puerto primero para poder resetear el dispositivo."
      );
      return;
    }

    if (!confirm("¿Estás seguro de que deseas resetear el taxímetro?")) {
      return;
    }

    // Puedes permitir cambiar el firmware, o usar el por defecto
    const firmwarePath = "leer_serial_memoria.ino.bin";

    try {
      const formData = new FormData();
      formData.append("port", selectedPort);
      formData.append("firmware_path", firmwarePath);

      const response = await fetch("/resetcibtron", {
        method: "POST",
        body: formData,
      });
      const result = await response.json();

      if (result.status === "success") {
        addLogMessage(
          "Dispositivo reseteado con el firmware por defecto correctamente."
        );
        alert("El dispositivo ha sido reseteado correctamente.");
        location.reload();
        initializeForm();
        updatePortsBtn.disabled = false;
        updatePortsBtn.textContent = "Actualizar";
        selectedPort = null;
      } else {
        addLogMessage(`Error al resetear: ${result.message}`);
        alert(`Error al resetear: ${result.message}`);
      }
    } catch (error) {
      console.error("Error al resetear el dispositivo:", error);
      addLogMessage(`Error al resetear el dispositivo: ${error.message}`);
      alert(`Error al resetear el dispositivo: ${error.message}`);
    }
  });

function generateAndPrintPDF() {
  if (validateForm()) {
    //console.log("Formulario validado, generando PDF...");
    // Recopilar datos del formulario
    const formData = {
      CANTIDAD_PULSOS: document.getElementById("CANTIDAD_PULSOS").value,
      RESOLUCION: document.getElementById("RESOLUCION").value,
      TARIFA_INICIAL: document.getElementById("TARIFA_INICIAL").value,
      TARIFA_CAIDA_PARCIAL_METROS: document.getElementById(
        "TARIFA_CAIDA_PARCIAL_METROS"
      ).value,
      MARCA_VEHICULO: document.getElementById("MARCA_VEHICULO").value,
      PATENTE: document.getElementById("PATENTE").value,
      NUMERO_SELLO: document.getElementById("NUMERO_SELLO").value,
      NUMERO_SERIAL: document.getElementById("NUMERO_SERIAL").value,
      NOMBRE_PROPIETARIO: document.getElementById("NOMBRE_PROPIETARIO").value,
      APELLIDO_PROPIETARIO: document.getElementById("APELLIDO_PROPIETARIO")
        .value,
    };
    //console.log(formData);
    // Enviar datos al servidor para generar el PDF
    fetch("/generate_pdf", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(formData),
    })
      .then((response) => {
        if (response.ok) {
          return response.blob();
        } else {
          throw new Error("Error al generar el PDF.");
        }
      })
      .then((blob) => {
        // Crear un objeto URL para el blob
        const url = URL.createObjectURL(blob);

        // Abrir una nueva ventana para imprimir el PDF
        window.open(url, "_blank");

        // Limpiar el objeto URL después de abrir la ventana
        URL.revokeObjectURL(url);
      })
      .catch((error) => {
        console.error("Error:", error);
        alert("Error al generar el PDF: " + error.message);
      });
  } else {
    alert("Por favor, complete todos los campos requeridos del formulario.");
  }
}



function validateForm() {
  const form = document.getElementById("jobForm");
  if (form.checkValidity() === false) {
    form.classList.add("was-validated");
    return false;
  }
  form.classList.remove("was-validated");
  return true;
}