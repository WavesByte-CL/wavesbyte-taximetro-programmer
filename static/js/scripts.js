// Intenta obtener el socket globalmente si aún no está definido.
const socket = typeof io !== "undefined" ? io() : null;

let isProgramFinished = false; // Para el job de parámetros
let isSetSerialProgramFinished = false; // Para el job de seteo de serial
let isReloading = false;
let lastStatus = null; // Para rastrear el último estado del job de parámetros
let lastSetSerialStatus = null; // Para rastrear el último estado del job de seteo de serial
let selectedPort = null; // Puerto seleccionado en el formulario principal

// --- Inicialización y Funciones de Utilidad ---
async function initializeForm() {
  try {
    const response = await fetch("/get_user_data");
    if (response.ok) {
      const data = await response.json();
      const userEmail = data.email;
      document.getElementById("USER").value = userEmail.split("@")[0];
      document.getElementById("UUID").value = generateUUID(); // Para el job de programar parámetros
      document.getElementById("MARCA_TAXIMETRO").value = "CIBTRON";
      document.getElementById("MODELO_TAXIMETRO").value = "WB-001";
      document.getElementById("executeButton").disabled = true;
      if (document.getElementById("userEmailDisplay")) {
        document.getElementById("userEmailDisplay").textContent = userEmail;
      }
    } else {
      console.error("No se pudo obtener el usuario, redirigiendo a login.");
      // window.location.href = "/login";
    }
  } catch (error) {
    console.error("Error inicializando formulario:", error);
  }
}

