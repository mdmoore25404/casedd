import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBolt,
  faCirclePlay,
  faExpand,
  faFileArrowDown,
  faFileArrowUp,
  faFlask,
  faFloppyDisk,
  faList,
  faPenRuler,
  faRotate,
  faTableCells,
  faToggleOn,
  faUpload,
  faWandMagicSparkles,
} from "@fortawesome/free-solid-svg-icons";

import {
  exportTemplateFile,
  fetchTemplate,
  fetchTemplates,
  fetchPanels,
  importTemplateFile,
  getSimulationStatus,
  getTestMode,
  postDataUpdate,
  postTemplateOverride,
  saveTemplate,
  setTestMode,
  startRandomSimulation,
  startReplaySimulation,
  stopSimulation,
} from "./api";

function usePolling(callback, intervalMs) {
  const callbackRef = useRef(callback);

  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);

  useEffect(() => {
    let mounted = true;
    let inFlight = false;

    const tick = async () => {
      if (!mounted || inFlight) {
        return;
      }
      inFlight = true;
      try {
        await callbackRef.current();
      } catch (_err) {
        // polling errors are surfaced in UI actions
      } finally {
        inFlight = false;
      }
    };

    void tick();
    const timer = window.setInterval(() => {
      void tick();
    }, intervalMs);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [intervalMs]);
}

function parseTemplateAreas(templateAreas) {
  const rows = String(templateAreas || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => line.replace(/^"|"$/g, ""));

  return rows.map((line) => line.split(/\s+/).filter((token) => token.length > 0));
}

function buildAreaLayout(areaRows) {
  const map = new Map();
  areaRows.forEach((row, rowIndex) => {
    row.forEach((name, colIndex) => {
      if (name === ".") {
        return;
      }
      const existing = map.get(name);
      if (!existing) {
        map.set(name, {
          rowStart: rowIndex + 1,
          rowEnd: rowIndex + 2,
          colStart: colIndex + 1,
          colEnd: colIndex + 2,
        });
        return;
      }

      existing.rowStart = Math.min(existing.rowStart, rowIndex + 1);
      existing.rowEnd = Math.max(existing.rowEnd, rowIndex + 2);
      existing.colStart = Math.min(existing.colStart, colIndex + 1);
      existing.colEnd = Math.max(existing.colEnd, colIndex + 2);
    });
  });
  return map;
}

function stringifyTemplateAreas(areaRows) {
  return areaRows.map((row) => `"${row.join(" ")}"`).join("\n");
}

const WIDGET_TYPES = [
  "panel",
  "value",
  "text",
  "bar",
  "gauge",
  "histogram",
  "sparkline",
  "image",
  "slideshow",
  "clock",
  "ups",
];

function prettyJson(value) {
  return JSON.stringify(value, null, 2);
}

function parseJson(rawText, label) {
  try {
    return { value: JSON.parse(rawText), error: "" };
  } catch (err) {
    const message = err instanceof Error ? err.message : `Invalid ${label} JSON`;
    return { value: null, error: message };
  }
}

function inferNumericRange(key, widget) {
  if (
    typeof widget?.min === "number" &&
    typeof widget?.max === "number" &&
    widget.max > widget.min
  ) {
    return { min: widget.min, max: widget.max };
  }

  const token = key.toLowerCase();
  if (token.includes("percent") || token.includes("pct")) {
    return { min: 0, max: 100 };
  }
  if (token.includes("temp")) {
    return { min: 30, max: 95 };
  }
  if (token.includes("rpm")) {
    return { min: 500, max: 2400 };
  }
  if (token.includes("mbps") || token.includes("speed") || token.includes("read") || token.includes("write")) {
    return { min: 0, max: 300 };
  }
  return { min: 0, max: 100 };
}

function isStringMetric(key, widget) {
  const token = key.toLowerCase();
  if (widget?.type === "text" || widget?.type === "clock") {
    return true;
  }
  return (
    token.includes("summary") ||
    token.includes("meta") ||
    token.includes("status") ||
    token.includes("state") ||
    token.includes("name") ||
    token.includes("hostname")
  );
}

function collectSourceEntries(template) {
  if (!template?.widgets) {
    return [];
  }

  const byKey = new Map();
  Object.values(template.widgets).forEach((widget) => {
    if (typeof widget?.source === "string" && widget.source.trim()) {
      byKey.set(widget.source, widget);
    }
    if (Array.isArray(widget?.sources)) {
      widget.sources.forEach((sourceKey) => {
        if (typeof sourceKey === "string" && sourceKey.trim()) {
          byKey.set(sourceKey, widget);
        }
      });
    }
  });

  return Array.from(byKey.entries()).map(([key, widget]) => ({ key, widget }));
}

