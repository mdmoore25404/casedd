# Multi-Output Backend Architecture

This document describes the pluggable output backend architecture for CASEDD, enabling support for multiple display types (framebuffer, WebSocket, HDMI, etc.) with clean separation of concerns.

## Current Architecture (Tightly Coupled)

```mermaid
graph TB
    Getters["Data Getters<br/>(CPU, Memory, Network, Disk)"]
    Store["Data Store<br/>(shared in-memory KV)"]
    Template["Template Engine<br/>(grid, widgets, rendering)"]
    Renderer["Renderer<br/>(PIL image generation)"]
    
    Framebuffer["Framebuffer Output<br/>(/dev/fb1)"]
    WebSocket["WebSocket Output<br/>(FastAPI broadcast)"]
    HTTP["HTTP Viewer<br/>(/image endpoint)"]
    
    Getters --> Store
    Store --> Template
    Template --> Renderer
    Renderer --> Framebuffer
    Renderer --> WebSocket
    Renderer --> HTTP
    
    style Framebuffer fill:#ff9999
    style WebSocket fill:#ff9999
    style HTTP fill:#ff9999
    classDef problem fill:#ffcccc
    class Framebuffer,WebSocket,HTTP problem
```

**Problem:** Output handling is hardcoded into the render loop. Adding new output types requires modifying core daemon logic. All outputs use the same resolution, template, and refresh rate.

---

## Target Architecture (Pluggable Backends)

```mermaid
graph TB
    Getters["Data Getters<br/>(CPU, Memory, Network, Disk)"]
    Store["Shared Data Store<br/>(single instance, all getters)"]
    Template["Template Registry<br/>(templates per backend)"]
    Renderer["Renderer<br/>(PIL image generation)"]
    
    Registry["Backend Registry<br/>(factory pattern)"]
    
    BaseBackend["OutputBackend Base<br/>(abstract interface)"]
    
    FB["FramebufferBackend<br/>(/dev/fb1)"]
    WS["WebSocketBackend<br/>(FastAPI)"]
    HDMI["HDMIBackend<br/>(/dev/fb0)"]
    Cast["CastBackend<br/>(future)"]
    Custom["CustomBackend<br/>(user extension)"]
    
    Getters --> Store
    Store --> Template
    Template --> Renderer
    Renderer --> Registry
    Registry --> BaseBackend
    BaseBackend --> FB
    BaseBackend --> WS
    BaseBackend --> HDMI
    BaseBackend --> Cast
    BaseBackend --> Custom
    
    FB -.->|async write| Framebuffer["Framebuffer Device"]
    WS -.->|broadcast| WebSocket["Connected Clients"]
    HDMI -.->|async write| HDMIDevice["HDMI Device"]
    
    style BaseBackend fill:#99ff99
    style FB fill:#99ff99
    style WS fill:#99ff99
    style HDMI fill:#ccffcc
    style Cast fill:#ccffcc
    style Custom fill:#ccffcc
    style Store fill:#99ccff
    style Registry fill:#99ccff
    classDef future fill:#e6e6e6
    class Cast,Custom,HDMI future
```

**Benefit:** Outputs are pluggable. Each backend has independent configuration (resolution, refresh rate, template). New backends require only a small concrete class. Shared data collection prevents redundant polling.

---

## Component Interaction Sequence

```mermaid
sequenceDiagram
    participant Daemon as Daemon<br/>(Main)
    participant Getters as Getters
    participant Store as Data Store
    participant Renderer as Renderer
    participant Registry as Backend<br/>Registry
    participant FB as Framebuffer<br/>Backend
    participant WS as WebSocket<br/>Backend
    
    Daemon->>Getters: poll() [periodic]
    Getters->>Store: update(key, value)
    Note over Store: Single update for all<br/>configured backends
    
    Daemon->>Renderer: render(active_template, store)
    Renderer->>Renderer: PIL draw, compose image
    Renderer-->>Daemon: PIL.Image
    
    Daemon->>Registry: get_all_backends()
    Registry-->>Daemon: [FB, WS, ...]
    
    par Parallel broadcast
        Daemon->>FB: output(image, config)
        activate FB
        FB->>FB: resize if needed
        FB-->>FB: asyncio.to_thread mmap write
        deactivate FB
    and
        Daemon->>WS: output(image, config)
        activate WS
        WS->>WS: encode JPEG
        WS->>WS: broadcast to all clients
        deactivate WS
    end
    
    Note over Daemon: Next render cycle<br/>after refresh_rate
```

---

## Backend Interface Specification