function generateUUID() {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
    const r = (Math.random() * 16) | 0,
      v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function addLogMessage(message, jobType = "params") {
  const logContainer = document.getElementById("jobLogs");
  if (!logContainer) {
    console.warn("Contenedor de logs 'jobLogs' no encontrado.");
    return;
  }

  const placeholder = logContainer.querySelector("p.text-muted");
  if (placeholder) {
    placeholder.remove();
  }

  const logEntry = document.createElement("p");
  const serialNumberInput = document.getElementById("NUMERO_SERIAL");
  const serialNumberForLog = serialNumberInput
    ? serialNumberInput.value
    : "N/A";

  const now = new Date();
  const timestamp = now.toLocaleString();

  let prefix = "";
  if (jobType === "set_serial") {
    prefix = "[Prog. Serial]";
  } else if (jobType === "params") {
    prefix = "[Prog. Params]";
  } else if (jobType === "system") {
    prefix = "[System]";
  }

  logEntry.innerHTML = `${timestamp} - ${prefix} ${serialNumberForLog}: ${message}`;
  logContainer.appendChild(logEntry);
  logContainer.scrollTop = logContainer.scrollHeight;
}

function clearLogs() {
  const logContainer = document.getElementById("jobLogs");
  if (logContainer) {
    logContainer.innerHTML =
      '<p class="text-muted text-center">Esperando logs...</p>';
  }
}

function clearMainForm() {
  const form = document.getElementById("jobForm");
  if (!form) return;

  const userField = document.getElementById("USER");
  const uuidField = document.getElementById("UUID");
  const marcaField = document.getElementById("MARCA_TAXIMETRO");
  const modeloField = document.getElementById("MODELO_TAXIMETRO");

  const userValue = userField ? userField.value : "";
  const marcaValue = marcaField ? marcaField.value : "CIBTRON";
  const modeloValue = modeloField ? modeloField.value : "WB-001";

  form.reset();

  if (userField) userField.value = userValue;
  if (uuidField) uuidField.value = generateUUID();
  if (marcaField) marcaField.value = marcaValue;
  if (modeloField) modeloField.value = modeloValue;

  const portSelect = document.getElementById("port");
  if (portSelect) {
    portSelect.innerHTML =
      '<option value="" disabled selected>Esperando detección...</option>';
  }
  document.getElementById("NUMERO_SERIAL").value = "";
  updatePortStatus(false);
  checkFormValidity();
}

function checkFormValidity() {
  const form = document.getElementById("jobForm");
  const executeBtn = document.getElementById("executeButton");
  const portVal = document.getElementById("port")?.value;
  const serialVal = document.getElementById("NUMERO_SERIAL")?.value;

  if (form && executeBtn) {
    if (
      form.checkValidity() &&
      portVal &&
      portVal !== "" &&
      serialVal &&
      serialVal !== "" &&
      serialVal !== "Detectando serial..." &&
      serialVal !== "Error al detectar"
    ) {
      executeBtn.disabled = false;
    } else {
      executeBtn.disabled = true;
    }
  }
}

function updatePortStatus(connected, portName = "") {
  const statusElement = document.getElementById("port-status");
  if (statusElement) {
    if (connected) {
      statusElement.textContent = `Conectado (${portName})`;
      statusElement.style.color = "green";
    } else {
      statusElement.textContent = "Desconectado";
      statusElement.style.color = "red";
      const executeBtn = document.getElementById("executeButton");
      if (executeBtn) executeBtn.disabled = true;
    }
  }
}

function updateGlobalJobStatusUI(status, jobType = "params") {
  console.log(`Estado global del Job [${jobType}]: ${status}`);
}

async function fetchAndPopulatePorts(selectElementId, autoSelectIfOne = false) {
  const portSelect = document.getElementById(selectElementId);
  if (!portSelect) return;

  const previouslySelectedPort = portSelect.value;
  portSelect.innerHTML =
    '<option value="" disabled selected>Detectando puertos...</option>';

  try {
    const response = await fetch("/get_ports");
    const data = await response.json();
    portSelect.innerHTML = "";

    if (data.status === "success" && data.ports.length > 0) {
      let foundPreviouslySelected = false;
      data.ports.forEach((port) => {
        const option = new Option(
          `${port.device} (${port.description || "N/A"})`,
          port.device
        );
        if (port.device === previouslySelectedPort) {
          option.selected = true;
          foundPreviouslySelected = true;
        }
        portSelect.appendChild(option);
      });

      if (
        !foundPreviouslySelected ||
        !autoSelectIfOne ||
        data.ports.length > 1
      ) {
        portSelect.insertBefore(
          new Option(
            "Selecciona un puerto...",
            "",
            !foundPreviouslySelected,
            !foundPreviouslySelected
          ),
          portSelect.firstChild
        );
        if (!foundPreviouslySelected) portSelect.value = "";
      }

      if (
        autoSelectIfOne &&
        data.ports.length === 1 &&
        !foundPreviouslySelected
      ) {
        portSelect.value = data.ports[0].device;
      }

      if (portSelect.value) {
        const event = new Event("change", { bubbles: true });
        portSelect.dispatchEvent(event);
      } else {
        document.getElementById("NUMERO_SERIAL").value = "";
        updatePortStatus(false);
      }
    } else {
      portSelect.appendChild(
        new Option("No se detectaron taxímetros", "", true, true)
      );
      portSelect.value = "";
      updatePortStatus(false);
      document.getElementById("NUMERO_SERIAL").value = "";
    }
  } catch (error) {
    console.error("Error al obtener puertos:", error);
    portSelect.innerHTML =
      '<option value="" disabled selected>Error al cargar puertos</option>';
    updatePortStatus(false);
    document.getElementById("NUMERO_SERIAL").value = "";
  }
  checkFormValidity();
}

async function fetchAndSetSerialNumber(portDevice) {
  const serialInput = document.getElementById("NUMERO_SERIAL");
  if (!portDevice || !serialInput) {
    if (serialInput) serialInput.value = "";
    checkFormValidity();
    return;
  }

  serialInput.value = "Detectando serial...";
  try {
    const response = await fetch("/get_serial_number", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: portDevice }),
    });
    const data = await response.json();
    if (data.status === "success") {
      serialInput.value = data.serial_number.toUpperCase();
    } else {
      serialInput.value = "Error al detectar";
      console.warn(
        `No se pudo obtener el número serial del puerto ${portDevice}: ${data.message}`
      );
    }
  } catch (error) {
    serialInput.value = "Error de conexión";
    console.error("Error al obtener número de serie:", error);
  }
  checkFormValidity();
}

