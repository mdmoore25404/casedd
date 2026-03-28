import { useEffect, useMemo, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBolt,
  faCirclePlay,
  faFlask,
  faList,
  faToggleOn,
  faUpload,
  faWandMagicSparkles,
} from "@fortawesome/free-solid-svg-icons";

import {
  fetchPanels,
  getSimulationStatus,
  getTestMode,
  postDataUpdate,
  postTemplateOverride,
  setTestMode,
  startRandomSimulation,
  startReplaySimulation,
  stopSimulation,
} from "./api";

function usePolling(callback, intervalMs) {
  useEffect(() => {
    let mounted = true;
    const tick = async () => {
      if (!mounted) {
        return;
      }
      try {
        await callback();
      } catch (_err) {
        // polling errors are surfaced in UI actions
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
  }, [callback, intervalMs]);
}

export function App() {
  const [panelsData, setPanelsData] = useState({ panels: [], default_panel: "", test_mode: false });
  const [status, setStatus] = useState("ready");
  const [selectedPanel, setSelectedPanel] = useState("");
  const [templateName, setTemplateName] = useState("");
  const [updateJson, setUpdateJson] = useState('{"fans.cpu.max_rpm": 1200}');
  const [testModeEnabled, setTestModeEnabled] = useState(false);
  const [simStatus, setSimStatus] = useState({ running: false, mode: "idle" });
  const [replayJson, setReplayJson] = useState('[{"at_ms":0,"update":{"fans.cpu.max_rpm":900}},{"at_ms":1000,"update":{"fans.cpu.max_rpm":1400}}]');

  const refreshPanels = async () => {
    const payload = await fetchPanels();
    setPanelsData(payload);
    if (!selectedPanel) {
      setSelectedPanel(payload.default_panel || "");
    }
  };

  const refreshStatus = async () => {
    const [tm, sim] = await Promise.all([getTestMode(), getSimulationStatus()]);
    setTestModeEnabled(Boolean(tm.enabled));
    setSimStatus(sim);
  };

  usePolling(refreshPanels, 2000);
  usePolling(refreshStatus, 1500);

  const panels = panelsData.panels || [];
  const selectedPanelData = useMemo(
    () => panels.find((item) => item.name === selectedPanel) || null,
    [panels, selectedPanel],
  );

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