function collectUpsPrefixedKeys(prefix) {
  const root = prefix && prefix.trim() ? prefix.trim().replace(/\.+$/, "") : "ups";
  return [
    `${root}.status`,
    `${root}.battery_percent`,
    `${root}.load_percent`,
    `${root}.load_watts`,
    `${root}.runtime_minutes`,
    `${root}.input_voltage`,
    `${root}.input_frequency`,
  ];
}

function generateScenarioData(template) {
  const entries = collectSourceEntries(template);
  if (template?.widgets) {
    Object.values(template.widgets).forEach((widget) => {
      if (widget?.type !== "ups") {
        return;
      }
      collectUpsPrefixedKeys(widget?.source || "ups").forEach((key) => {
        if (!entries.some((entry) => entry.key === key)) {
          entries.push({ key, widget });
        }
      });
    });
  }
  const updatePayload = {};
  const randomFields = [];

  entries.forEach(({ key, widget }) => {
    if (widget?.type === "ups") {
      const prefix = widget?.source && widget.source.trim() ? widget.source.trim() : "ups";
      if (key === prefix) {
        return;
      }
    }

    if (isStringMetric(key, widget)) {
      const suffix = key.split(".").at(-1) || key;
      updatePayload[key] = `${suffix} sample`;
      return;
    }

    const range = inferNumericRange(key, widget);
    const middle = Number(((range.min + range.max) / 2).toFixed(2));
    updatePayload[key] = middle;
    randomFields.push({
      key,
      min: range.min,
      max: range.max,
      step: Math.max(1, Number(((range.max - range.min) / 20).toFixed(2))),
    });
  });

  const replayRecords = [0.6, 0.9, 0.7].map((scale, index) => {
    const update = {};
    entries.forEach(({ key, widget }) => {
      if (isStringMetric(key, widget)) {
        update[key] = updatePayload[key];
        return;
      }
      const range = inferNumericRange(key, widget);
      const value = range.min + ((range.max - range.min) * scale);
      update[key] = Number(value.toFixed(2));
    });

    return {
      at_ms: index * 1200,
      update,
    };
  });

  return {
    updatePayload,
    randomSimulation: {
      interval: 1.0,
      fields: randomFields,
    },
    replayRecords,
  };
}