document.addEventListener("DOMContentLoaded", () => {
  initializeForm();
  fetchAndPopulatePorts("port", true);

  const mainForm = document.getElementById("jobForm");
  if (mainForm) {
    mainForm.addEventListener("input", checkFormValidity);
    mainForm.addEventListener("change", checkFormValidity);
  }

  const updatePortsBtn = document.getElementById("updatePortsBtn");
  if (updatePortsBtn) {
    updatePortsBtn.addEventListener("click", () => {
      fetchAndPopulatePorts("port", true);
    });
  }

  const portSelectMain = document.getElementById("port");
  if (portSelectMain) {
    portSelectMain.addEventListener("change", async (event) => {
      selectedPort = event.target.value;
      if (selectedPort) {
        updatePortStatus(true, selectedPort);
        await fetchAndSetSerialNumber(selectedPort);
      } else {
        document.getElementById("NUMERO_SERIAL").value = "";
        updatePortStatus(false);
      }
      checkFormValidity();
    });
  }

  const searchSerialBtn = document.getElementById("searchSerialBtn");
  if (searchSerialBtn) {
    searchSerialBtn.addEventListener("click", async () => {
      const serialNumber = document.getElementById("NUMERO_SERIAL").value;
      if (
        !serialNumber ||
        serialNumber === "Error al detectar" ||
        serialNumber === "Detectando serial..."
      ) {
        alert("Por favor, detecta o ingresa un número serial válido primero.");
        return;
      }
      try {
        const response = await fetch(
          `/search_serial?serial_number=${serialNumber.trim()}`
        );
        const result = await response.json();
        if (
          result.status === "success" &&
          result.data &&
          result.data.env_vars
        ) {
          const data = result.data.env_vars;
          Object.keys(data).forEach((key) => {
            const field = document.getElementById(key);
            if (
              field &&
              key !== "USER" &&
              key !== "UUID" &&
              key !== "NUMERO_SERIAL"
            )
              field.value = data[key];
          });
          alert("Datos del último certificado cargados en el formulario.");
        } else {
          alert(
            result.message ||
              "No se encontró información para este serial o hubo un error."
          );
        }
      } catch (error) {
        console.error("Error al buscar número serial para rellenar:", error);
        alert("Ocurrió un error al buscar la información del serial.");
      }
      checkFormValidity();
    });
  }

  const searchCertBtn = document.getElementById("searchCertificateBtn");
  if (searchCertBtn) {
    searchCertBtn.addEventListener("click", async () => {
      const serialNumber = document.getElementById("NUMERO_SERIAL").value;
      if (
        !serialNumber ||
        serialNumber === "Error al detectar" ||
        serialNumber === "Detectando serial..."
      ) {
        alert(
          "Por favor, detecta o ingresa un número serial válido para buscar programaciones."
        );
        return;
      }
      const certificatesList = document.getElementById("certificatesList");
      certificatesList.innerHTML = "<p>Buscando programaciones...</p>";
      try {
        const response = await fetch(
          `/search_certificates?serial_number=${serialNumber.trim()}`
        );
        const result = await response.json();
        certificatesList.innerHTML = "";
        if (
          result.status === "success" &&
          Array.isArray(result.data) &&
          result.data.length > 0
        ) {
          result.data.forEach((cert) => {
            const data = cert.document_data; // Aquí data ya está serializada por el backend si es necesario
            const card = document.createElement("div");
            card.className = "card m-2";
            card.style.minWidth = "250px";
            // Convertir la fecha a formato local legible
            const dateObject = new Date(data.date || data.timestamp); // Usar data.timestamp si data.date no existe
            const formattedDate = dateObject.toLocaleString();

            card.innerHTML = `
              <div class="card-body">
                <h5 class="card-title">ID: ${cert.document_id.substring(
                  0,
                  8
                )}...</h5>
                <p class="card-text">
                  <strong>Fecha:</strong> ${formattedDate}<br>
                  <strong>Prog:</strong> ${data.user || "N/A"}<br>
                  <strong>Patente:</strong> ${data.env_vars?.PATENTE || "N/A"}
                </p>
                <button class="btn btn-primary btn-sm" onclick='showCertificateDetails(${JSON.stringify(
                  data
                )})'>
                  <i class="fas fa-eye"></i> Ver detalles
                </button>
              </div>`;
            certificatesList.appendChild(card);
          });
        } else {
          certificatesList.innerHTML =
            "<p>No se encontraron programaciones previas para este número serial.</p>";
        }
      } catch (error) {
        console.error("Error al buscar programaciones:", error);
        certificatesList.innerHTML =
          "<p>Ocurrió un error al buscar programaciones.</p>";
      }
    });
  }

  const resetButton = document.getElementById("resetButton");
  if (resetButton) {
    resetButton.addEventListener("click", async () => {
      if (!selectedPort) {
        alert(
          "Debe seleccionar/conectar un puerto primero para poder resetear el dispositivo."
        );
        return;
      }
      if (
        !confirm(
          "¿Estás seguro de que deseas resetear el firmware del taxímetro al de lectura de serial?"
        )
      ) {
        return;
      }
      addLogMessage("Iniciando reseteo de firmware del Cibtron...", "system");
      try {
        const formData = new FormData();
        formData.append("port", selectedPort);
        const response = await fetch("/resetcibtron", {
          method: "POST",
          body: formData,
        });
        const result = await response.json();
        if (result.status === "success") {
          addLogMessage(
            "Dispositivo reseteado con firmware de lectura de serial. Refrescando...",
            "system"
          );
          alert(
            "El dispositivo ha sido reseteado. La página se recargará para reflejar los cambios."
          );
          location.reload();
        } else {
          addLogMessage(`Error al resetear: ${result.message}`, "system");
          alert(`Error al resetear: ${result.message}`);
        }
      } catch (error) {
        console.error("Error al resetear:", error);
        addLogMessage(`Error al resetear: ${error.message}`, "system");
        alert(`Error al resetear: ${error.message}`);
      }
    });
  }

  const setSerialButtonModalTrigger =
    document.getElementById("setSerialButton");
  if (setSerialButtonModalTrigger) {
    setSerialButtonModalTrigger.addEventListener("click", () => {
      const mainPortSelect = document.getElementById("port");
      const modalPortSelect = document.getElementById("modal_port_set_serial");
      if (mainPortSelect && modalPortSelect) {
        modalPortSelect.innerHTML = "";
        modalPortSelect.appendChild(
          new Option("Selecciona un puerto...", "", true, true)
        );
        Array.from(mainPortSelect.options).forEach((opt) => {
          if (opt.value && !opt.disabled) {
            modalPortSelect.appendChild(new Option(opt.text, opt.value));
          }
        });
        modalPortSelect.value = mainPortSelect.value || "";
      }
      document.getElementById("modal_NUMERO_SERIAL_A_PROGRAMAR").value = "";
      document.getElementById("modal_CLAVE_ACCESO").value = "";
      isSetSerialProgramFinished = false;
      lastSetSerialStatus = null;

      // --- RESETEAR EL BOTÓN DEL MODAL ---
      const executeBtnModal = document.getElementById(
        "executeSetSerialJobButton"
      );
      if (executeBtnModal) {
        executeBtnModal.disabled = false;
        executeBtnModal.textContent = "Ejecutar Programación de Serial";
      }
      // --- FIN DEL RESETEO ---
    });
  }

  const executeSetSerialJobButton = document.getElementById(
    "executeSetSerialJobButton"
  );
  if (executeSetSerialJobButton) {
    executeSetSerialJobButton.addEventListener("click", async () => {
      const serialToProgram = document
        .getElementById("modal_NUMERO_SERIAL_A_PROGRAMAR")
        .value.trim();
      const accessKey = document
        .getElementById("modal_CLAVE_ACCESO")
        .value.trim();
      const portForSetSerial = document.getElementById(
        "modal_port_set_serial"
      ).value;
      const userEmailField = document.getElementById("USER");

      if (!userEmailField || !userEmailField.value) {
        alert("Error: No se pudo obtener el email del programador.");
        return;
      }
      const userForJob = userEmailField.value;

      if (!serialToProgram || !/^\d+$/.test(serialToProgram)) {
        alert("Por favor, ingresa un número serial válido (solo números).");
        return;
      }
      if (!accessKey || accessKey.length !== 10) {
        alert(
          "Por favor, ingresa una clave de acceso válida de 10 caracteres."
        );
        return;
      }
      if (!portForSetSerial) {
        alert(
          "Por favor, selecciona un puerto para la programación del dispositivo."
        );
        return;
      }

      addLogMessage(
        `Iniciando proceso para programar nuevo serial: ${serialToProgram}`,
        "set_serial"
      );
      updateGlobalJobStatusUI(
        "Iniciando programación de nuevo serial...",
        "set_serial"
      );
      isSetSerialProgramFinished = false;
      lastSetSerialStatus = null;

      const uuidForJob = generateUUID();

      const formData = new FormData();
      formData.append("NUMERO_SERIAL_A_PROGRAMAR", serialToProgram);
      formData.append("CLAVE_ACCESO", accessKey);
      formData.append("USER", userForJob);
      formData.append("UUID", uuidForJob);
      formData.append("port", portForSetSerial);

      $("#setSerialModal").modal("hide");
      executeSetSerialJobButton.disabled = true;
      executeSetSerialJobButton.textContent = "Iniciando Job...";

      try {
        const response = await fetch("/execute_set_serial_job", {
          method: "POST",
          body: formData,
        });
        const result = await response.json();

        if (result.status === "success") {
          addLogMessage(
            `Job para programar serial ${result.serial_programmed} (UUID: ${result.uuid}) iniciado. Monitoreando...`,
            "set_serial"
          );
          // El texto del botón se actualizará por Socket.IO
        } else {
          addLogMessage(
            `Error al iniciar job de programación de serial: ${result.message}`,
            "set_serial"
          );
          alert(`Error al iniciar job: ${result.message}`);
          updateGlobalJobStatusUI("Error al iniciar", "set_serial");
          executeSetSerialJobButton.disabled = false;
          executeSetSerialJobButton.textContent =
            "Ejecutar Programación de Serial";
        }
      } catch (error) {
        console.error("Error en fetch /execute_set_serial_job:", error);
        addLogMessage(
          `Error de red al ejecutar job de programación de serial: ${error.message}`,
          "set_serial"
        );
        alert(`Error de red: ${error.message}`);
        updateGlobalJobStatusUI("Error de red", "set_serial");
        executeSetSerialJobButton.disabled = false;
        executeSetSerialJobButton.textContent =
          "Ejecutar Programación de Serial";
      }
    });
  }
}); // Fin de DOMContentLoaded