```mermaid
classDiagram
    class OutputBackend {
        <<abstract>>
        - name: str
        - width: int
        - height: int
        - refresh_rate: float
        - enabled: bool
        + async output(image: PIL.Image, config: Dict) None
        + async start() None
        + async stop() None
        + is_healthy() bool
        + get_config() Dict
    }
    
    class FramebufferBackend {
        - device_path: str
        - buffer: mmap
        + async output(image: PIL.Image, config: Dict) None
        + async start() None
        + async stop() None
    }
    
    class WebSocketBackend {
        - broadcast_queue: asyncio.Queue
        - clients: Set[WebSocketConnection]
        + async output(image: PIL.Image, config: Dict) None
        + async start() None
        + async stop() None
    }
    
    class HDMIBackend {
        - device_path: str
        - buffer: mmap
        + async output(image: PIL.Image, config: Dict) None
        + async start() None
        + async stop() None
    }
    
    OutputBackend <|-- FramebufferBackend
    OutputBackend <|-- WebSocketBackend
    OutputBackend <|-- HDMIBackend
```

---

## Config Structure (casedd.yaml)

```yaml
# Example: single framebuffer + multiple WebSocket outputs with different configs
outputs:
  framebuffer_usb:
    type: framebuffer
    enabled: true
    device: /dev/fb1
    width: 800
    height: 480
    refresh_rate: 2.0
    template: system_stats

  websocket_primary:
    type: websocket
    enabled: true
    width: 800
    height: 480
    refresh_rate: 2.0
    template: system_stats
    port: 8765

  websocket_detail:
    type: websocket
    enabled: true
    width: 1024
    height: 600
    refresh_rate: 1.0
    template: detailed_metrics
    port: 8766

  hdmi_display:
    type: hdmi
    enabled: false  # Future
    device: /dev/fb0
    width: 1920
    height: 1080
    refresh_rate: 1.0
    template: fullscreen_dashboard
```

---

## Data Store Design (No Redundant Polling)

```mermaid
graph LR
    CPU["CPU Getter"]
    MEM["Memory Getter"]
    NET["Network Getter"]
    DISK["Disk Getter"]
    
    Store["Data Store<br/>(single shared instance)"]
    
    CPU -->|cpu.temperature| Store
    CPU -->|cpu.usage_percent| Store
    MEM -->|memory.used_gb| Store
    MEM -->|memory.percent| Store
    NET -->|net.bytes_recv| Store
    NET -->|net.bytes_sent| Store
    DISK -->|disk.percent| Store
    
    Store -->|template request| Template["Template Renderer"]
    Template -->|reads all keys once| Backend1["Backend 1<br/>(renders)"]
    Template -->|reads all keys once| Backend2["Backend 2<br/>(renders)"]
    Template -->|reads all keys once| Backend3["Backend 3<br/>(renders)"]
    
    style Store fill:#99ccff
    style Template fill:#99ff99
```

**Key:** Getters write to the store once per poll cycle. Template renderer reads the store once and distributes the rendered image to all backends. No duplicate polling or rendering.

---

## Migration Path (MVP Implementation)

### Phase 1: Create Abstraction
- [ ] Create `outputs/base.py` with `OutputBackend` abstract class
- [ ] Define standard interface: `output()`, `start()`, `stop()`, `is_healthy()`
- [ ] Create `outputs/registry.py` with factory pattern

### Phase 2: Refactor Existing Backends
- [ ] Move framebuffer logic → `outputs/framebuffer.py` (implement OutputBackend)
- [ ] Move WebSocket logic → `outputs/websocket.py` (implement OutputBackend)
- [ ] Update HTTP viewer to use registry instead of direct reference

### Phase 3: Update Config & Daemon Loop
- [ ] Extend `config.py` with `outputs` section (list of backend configs)
- [ ] Update `daemon.py` render loop to use registry
- [ ] Ensure all backends read from shared data store (verify no duplicate polling)

### Phase 4: Testing & Documentation
- [ ] Unit tests for registry (instantiation, enable/disable)
- [ ] Integration test for multi-backend output
- [ ] Add mermaid diagrams to docs/
- [ ] Update README with multi-output config example

---

## Notes

- **Backwards Compatibility:** Default behavior (framebuffer + WebSocket on standard ports) preserved when `casedd.yaml` omits `outputs` section.
- **Async Safety:** All backend I/O (mmap writes, WebSocket broadcast) must use `asyncio.to_thread` or native async.
- **Template per Backend:** Each backend can reference a different template if needed (e.g., different layout for 16:9 vs 4:3).
- **Health Monitoring:** Registry tracks backend health. Failed backends can be logged and optionally restarted.
- **Future:** Post-MVP, add hot-reload (add/remove backends without daemon restart), multi-output WebUI collage, deep linking, etc.