export function App() {
  const [panelsData, setPanelsData] = useState({ panels: [], default_panel: "", test_mode: false });
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [templateDoc, setTemplateDoc] = useState(null);
  const [selectedWidget, setSelectedWidget] = useState("");
  const [templateDirty, setTemplateDirty] = useState(false);
  const [status, setStatus] = useState("ready");
  const [selectedPanel, setSelectedPanel] = useState("");
  const [templateName, setTemplateName] = useState("auto");
  const [overrideDraftDirty, setOverrideDraftDirty] = useState(false);
  const [jsonModalTarget, setJsonModalTarget] = useState("");
  const [jsonModalDraft, setJsonModalDraft] = useState("");
  const [previewNonce, setPreviewNonce] = useState(() => Date.now());
  const [updateJson, setUpdateJson] = useState(() => prettyJson({}));
  const [testModeEnabled, setTestModeEnabled] = useState(false);
  const [simStatus, setSimStatus] = useState({ running: false, mode: "idle" });
  const [randomJson, setRandomJson] = useState(() => prettyJson({ interval: 1.0, fields: [] }));
  const [replayJson, setReplayJson] = useState(() => prettyJson([]));
  const importInputRef = useRef(null);
  const selectedPanelRef = useRef("");

  const refreshPanels = useCallback(async () => {
    const payload = await fetchPanels();
    setPanelsData(payload);
    if (!selectedPanel) {
      setSelectedPanel(payload.default_panel || "");
    }
  }, [selectedPanel]);

  const refreshStatus = useCallback(async () => {
    const [tm, sim] = await Promise.all([getTestMode(), getSimulationStatus()]);
    setTestModeEnabled(Boolean(tm.enabled));
    setSimStatus(sim);
  }, []);

  const refreshTemplates = useCallback(async () => {
    const payload = await fetchTemplates();
    const names = payload.templates || [];
    setTemplates(names);
    if (!selectedTemplate && names.length > 0) {
      setSelectedTemplate(names[0]);
    }
  }, [selectedTemplate]);

  usePolling(refreshPanels, 2000);
  usePolling(refreshStatus, 1500);
  usePolling(refreshTemplates, 5000);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setPreviewNonce(Date.now());
    }, 2000);
    return () => window.clearInterval(timer);
  }, []);

  const panels = panelsData.panels || [];
  const selectedPanelData = useMemo(
    () => panels.find((item) => item.name === selectedPanel) || null,
    [panels, selectedPanel],
  );

  const areaRows = useMemo(() => {
    if (!templateDoc?.grid?.template_areas) {
      return [];
    }
    return parseTemplateAreas(templateDoc.grid.template_areas);
  }, [templateDoc]);

  const areaLayout = useMemo(() => buildAreaLayout(areaRows), [areaRows]);
  const widgetNames = useMemo(() => {
    if (!templateDoc?.widgets) {
      return [];
    }
    return Object.keys(templateDoc.widgets);
  }, [templateDoc]);

  const selectedWidgetConfig = useMemo(() => {
    if (!templateDoc?.widgets || !selectedWidget) {
      return null;
    }
    return templateDoc.widgets[selectedWidget] || null;
  }, [templateDoc, selectedWidget]);

  const previewUrl = useMemo(() => {
    if (!selectedPanel) {
      return "";
    }
    const panel = encodeURIComponent(selectedPanel);
    return `/image?panel=${panel}&t=${previewNonce}`;
  }, [selectedPanel, previewNonce]);

  const updateJsonState = useMemo(() => parseJson(updateJson, "update"), [updateJson]);
  const randomJsonState = useMemo(() => parseJson(randomJson, "random simulation"), [randomJson]);
  const replayJsonState = useMemo(() => parseJson(replayJson, "replay"), [replayJson]);

  const sourceListValue = useMemo(() => {
    if (!selectedWidgetConfig?.sources || !Array.isArray(selectedWidgetConfig.sources)) {
      return "";
    }
    return selectedWidgetConfig.sources.join("\n");
  }, [selectedWidgetConfig]);

  const applyScenarioFromTemplate = useCallback((template) => {
    const scenario = generateScenarioData(template);
    setUpdateJson(prettyJson(scenario.updatePayload));
    setRandomJson(prettyJson(scenario.randomSimulation));
    setReplayJson(prettyJson(scenario.replayRecords));
  }, []);

  const loadTemplateForEditor = useCallback(async (templateNameToLoad) => {
    if (!templateNameToLoad) {
      return;
    }
    const payload = await fetchTemplate(templateNameToLoad);
    setTemplateDoc(payload.template);
    setTemplateDirty(false);
    applyScenarioFromTemplate(payload.template);
    setStatus(`loaded template ${templateNameToLoad}`);
  }, [applyScenarioFromTemplate]);

  const openJsonModal = useCallback((target) => {
    if (target === "update") {
      setJsonModalDraft(updateJson);
    } else if (target === "random") {
      setJsonModalDraft(randomJson);
    } else if (target === "replay") {
      setJsonModalDraft(replayJson);
    } else {
      return;
    }
    setJsonModalTarget(target);
  }, [randomJson, replayJson, updateJson]);

  const modalJsonState = useMemo(() => {
    if (!jsonModalTarget) {
      return { value: null, error: "" };
    }
    return parseJson(jsonModalDraft, `${jsonModalTarget} modal`);
  }, [jsonModalDraft, jsonModalTarget]);

  const closeJsonModal = useCallback(() => {
    setJsonModalTarget("");
    setJsonModalDraft("");
  }, []);

  const applyJsonModal = useCallback(() => {
    if (modalJsonState.error) {
      setStatus("fix modal JSON before applying");
      return;
    }
    if (jsonModalTarget === "update") {
      setUpdateJson(prettyJson(modalJsonState.value));
    } else if (jsonModalTarget === "random") {
      setRandomJson(prettyJson(modalJsonState.value));
    } else if (jsonModalTarget === "replay") {
      setReplayJson(prettyJson(modalJsonState.value));
    }
    closeJsonModal();
  }, [closeJsonModal, jsonModalTarget, modalJsonState.error, modalJsonState.value]);

  useEffect(() => {
    if (!selectedWidget && widgetNames.length > 0) {
      setSelectedWidget(widgetNames[0]);
      return;
    }
    if (selectedWidget && !widgetNames.includes(selectedWidget)) {
      setSelectedWidget(widgetNames[0] || "");
    }
  }, [selectedWidget, widgetNames]);

  useEffect(() => {
    if (!selectedPanelData) {
      return;
    }
    const panelName = String(selectedPanelData.name || "");
    const panelChanged = selectedPanelRef.current !== panelName;
    if (panelChanged) {
      selectedPanelRef.current = panelName;
      setOverrideDraftDirty(false);
    }
    if (!selectedTemplate && selectedPanelData.current_template) {
      setSelectedTemplate(String(selectedPanelData.current_template));
    }
    if (panelChanged || !overrideDraftDirty) {
      setTemplateName(
        selectedPanelData.forced_template
          ? String(selectedPanelData.forced_template)
          : "auto",
      );
    }
  }, [overrideDraftDirty, selectedPanelData, selectedTemplate]);

  useEffect(() => {
    if (!selectedTemplate) {
      return;
    }
    let cancelled = false;
    const run = async () => {
      try {
        const payload = await fetchTemplate(selectedTemplate);
        if (cancelled) {
          return;
        }
        setTemplateDoc(payload.template);
        setTemplateDirty(false);
        applyScenarioFromTemplate(payload.template);
      } catch (_err) {
        if (!cancelled) {
          setStatus(`failed to load template ${selectedTemplate}`);
        }
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [applyScenarioFromTemplate, selectedTemplate]);

  function updateTemplate(mutator) {
    setTemplateDoc((previous) => {
      if (!previous) {
        return previous;
      }
      const next = mutator(previous);
      setTemplateDirty(true);
      return next;
    });
  }

  function updateGridField(field, value) {
    updateTemplate((previous) => ({
      ...previous,
      grid: {
        ...previous.grid,
        [field]: value,
      },
    }));
  }

  function updateWidgetField(field, value) {
    if (!selectedWidget) {
      return;
    }
    updateTemplate((previous) => ({
      ...previous,
      widgets: {
        ...previous.widgets,
        [selectedWidget]: {
          ...previous.widgets[selectedWidget],
          [field]: value,
        },
      },
    }));
  }

  function updateWidgetNumberField(field, rawValue) {
    if (!selectedWidget) {
      return;
    }
    if (rawValue.trim() === "") {
      updateWidgetField(field, null);
      return;
    }
    const parsed = Number(rawValue);
    if (Number.isNaN(parsed)) {
      setStatus(`invalid number for ${field}`);
      return;
    }
    updateWidgetField(field, parsed);
  }

  function updateWidgetSources(rawValue) {
    const parsed = rawValue
      .split("\n")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
    updateWidgetField("sources", parsed);
  }

  async function handleLoadTemplate() {
    if (!selectedTemplate) {
      setStatus("select a template first");
      return;
    }
    await loadTemplateForEditor(selectedTemplate);
  }

  async function handleSaveTemplate() {
    if (!selectedTemplate || !templateDoc) {
      setStatus("load a template before saving");
      return;
    }
    const payload = await saveTemplate(selectedTemplate, templateDoc);
    setTemplateDoc(payload.template);
    setTemplateDirty(false);
    setStatus(`saved ${selectedTemplate} and applied live`);
    setPreviewNonce(Date.now());
    await refreshPanels();
  }

  async function handleTemplateForce() {
    if (!selectedPanel) {
      setStatus("select a panel first");
      return;
    }
    const forced = templateName === "auto" ? null : templateName;
    await postTemplateOverride(selectedPanel, forced);
    setOverrideDraftDirty(false);
    setStatus(
      forced
        ? `forced ${forced} on ${selectedPanel}`
        : `cleared override on ${selectedPanel}`,
    );
    await refreshPanels();
  }

  async function handlePushUpdate() {
    if (updateJsonState.error || typeof updateJsonState.value !== "object" || !updateJsonState.value) {
      setStatus("fix update JSON before pushing");
      return;
    }
    await postDataUpdate(updateJsonState.value);
    setStatus("update pushed");
  }

  async function handleToggleTestMode() {
    const next = !testModeEnabled;
    await setTestMode(next);
    setTestModeEnabled(next);
    setStatus(next ? "test mode enabled (getters disabled)" : "test mode disabled");
  }

  async function handleStartRandom() {
    if (randomJsonState.error || typeof randomJsonState.value !== "object" || !randomJsonState.value) {
      setStatus("fix random simulation JSON before starting");
      return;
    }
    if (!Array.isArray(randomJsonState.value.fields) || randomJsonState.value.fields.length === 0) {
      setStatus("random simulation needs at least one numeric field");
      return;
    }
    await startRandomSimulation(randomJsonState.value);
    setStatus("random simulation started");
    await refreshStatus();
  }

  async function handleStartReplay() {
    if (!Array.isArray(replayJsonState.value)) {
      setStatus("replay JSON must be an array of records");
      return;
    }
    const records = replayJsonState.value;
    if (records.length === 0 || !records.some((item) => item && item.update)) {
      setStatus("replay JSON needs at least one record with an update object");
      return;
    }
    await startReplaySimulation({ records, loop: true, speed: 1.0 });
    setStatus("replay simulation started");
    await refreshStatus();
  }

  async function handleStopSimulation() {
    await stopSimulation();
    setStatus("simulation stopped");
    await refreshStatus();
  }

  function handleGridAreaCellClick(areaName) {
    setSelectedWidget(areaName);
    if (!widgetNames.includes(areaName)) {
      updateTemplate((previous) => ({
        ...previous,
        widgets: {
          ...previous.widgets,
          [areaName]: {
            type: "text",
            content: areaName,
          },
        },
      }));
    }
  }

  function handleGridCellRename(rowIndex, colIndex, value) {
    const nextRows = areaRows.map((row) => [...row]);
    nextRows[rowIndex][colIndex] = value.trim() || ".";
    updateGridField("template_areas", stringifyTemplateAreas(nextRows));
  }

  async function handleExportTemplate() {
    if (!selectedTemplate) {
      setStatus("select a template to export");
      return;
    }
    const content = await exportTemplateFile(selectedTemplate);
    const blob = new Blob([content], { type: "text/yaml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${selectedTemplate}.casedd`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
    setStatus(`exported ${selectedTemplate}.casedd`);
  }

  async function handleImportFileChange(event) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    const content = await file.text();
    const stem = file.name.replace(/\.[^.]+$/, "");
    const suggestedName = stem.trim() || null;
    const payload = await importTemplateFile(content, suggestedName);
    await refreshTemplates();
    setSelectedTemplate(payload.name);
    setTemplateDoc(payload.template);
    setTemplateDirty(false);
    applyScenarioFromTemplate(payload.template);
    setStatus(`imported ${payload.name}.casedd`);
    event.target.value = "";
  }

  return (
    <div className="container-fluid py-3 app-shell">
      <div className="row g-3">
        <div className="col-12 col-lg-5 col-xl-4">
          <div className="card border-secondary bg-dark-subtle">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faList} /> Panels
              </h5>
              <select
                className="form-select form-select-sm mb-2"
                value={selectedPanel}
                onChange={(event) => setSelectedPanel(event.target.value)}
              >
                {(panels || []).map((panel) => (
                  <option key={panel.name} value={panel.name}>
                    {panel.display_name || panel.name}
                  </option>
                ))}
              </select>
              {selectedPanelData ? (
                <div className="small text-body-secondary">
                  <div>Current: {selectedPanelData.current_template || "n/a"}</div>
                  <div>Forced: {selectedPanelData.forced_template || "auto"}</div>
                  <div>Size: {selectedPanelData.width}x{selectedPanelData.height}</div>
                </div>
              ) : null}
              <hr />
              <label className="form-label small">Template override</label>
              <select
                className="form-select form-select-sm mb-2"
                value={templateName}
                onChange={(event) => {
                  setTemplateName(event.target.value);
                  setOverrideDraftDirty(true);
                }}
              >
                <option value="auto">auto</option>
                {templates.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <button className="btn btn-primary btn-sm" onClick={() => void handleTemplateForce()}>
                <FontAwesomeIcon icon={faWandMagicSparkles} className="me-1" /> Apply
              </button>
            </div>
          </div>

          <div className="card border-secondary bg-dark-subtle mt-3">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faPenRuler} /> Template Editor
              </h5>
              <div className="d-flex gap-2 mb-2">
                <select
                  className="form-select form-select-sm"
                  value={selectedTemplate}
                  onChange={(event) => setSelectedTemplate(event.target.value)}
                >
                  {(templates || []).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
                <button
                  className="btn btn-outline-light btn-sm"
                  onClick={() => void handleLoadTemplate()}
                >
                  Load
                </button>
                <button
                  className="btn btn-success btn-sm"
                  onClick={() => void handleSaveTemplate()}
                  disabled={!templateDoc}
                >
                  <FontAwesomeIcon icon={faFloppyDisk} className="me-1" />
                  Save
                </button>
              </div>
              <div className="d-flex flex-wrap gap-2 mb-2">
                <button
                  className="btn btn-outline-light btn-sm"
                  onClick={() => void handleExportTemplate()}
                  disabled={!selectedTemplate}
                >
                  <FontAwesomeIcon icon={faFileArrowDown} className="me-1" />
                  Export .casedd
                </button>
                <button
                  className="btn btn-outline-light btn-sm"
                  onClick={() => importInputRef.current?.click()}
                >
                  <FontAwesomeIcon icon={faFileArrowUp} className="me-1" />
                  Import .casedd
                </button>
                <input
                  ref={importInputRef}
                  type="file"
                  accept=".casedd,.yaml,.yml"
                  className="d-none"
                  onChange={(event) => void handleImportFileChange(event)}
                />
              </div>
              <div className="small text-body-secondary">
                {templateDoc ? (
                  <>
                    <div>Editing: {selectedTemplate}</div>
                    <div>Unsaved changes: {templateDirty ? "yes" : "no"}</div>
                  </>
                ) : (
                  <div>Load a template to start editing.</div>
                )}
              </div>
            </div>
          </div>

          <div className="card border-secondary bg-dark-subtle mt-3">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faFlask} /> Test Mode
              </h5>
              <p className="small text-body-secondary mb-2">
                Global toggle. When enabled, all getters are disabled.
              </p>
              <button className="btn btn-warning btn-sm" onClick={() => void handleToggleTestMode()}>
                <FontAwesomeIcon icon={faToggleOn} className="me-1" />
                {testModeEnabled ? "Disable test mode" : "Enable test mode"}
              </button>
            </div>
          </div>
        </div>

        <div className="col-12 col-lg-7 col-xl-8">
          <div className="card border-secondary bg-dark-subtle mb-3">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faTableCells} /> Layout + Live Preview
              </h5>
              <div className="row g-3">
                <div className="col-12 col-xl-7">
                  <div className="preview-wrap mb-2">
                    {previewUrl ? (
                      <img src={previewUrl} alt="Live panel preview" className="preview-image" />
                    ) : (
                      <div className="small text-body-secondary">Select a panel for preview.</div>
                    )}
                  </div>
                  <button
                    className="btn btn-outline-light btn-sm"
                    onClick={() => setPreviewNonce(Date.now())}
                  >
                    <FontAwesomeIcon icon={faRotate} className="me-1" /> Refresh frame
                  </button>
                </div>
                <div className="col-12 col-xl-5">
                  {templateDoc ? (
                    <>
                      <label className="form-label small">Grid columns</label>
                      <input
                        className="form-control form-control-sm mb-2 font-monospace"
                        value={templateDoc.grid?.columns || ""}
                        onChange={(event) => updateGridField("columns", event.target.value)}
                      />
                      <label className="form-label small">Grid rows</label>
                      <input
                        className="form-control form-control-sm mb-2 font-monospace"
                        value={templateDoc.grid?.rows || ""}
                        onChange={(event) => updateGridField("rows", event.target.value)}
                      />
                      <label className="form-label small">Template background</label>
                      <input
                        className="form-control form-control-sm mb-2"
                        value={templateDoc.background || ""}
                        onChange={(event) =>
                          updateTemplate((previous) => ({
                            ...previous,
                            background: event.target.value,
                          }))
                        }
                      />
                      <div className="grid-syntax-help small text-body-secondary">
                        <div>Syntax: whitespace-separated track sizes per axis.</div>
                        <div>Use `fr`, `px`, or `%` units. Example: `1fr 2fr 160px`.</div>
                        <div>Grid columns count should match each `template_areas` row token count.</div>
                        <div>Grid rows count should match the number of `template_areas` lines.</div>
                      </div>
                    </>
                  ) : null}
                </div>
              </div>

              {templateDoc ? (
                <div
                  className="editor-grid mt-3"
                  style={{
                    gridTemplateColumns: `repeat(${Math.max(1, areaRows[0]?.length || 1)}, minmax(0, 1fr))`,
                  }}
                >
                  {areaRows.map((row, rowIndex) =>
                    row.map((cellName, colIndex) => (
                      <button
                        key={`${rowIndex}-${colIndex}`}
                        className={`btn btn-sm editor-grid-cell ${selectedWidget === cellName ? "is-active" : ""}`}
                        onClick={() => handleGridAreaCellClick(cellName)}
                        title="Click to select this area"
                        type="button"
                      >
                        <input
                          className="grid-cell-input"
                          value={cellName}
                          onChange={(event) =>
                            handleGridCellRename(rowIndex, colIndex, event.target.value)
                          }
                          onClick={(event) => event.stopPropagation()}
                        />
                      </button>
                    )),
                  )}
                </div>
              ) : null}

              {templateDoc ? (
                <div
                  className="editor-area-map mt-3"
                  style={{
                    gridTemplateColumns: `repeat(${Math.max(1, areaRows[0]?.length || 1)}, minmax(0, 1fr))`,
                    gridTemplateRows: `repeat(${Math.max(1, areaRows.length)}, minmax(56px, 1fr))`,
                  }}
                >
                  {Array.from(areaLayout.entries()).map(([name, box]) => {
                    const widgetType = templateDoc.widgets?.[name]?.type || "(missing)";
                    return (
                      <button
                        key={name}
                        className={`editor-area ${selectedWidget === name ? "is-active" : ""}`}
                        style={{
                          gridColumn: `${box.colStart} / ${box.colEnd}`,
                          gridRow: `${box.rowStart} / ${box.rowEnd}`,
                        }}
                        onClick={() => handleGridAreaCellClick(name)}
                        type="button"
                      >
                        <div className="fw-semibold">{name}</div>
                        <div className="small opacity-75">{widgetType}</div>
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>

          {templateDoc ? (
            <div className="card border-secondary bg-dark-subtle mb-3">
              <div className="card-body">
                <h5 className="card-title">Widget Inspector</h5>
                <div className="row g-2">
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Widget</label>
                    <select
                      className="form-select form-select-sm"
                      value={selectedWidget}
                      onChange={(event) => setSelectedWidget(event.target.value)}
                    >
                      {widgetNames.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Type</label>
                    <select
                      className="form-select form-select-sm"
                      value={selectedWidgetConfig?.type || ""}
                      onChange={(event) => updateWidgetField("type", event.target.value)}
                    >
                      {WIDGET_TYPES.map((widgetType) => (
                        <option key={widgetType} value={widgetType}>
                          {widgetType}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Label</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.label || ""}
                      onChange={(event) => updateWidgetField("label", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Source (single)</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.source || ""}
                      onChange={(event) => updateWidgetField("source", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Sources (one per line)</label>
                    <textarea
                      className="form-control form-control-sm font-monospace"
                      rows={3}
                      value={sourceListValue}
                      onChange={(event) => updateWidgetSources(event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-6">
                    <label className="form-label small">Content</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.content || ""}
                      onChange={(event) => updateWidgetField("content", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-3">
                    <label className="form-label small">Unit</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.unit || ""}
                      onChange={(event) => updateWidgetField("unit", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-3">
                    <label className="form-label small">Precision</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.precision ?? ""}
                      onChange={(event) =>
                        updateWidgetNumberField("precision", event.target.value)
                      }
                    />
                  </div>
                  <div className="col-12 col-md-3">
                    <label className="form-label small">Min</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.min ?? ""}
                      onChange={(event) => updateWidgetNumberField("min", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-3">
                    <label className="form-label small">Max</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.max ?? ""}
                      onChange={(event) => updateWidgetNumberField("max", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Color</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.color || ""}
                      onChange={(event) => updateWidgetField("color", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Background</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.background || ""}
                      onChange={(event) =>
                        updateWidgetField("background", event.target.value)
                      }
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Font size</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.font_size ?? ""}
                      onChange={(event) => updateWidgetField("font_size", event.target.value)}
                    />
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          <div className="card border-secondary bg-dark-subtle">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faUpload} /> Push Test Data
              </h5>
              <textarea
                className={`form-control form-control-sm font-monospace ${updateJsonState.error ? "is-invalid" : ""}`}
                rows={5}
                value={updateJson}
                onChange={(event) => setUpdateJson(event.target.value)}
              />
              {updateJsonState.error ? (
                <div className="invalid-feedback d-block">{updateJsonState.error}</div>
              ) : (
                <div className="small text-body-secondary mt-1">Valid JSON</div>
              )}
              <button
                className="btn btn-outline-light btn-sm mt-2 me-2"
                onClick={() => {
                  if (updateJsonState.error) {
                    setStatus("fix update JSON before formatting");
                    return;
                  }
                  setUpdateJson(prettyJson(updateJsonState.value));
                }}
              >
                Format JSON
              </button>
              <button
                className="btn btn-outline-light btn-sm mt-2 me-2"
                onClick={() => openJsonModal("update")}
              >
                <FontAwesomeIcon icon={faExpand} className="me-1" /> Large Editor
              </button>
              <button className="btn btn-success btn-sm mt-2" onClick={() => void handlePushUpdate()}>
                Push /api/update
              </button>
            </div>
          </div>

          <div className="card border-secondary bg-dark-subtle mt-3">
            <div className="card-body">
              <h5 className="card-title d-flex align-items-center gap-2">
                <FontAwesomeIcon icon={faBolt} /> Simulation
              </h5>
              <div className="small text-body-secondary mb-2">
                Running: {simStatus.running ? "yes" : "no"} | mode: {simStatus.mode}
              </div>
              <div className="d-flex flex-wrap gap-2 mb-3">
                <button
                  className="btn btn-outline-primary btn-sm"
                  onClick={() => void handleStartRandom()}
                  disabled={Boolean(randomJsonState.error)}
                >
                  <FontAwesomeIcon icon={faCirclePlay} className="me-1" /> Start Random
                </button>
                <button
                  className="btn btn-outline-primary btn-sm"
                  onClick={() => void handleStartReplay()}
                  disabled={Boolean(replayJsonState.error)}
                >
                  <FontAwesomeIcon icon={faCirclePlay} className="me-1" /> Start Replay
                </button>
                <button className="btn btn-outline-danger btn-sm" onClick={() => void handleStopSimulation()}>
                  Stop
                </button>
              </div>
              <label className="form-label small">Random simulation config JSON</label>
              <textarea
                className={`form-control form-control-sm font-monospace ${randomJsonState.error ? "is-invalid" : ""}`}
                rows={7}
                value={randomJson}
                onChange={(event) => setRandomJson(event.target.value)}
              />
              {randomJsonState.error ? (
                <div className="invalid-feedback d-block">{randomJsonState.error}</div>
              ) : (
                <div className="small text-body-secondary mt-1">Valid JSON</div>
              )}
              <button
                className="btn btn-outline-light btn-sm mt-2 mb-3"
                onClick={() => {
                  if (randomJsonState.error) {
                    setStatus("fix random simulation JSON before formatting");
                    return;
                  }
                  setRandomJson(prettyJson(randomJsonState.value));
                }}
              >
                Format JSON
              </button>
              <button
                className="btn btn-outline-light btn-sm mt-2 mb-3 ms-2"
                onClick={() => openJsonModal("random")}
              >
                <FontAwesomeIcon icon={faExpand} className="me-1" /> Large Editor
              </button>
              <label className="form-label small">Replay JSON records</label>
              <textarea
                className={`form-control form-control-sm font-monospace ${replayJsonState.error ? "is-invalid" : ""}`}
                rows={7}
                value={replayJson}
                onChange={(event) => setReplayJson(event.target.value)}
              />
              {replayJsonState.error ? (
                <div className="invalid-feedback d-block">{replayJsonState.error}</div>
              ) : (
                <div className="small text-body-secondary mt-1">Valid JSON</div>
              )}
              <button
                className="btn btn-outline-light btn-sm mt-2"
                onClick={() => {
                  if (replayJsonState.error) {
                    setStatus("fix replay JSON before formatting");
                    return;
                  }
                  setReplayJson(prettyJson(replayJsonState.value));
                }}
              >
                Format JSON
              </button>
              <button
                className="btn btn-outline-light btn-sm mt-2 ms-2"
                onClick={() => openJsonModal("replay")}
              >
                <FontAwesomeIcon icon={faExpand} className="me-1" /> Large Editor
              </button>
            </div>
          </div>

          <div className="alert alert-secondary mt-3 mb-0 py-2 small">{status}</div>
        </div>
      </div>

      {jsonModalTarget ? (
        <div className="json-modal-backdrop" onClick={closeJsonModal}>
          <div className="json-modal-card" onClick={(event) => event.stopPropagation()}>
            <div className="d-flex align-items-center justify-content-between mb-2">
              <h5 className="mb-0 text-capitalize">{jsonModalTarget} JSON Editor</h5>
              <button className="btn btn-sm btn-outline-light" onClick={closeJsonModal}>
                Close
              </button>
            </div>
            <textarea
              className={`form-control form-control-sm font-monospace json-modal-textarea ${modalJsonState.error ? "is-invalid" : ""}`}
              value={jsonModalDraft}
              onChange={(event) => setJsonModalDraft(event.target.value)}
            />
            {modalJsonState.error ? (
              <div className="invalid-feedback d-block">{modalJsonState.error}</div>
            ) : (
              <div className="small text-body-secondary mt-2">Valid JSON</div>
            )}
            <div className="d-flex gap-2 mt-3">
              <button
                className="btn btn-outline-light btn-sm"
                onClick={() => {
                  if (modalJsonState.error) {
                    setStatus("fix modal JSON before formatting");
                    return;
                  }
                  setJsonModalDraft(prettyJson(modalJsonState.value));
                }}
              >
                Format JSON
              </button>
              <button
                className="btn btn-success btn-sm"
                onClick={applyJsonModal}
                disabled={Boolean(modalJsonState.error)}
              >
                Apply to Section
              </button>
              <button className="btn btn-secondary btn-sm" onClick={closeJsonModal}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