if (socket) {
  socket.on("connect", () => {
    console.log("Socket.IO: Conectado al servidor.");
    addLogMessage("Conectado al servidor de notificaciones.", "system");
  });

  socket.on("disconnect", () => {
    console.warn("Socket.IO: Desconectado del servidor.");
    addLogMessage("Desconectado del servidor de notificaciones.", "system");
  });

  socket.on("connect_error", (error) => {
    console.error("Socket.IO: Error de conexión:", error);
    addLogMessage(
      `Error de conexión al servidor de notificaciones: ${error.message}`,
      "system"
    );
  });

  socket.on("job_status_update", (data) => {
    // Para programación de parámetros
    const currentStatus = data.status;
    const message = data.message || "";
    console.log(
      "Socket.IO (Job Parámetros) - Estado:",
      currentStatus,
      "Mensaje:",
      message
    );

    if (currentStatus !== lastStatus) {
      addLogMessage(`Estado: ${currentStatus}. ${message}`, "params");
      lastStatus = currentStatus;
    }
    updateGlobalJobStatusUI(currentStatus, "params");

    const executeBtn = document.getElementById("executeButton");
    if (currentStatus === "Finalizado") {
      if (!isProgramFinished) {
        isProgramFinished = true;
        if (executeBtn) {
          executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        }
        alert(
          `Programación de parámetros para ${
            document.getElementById("NUMERO_SERIAL").value
          } completada. Desconecte el taxímetro.`
        );
        if (!isReloading) {
          isReloading = true; // Prevenir múltiples reloads o acciones
          clearMainForm();
          fetchAndPopulatePorts("port", true);
          document.getElementById("certificatesList").innerHTML = "";
          clearLogs();
          // location.reload(); // Opcional, si prefieres recarga completa
        }
      }
    } else if (
      currentStatus &&
      (currentStatus.toLowerCase().includes("error") ||
        currentStatus === "failed" ||
        currentStatus === "blocked")
    ) {
      if (executeBtn) {
        executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        executeBtn.disabled = false;
      }
      isProgramFinished = true;
    } else if (currentStatus && executeBtn) {
      executeBtn.disabled = true;
      executeBtn.textContent = currentStatus;
    }
  });

  socket.on("set_serial_job_status_update", (data) => {
    // Para seteo de serial
    const currentStatus = data.status;
    const message = data.message || "";
    const logData = data.log_data || {};
    const serialProgrammed =
      logData.numero_serial_a_programar ||
      document.getElementById("modal_NUMERO_SERIAL_A_PROGRAMAR")?.value ||
      "desconocido";

    console.log(
      "Socket.IO (Set Serial) - Estado:",
      currentStatus,
      "Mensaje:",
      message
    );

    if (currentStatus !== lastSetSerialStatus) {
      addLogMessage(`Estado: ${currentStatus}. ${message}`, "set_serial");
      lastSetSerialStatus = currentStatus;
    }
    updateGlobalJobStatusUI(currentStatus, "set_serial");

    const setSerialExecuteBtn = document.getElementById(
      "executeSetSerialJobButton"
    );

    if (currentStatus === "Programación de Serial Completa") {
      if (!isSetSerialProgramFinished) {
        isSetSerialProgramFinished = true;
        alert(
          `El nuevo serial ${serialProgrammed} ha sido programado en el dispositivo. Puede desconectarlo.`
        );
        if (setSerialExecuteBtn) {
          setSerialExecuteBtn.disabled = false;
          setSerialExecuteBtn.textContent = "Ejecutar Programación de Serial";
        }
        // Podrías querer limpiar el formulario principal y recargar puertos
        // para que el nuevo serial se detecte si el usuario vuelve a conectar.
        clearMainForm();
        fetchAndPopulatePorts("port", true);
      }
    } else if (
      currentStatus &&
      (currentStatus.toLowerCase().includes("error") ||
        currentStatus === "failed" ||
        currentStatus === "auth_failed") // Incluir auth_failed si es un estado de error de tu job
    ) {
      alert(
        `Error durante la programación del nuevo serial ${serialProgrammed}: ${message}`
      );
      if (setSerialExecuteBtn) {
        setSerialExecuteBtn.disabled = false;
        setSerialExecuteBtn.textContent = "Ejecutar Programación de Serial";
      }
      isSetSerialProgramFinished = true;
    } else if (currentStatus && setSerialExecuteBtn) {
      // Estados intermedios como "Programando Dispositivo", "Ejecutando Job Remoto"
      setSerialExecuteBtn.disabled = true;
      setSerialExecuteBtn.textContent = currentStatus;
    } else if (
      setSerialExecuteBtn &&
      !currentStatus &&
      !isSetSerialProgramFinished
    ) {
      // Si el estado es indefinido y el job no ha terminado, resetear por si acaso.
      // Esto es un fallback, idealmente siempre habrá un estado.
      setSerialExecuteBtn.disabled = false;
      setSerialExecuteBtn.textContent = "Ejecutar Programación de Serial";
    }
  });
} else {
  console.error(
    "Socket.IO no está disponible. Las actualizaciones en tiempo real no funcionarán."
  );
  alert(
    "No se pudo conectar al servidor de notificaciones. Algunas funcionalidades pueden no estar disponibles."
  );
}

