import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBolt,
  faCirclePlay,
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
  fetchTemplate,
  fetchTemplates,
  fetchPanels,
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

export function App() {
  const [panelsData, setPanelsData] = useState({ panels: [], default_panel: "", test_mode: false });
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState("");
  const [templateDoc, setTemplateDoc] = useState(null);
  const [selectedWidget, setSelectedWidget] = useState("");
  const [templateDirty, setTemplateDirty] = useState(false);
  const [status, setStatus] = useState("ready");
  const [selectedPanel, setSelectedPanel] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [previewNonce, setPreviewNonce] = useState(() => Date.now());
  const [updateJson, setUpdateJson] = useState('{"fans.cpu.max_rpm": 1200}');
  const [testModeEnabled, setTestModeEnabled] = useState(false);
  const [simStatus, setSimStatus] = useState({ running: false, mode: "idle" });
  const [replayJson, setReplayJson] = useState('[{"at_ms":0,"update":{"fans.cpu.max_rpm":900}},{"at_ms":1000,"update":{"fans.cpu.max_rpm":1400}}]');

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
    if (!selectedTemplate && selectedPanelData.current_template) {
      setSelectedTemplate(String(selectedPanelData.current_template));
    }
    if (!templateName && selectedPanelData.current_template) {
      setTemplateName(String(selectedPanelData.current_template));
    }
  }, [selectedPanelData, selectedTemplate, templateName]);

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

  async function handleLoadTemplate() {
    if (!selectedTemplate) {
      setStatus("select a template first");
      return;
    }
    const payload = await fetchTemplate(selectedTemplate);
    setTemplateDoc(payload.template);
    setTemplateDirty(false);
    setStatus(`loaded template ${selectedTemplate}`);
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
    await postTemplateOverride(selectedPanel, templateName || null);
    setStatus(templateName ? `forced ${templateName} on ${selectedPanel}` : `cleared override on ${selectedPanel}`);
    await refreshPanels();
  }

  async function handlePushUpdate() {
    const parsed = JSON.parse(updateJson);
    await postDataUpdate(parsed);
    setStatus("update pushed");
  }

  async function handleToggleTestMode() {
    const next = !testModeEnabled;
    await setTestMode(next);
    setTestModeEnabled(next);
    setStatus(next ? "test mode enabled (getters disabled)" : "test mode disabled");
  }

  async function handleStartRandom() {
    await startRandomSimulation({
      interval: 1.0,
      fields: [
        { key: "fans.cpu.max_rpm", min: 700, max: 1800, step: 80 },
        { key: "fans.system.max_rpm", min: 500, max: 1500, step: 50 },
        { key: "cpu.percent", min: 0, max: 100, step: 8 },
      ],
    });
    setStatus("random simulation started");
    await refreshStatus();
  }

  async function handleStartReplay() {
    const records = JSON.parse(replayJson);
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
              <input
                className="form-control form-control-sm mb-2"
                placeholder="template name or blank for auto"
                value={templateName}
                onChange={(event) => setTemplateName(event.target.value)}
              />
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
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.type || ""}
                      onChange={(event) => updateWidgetField("type", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-4">
                    <label className="form-label small">Label</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.label || ""}
                      onChange={(event) => updateWidgetField("label", event.target.value)}
                    />
                  </div>
                  <div className="col-12 col-md-6">
                    <label className="form-label small">Source</label>
                    <input
                      className="form-control form-control-sm"
                      value={selectedWidgetConfig?.source || ""}
                      onChange={(event) => updateWidgetField("source", event.target.value)}
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
                className="form-control form-control-sm font-monospace"
                rows={5}
                value={updateJson}
                onChange={(event) => setUpdateJson(event.target.value)}
              />
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
                <button className="btn btn-outline-primary btn-sm" onClick={() => void handleStartRandom()}>
                  <FontAwesomeIcon icon={faCirclePlay} className="me-1" /> Start Random
                </button>
                <button className="btn btn-outline-primary btn-sm" onClick={() => void handleStartReplay()}>
                  <FontAwesomeIcon icon={faCirclePlay} className="me-1" /> Start Replay
                </button>
                <button className="btn btn-outline-danger btn-sm" onClick={() => void handleStopSimulation()}>
                  Stop
                </button>
              </div>
              <label className="form-label small">Replay JSON records</label>
              <textarea
                className="form-control form-control-sm font-monospace"
                rows={5}
                value={replayJson}
                onChange={(event) => setReplayJson(event.target.value)}
              />
            </div>
          </div>

          <div className="alert alert-secondary mt-3 mb-0 py-2 small">{status}</div>
        </div>
      </div>
    </div>
  );
}
