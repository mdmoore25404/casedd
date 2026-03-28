const API_ROOT = "";

async function readJson(response) {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

export async function fetchPanels() {
  const response = await fetch(`${API_ROOT}/api/panels`, { cache: "no-store" });
  return readJson(response);
}

export async function postTemplateOverride(panel, template) {
  const response = await fetch(`${API_ROOT}/api/template/override`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ panel, template }),
  });
  return readJson(response);
}

export async function postDataUpdate(mapping) {
  const response = await fetch(`${API_ROOT}/api/update`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ update: mapping }),
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
}

export async function setTestMode(enabled) {
  const response = await fetch(`${API_ROOT}/api/test-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return readJson(response);
}

export async function getTestMode() {
  const response = await fetch(`${API_ROOT}/api/test-mode`, { cache: "no-store" });
  return readJson(response);
}

export async function startRandomSimulation(payload) {
  const response = await fetch(`${API_ROOT}/api/sim/random`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function startReplaySimulation(payload) {
  const response = await fetch(`${API_ROOT}/api/sim/replay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readJson(response);
}

export async function stopSimulation() {
  const response = await fetch(`${API_ROOT}/api/sim/stop`, {
    method: "POST",
  });
  return readJson(response);
}

export async function getSimulationStatus() {
  const response = await fetch(`${API_ROOT}/api/sim/status`, { cache: "no-store" });
  return readJson(response);
}

export async function fetchTemplates() {
  const response = await fetch(`${API_ROOT}/api/templates`, { cache: "no-store" });
  return readJson(response);
}

export async function fetchTemplate(name) {
  const response = await fetch(
    `${API_ROOT}/api/templates/${encodeURIComponent(name)}`,
    { cache: "no-store" },
  );
  return readJson(response);
}

export async function saveTemplate(name, template) {
  const response = await fetch(
    `${API_ROOT}/api/templates/${encodeURIComponent(name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template }),
    },
  );
  return readJson(response);
}

export async function exportTemplateFile(name) {
  const response = await fetch(
    `${API_ROOT}/api/templates/${encodeURIComponent(name)}/export`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.text();
}

export async function importTemplateFile(content, name = null) {
  const response = await fetch(`${API_ROOT}/api/templates/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, name }),
  });
  return readJson(response);
}