async function executeAndProgram(event) {
  event.preventDefault();
  isProgramFinished = false;
  isReloading = false;
  lastStatus = null;

  const formData = new FormData(document.getElementById("jobForm"));

  addLogMessage("Iniciando programación de parámetros...", "params");
  updateGlobalJobStatusUI("Iniciando...", "params");

  const executeBtn = document.getElementById("executeButton");
  if (executeBtn) {
    executeBtn.disabled = true;
    executeBtn.textContent = "Procesando...";
  }

  try {
    const response = await fetch("/execute_and_program", {
      method: "POST",
      body: formData,
    });
    const result = await response.json();
    if (result.status === "success") {
      addLogMessage(
        "Job de programación de parámetros iniciado. Monitoreando...",
        "params"
      );
    } else {
      addLogMessage(
        `Error al iniciar job de parámetros: ${result.message}`,
        "params"
      );
      alert(`Error: ${result.message}`);
      if (executeBtn) {
        executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
        executeBtn.disabled = false;
      }
      updateGlobalJobStatusUI("Error", "params");
    }
  } catch (error) {
    console.error("Error en fetch /execute_and_program:", error);
    addLogMessage(
      `Error de red al ejecutar job de parámetros: ${error.message}`,
      "params"
    );
    alert(`Error de red: ${error.message}`);
    if (executeBtn) {
      executeBtn.textContent = "Programar Parámetros del Cibtron WB-001";
      executeBtn.disabled = false;
    }
    updateGlobalJobStatusUI("Error de red", "params");
  }
}

function logout() {
  if (!confirm("¿Estás seguro de que deseas cerrar sesión?")) return;
  fetch("/logout", { method: "POST", credentials: "include" })
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

function showCertificateDetails(data) {
  const modalContent = document.getElementById("modalContent");
  if (!modalContent) return;
  modalContent.innerHTML = "";

  const fieldMapping = {
    UUID: "UUID Compilación",
    USER: "Programador",
    DATE: "Fecha Compilación",
    NUMERO_SERIAL: "N° Serie Dispositivo",
    NUMERO_SELLO: "N° Sello",
    MARCA_TAXIMETRO: "Marca Taxímetro",
    MODELO_TAXIMETRO: "Modelo Taxímetro",
    NOMBRE_PROPIETARIO: "Nombre Propietario",
    APELLIDO_PROPIETARIO: "Apellido Propietario",
    MARCA_VEHICULO: "Marca Vehículo",
    YEAR_VEHICULO: "Año Vehículo",
    PATENTE: "Patente",
    RESOLUCION: "Resolución",
    CANTIDAD_PULSOS: "Divisor Pulsos",
    TARIFA_INICIAL: "Tarifa Inicial ($)",
    TARIFA_CAIDA_PARCIAL_METROS: "Caída Metros ($)",
    TARIFA_CAIDA_PARCIAL_MINUTO: "Caída Minuto ($)",
    MOSTRAR_VELOCIDAD_EN_PANTALLA: "Mostrar Metros",
    COLOR_FONDO_PANTALLA: "Color Fondo",
    COLOR_LETRAS_PANTALLA: "Color Letras",
    COLOR_PRECIO_PANTALLA: "Color Precio",
    PROPAGANDA_1: "Propaganda 1",
    PROPAGANDA_2: "Propaganda 2",
    PROPAGANDA_3: "Propaganda 3",
    PROPAGANDA_4: "Propaganda 4",
    COMENTARIO: "Comentario Programación",
  };

  // Convertir la fecha a formato local legible si existe
  // Nota: data.date o data.timestamp ya deberían ser strings ISO por la conversión en backend
  let formattedDate = "N/A";
  if (data.date || data.timestamp) {
    try {
      const dateString = data.date || data.timestamp;
      const dateObject = new Date(dateString);
      if (!isNaN(dateObject)) {
        // Verificar si la fecha es válida
        formattedDate = dateObject.toLocaleString();
      } else {
        formattedDate = dateString; // Si no es válida, mostrar el string original
      }
    } catch (e) {
      console.warn("Error formateando fecha de certificado:", e);
      formattedDate = data.date || data.timestamp || "Error al formatear fecha";
    }
  }

  if (data.env_vars && typeof data.env_vars === "object") {
    data.env_vars.DATE = formattedDate; // Sobrescribir o añadir la fecha formateada
  } else if (data) {
    // Si no hay env_vars pero sí data, añadirlo directamente
    data.DATE = formattedDate;
  }

  if (data && (data.env_vars || typeof data === "object")) {
    // Verificar si data o data.env_vars es un objeto
    const source = data.env_vars || data; // Usar data.env_vars si existe, sino data directamente

    const orderedKeys = [
      "UUID",
      "USER",
      "DATE",
      "NUMERO_SERIAL",
      "NUMERO_SELLO",
      "PATENTE",
      "NOMBRE_PROPIETARIO",
      "APELLIDO_PROPIETARIO",
      "MARCA_VEHICULO",
      "YEAR_VEHICULO",
      "TARIFA_INICIAL",
      "TARIFA_CAIDA_PARCIAL_METROS",
      "TARIFA_CAIDA_PARCIAL_MINUTO",
      "RESOLUCION",
      "CANTIDAD_PULSOS",
      "COMENTARIO",
    ];

    const displayedKeys = new Set();

    orderedKeys.forEach((key) => {
      if (
        fieldMapping[key] &&
        typeof source === "object" &&
        source !== null &&
        source.hasOwnProperty(key)
      ) {
        const value =
          source[key] !== undefined && source[key] !== "" ? source[key] : "N/A";
        // Para el comentario, podríamos querer un formato diferente si es largo
        if (key === "COMENTARIO" && value !== "N/A") {
          modalContent.innerHTML += `<p><strong>${fieldMapping[key]}:</strong></p><div style="white-space: pre-wrap; background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 5px; border-radius: 3px; margin-top: -10px; margin-bottom: 10px;">${value}</div>`;
        } else {
          modalContent.innerHTML += `<p><strong>${fieldMapping[key]}:</strong> ${value}</p>`;
        }
        displayedKeys.add(key);
      }
    });

    if (typeof source === "object" && source !== null) {
      // Solo iterar si source es un objeto
      for (const [key, title] of Object.entries(fieldMapping)) {
        if (!displayedKeys.has(key) && source.hasOwnProperty(key)) {
          const value =
            source[key] !== undefined && source[key] !== ""
              ? source[key]
              : "N/A";
          if (key === "COMENTARIO" && value !== "N/A") {
            modalContent.innerHTML += `<p><strong>${title}:</strong></p><div style="white-space: pre-wrap; background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 5px; border-radius: 3px; margin-top: -10px; margin-bottom: 10px;">${value}</div>`;
          } else {
            modalContent.innerHTML += `<p><strong>${title}:</strong> ${value}</p>`;
          }
        }
      }
    }

    if (typeof source === "object" && source !== null) {
      if (source.path) {
        modalContent.innerHTML += `<p><strong>Ruta Binario (Params):</strong> ${source.path}</p>`;
      }
      if (source.binary_path) {
        modalContent.innerHTML += `<p><strong>Ruta Binario (Set Serial):</strong> ${source.binary_path}</p>`;
      }
    }
  } else {
    modalContent.innerHTML =
      "<p>No hay detalles disponibles para esta programación.</p>";
  }
  $("#detailsModal").modal("show");
}

setInterval(async () => {
  if (!selectedPort) {
    return;
  }
  try {
    const response = await fetch("/check_port_status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port: selectedPort }),
    });
    const data = await response.json();
    if (data.status === "success") {
      updatePortStatus(data.connected, selectedPort);
      if (!data.connected) {
        addLogMessage(
          `Puerto ${selectedPort} desconectado. Actualizando...`,
          "system"
        );
        selectedPort = null;
        clearMainForm();
        fetchAndPopulatePorts("port", true);
      }
    } else {
      updatePortStatus(false);
    }
  } catch (error) {
    updatePortStatus(false);
  }
}, 3000);
