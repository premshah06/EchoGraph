import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  forceZ,
} from "https://cdn.jsdelivr.net/npm/d3-force-3d@3/+esm";
import * as pdfjsLib from "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.2.67/build/pdf.min.mjs";
import {
  escapeHtml,
  mapErrorToUserMessage,
  parseEventTimestamp,
  renderAnswerTemplate,
} from "./testable_utils.js";

pdfjsLib.GlobalWorkerOptions.workerSrc =
  "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.2.67/build/pdf.worker.min.mjs";

const IS_FILE_ORIGIN = window.location.protocol === "file:";
const API_BASE = IS_FILE_ORIGIN ? "http://localhost:8000" : window.location.origin;
const WS_BASE = API_BASE.replace(/^http/i, "ws");

const NODE_COLORS = {
  raw: 0x2563eb,
  synthesized: 0x06b6d4,
  bridge: 0x7c3aed,
};

const EDGE_COLORS = {
  supports: 0x3b82f6,
  extends: 0x0891b2,
  reframes: 0x9333ea,
  questions: 0xdc2626,
  is_prerequisite_of: 0xea580c,
  bridge: 0x0d9488,
  synthesizes: 0x16a34a,
  related: 0x64748b,
};

const dom = {
  navToggle: document.getElementById("navToggle"),
  navLinks: document.getElementById("navLinks"),
  connectionStatus: document.getElementById("connectionStatus"),
  demoBanner: document.getElementById("demoBanner"),

  fileInput: document.getElementById("fileInput"),
  uploadProgress: document.getElementById("uploadProgress"),
  uploadHint: document.getElementById("uploadHint"),
  documentContent: document.getElementById("documentContent"),
  sourceLabel: document.getElementById("sourceLabel"),
  ingestDocumentBtn: document.getElementById("ingestDocumentBtn"),
  ingestUrlBtn: document.getElementById("ingestUrlBtn"),
  urlInput: document.getElementById("urlInput"),
  ingestState: document.getElementById("ingestState"),

  queryInput: document.getElementById("queryInput"),
  queryBtn: document.getElementById("queryBtn"),
  querySpinner: document.getElementById("querySpinner"),
  answerBox: document.getElementById("answerBox"),

  refreshStatsBtn: document.getElementById("refreshStatsBtn"),
  resetGraphBtn: document.getElementById("resetGraphBtn"),
  nodeCount: document.getElementById("nodeCount"),
  edgeCount: document.getElementById("edgeCount"),
  contradictionCount: document.getElementById("contradictionCount"),
  synthesizedCount: document.getElementById("synthesizedCount"),

  graphContainer: document.getElementById("graph-container"),
  graphTooltip: document.getElementById("graphTooltip"),
  resetViewBtn: document.getElementById("resetViewBtn"),
  physicsToggleBtn: document.getElementById("physicsToggleBtn"),
  zoomInBtn: document.getElementById("zoomInBtn"),
  zoomOutBtn: document.getElementById("zoomOutBtn"),
  nodeSearchInput: document.getElementById("nodeSearchInput"),
  nodeSearchBtn: document.getElementById("nodeSearchBtn"),
  filterRaw: document.getElementById("filterRaw"),
  filterSynthesized: document.getElementById("filterSynthesized"),
  filterBridge: document.getElementById("filterBridge"),

  inspectorBody: document.getElementById("inspectorBody"),

  eventLog: document.getElementById("eventLog"),
  loopBadge: document.getElementById("loopBadge"),

  sideDrawer: document.getElementById("sideDrawer"),
  drawerBackdrop: document.getElementById("drawerBackdrop"),
  closeDrawerBtn: document.getElementById("closeDrawerBtn"),
  drawerTabInspector: document.getElementById("drawerTabInspector"),
  drawerTabEvents: document.getElementById("drawerTabEvents"),
  drawerInspectorSection: document.getElementById("drawerInspectorSection"),
  drawerEventsSection: document.getElementById("drawerEventsSection"),
  openEventsBtn: document.getElementById("openEventsBtn"),
  clearHighlightBtn: document.getElementById("clearHighlightBtn"),
};

const appState = {
  nodesById: new Map(),
  edges: [],
  selectedNodeId: null,
  activeSessionId: null,
  activeIngestion: false,
  physicsPaused: false,
  filters: {
    raw: true,
    synthesized: true,
    bridge: true,
  },
  lastContradiction: null,
};

class SessionSocket {
  constructor({ onEvent, onStatus }) {
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.socket = null;
    this.sessionId = null;
    this.reconnectAttempts = 0;
    this.shouldReconnect = true;
    this.pingIntervalId = null;
  }

  connect(sessionId) {
    if (!sessionId) return;

    if (
      this.socket &&
      this.sessionId === sessionId &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    this.disconnect(false);
    this.sessionId = sessionId;

    const wsUrl = `${WS_BASE}/stream/${sessionId}`;
    this.socket = new WebSocket(wsUrl);
    this.onStatus("connecting");

    this.socket.onopen = () => {
      this.reconnectAttempts = 0;
      this.onStatus("online");
      this.pingIntervalId = window.setInterval(() => {
        if (this.socket?.readyState === WebSocket.OPEN) {
          this.socket.send("ping");
        }
      }, 15000);
    };

    this.socket.onmessage = (messageEvent) => {
      try {
        const event = JSON.parse(messageEvent.data);
        this.onEvent(event);
      } catch (error) {
        addEventLog("error", `Invalid socket payload: ${error.message}`);
      }
    };

    this.socket.onclose = () => {
      this.onStatus("offline");
      this.clearPing();
      if (this.shouldReconnect) this.scheduleReconnect();
    };

    this.socket.onerror = () => {
      this.onStatus("offline");
    };
  }

  scheduleReconnect() {
    if (!this.sessionId) return;
    this.reconnectAttempts += 1;
    const delayMs = Math.min(10000, 500 * 2 ** this.reconnectAttempts);
    window.setTimeout(() => {
      if (!this.shouldReconnect || !this.sessionId) return;
      this.connect(this.sessionId);
    }, delayMs);
  }

  clearPing() {
    if (this.pingIntervalId) {
      window.clearInterval(this.pingIntervalId);
      this.pingIntervalId = null;
    }
  }

  disconnect(keepReconnect = false) {
    this.shouldReconnect = keepReconnect;
    this.clearPing();
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }
}

class KnowledgeGraph3D {
  constructor(container, { onNodeClick, onNodeHover }) {
    this.container = container;
    this.onNodeClick = onNodeClick;
    this.onNodeHover = onNodeHover;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x020817);

    this.camera = new THREE.PerspectiveCamera(
      58,
      Math.max(1, container.clientWidth) / Math.max(1, container.clientHeight),
      0.1,
      3000
    );
    this.camera.position.set(0, 70, 160);
    this.camera.lookAt(0, 0, 0);

    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(
      Math.max(1, container.clientWidth),
      Math.max(1, container.clientHeight)
    );
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.style.display = "block";
    this.renderer.domElement.tabIndex = 0;
    container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.enablePan = true;
    this.controls.enableZoom = true;
    this.controls.screenSpacePanning = true;
    this.controls.zoomSpeed = 0.85;
    this.controls.rotateSpeed = 0.7;
    this.controls.panSpeed = 0.75;
    this.controls.minDistance = 8;
    this.controls.maxDistance = 800;
    this.controls.target.set(0, 0, 0);

    this.nodeGroup = new THREE.Group();
    this.edgeGroup = new THREE.Group();
    this.effectGroup = new THREE.Group();
    this.particleGroup = new THREE.Group();
    this.scene.add(this.edgeGroup, this.nodeGroup, this.particleGroup, this.effectGroup);

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.25));

    const directional = new THREE.DirectionalLight(0xffffff, 1.0);
    directional.position.set(40, 70, 35);
    this.scene.add(directional);

    const fillLight = new THREE.PointLight(0x60a5fa, 0.8, 600);
    fillLight.position.set(-80, -50, 90);
    this.scene.add(fillLight);

    this.pointer = new THREE.Vector2();
    this.raycaster = new THREE.Raycaster();
    this.hoveredNodeId = null;

    this.nodeMeshes = new Map();
    this.edgeObjects = new Map();
    this.forceNodesById = new Map();
    this.forceLinks = [];
    this.edgeParticles = [];
    this.maxParticleCount = 220;
    this._particleReuseCursor = 0;
    this.effectAnimations = [];
    this.cameraTween = null;
    this.useInstancedNodes = false;
    this.instancedNodes = null;
    this.instancedNodeIds = [];
    this.instanceDummy = new THREE.Object3D();
    this.instanceNodeThreshold = 220;

    this.linkForce = forceLink([])
      .id((node) => node.id)
      .distance(18)
      .strength(0.35);

    this.simulation = forceSimulation([], 3)
      .force("charge", forceManyBody().strength(-70))
      .force("center", forceCenter(0, 0, 0))
      .force("x", forceX(0).strength(0.025))
      .force("y", forceY(0).strength(0.025))
      .force("z", forceZ(0).strength(0.025))
      .force("link", this.linkForce)
      .force(
        "collision",
        forceCollide().radius((node) => this.getNodeRadius(node.data) + 1.4)
      )
      .alphaDecay(0.08)
      .alphaMin(0.001)
      .on("tick", () => this.syncPositions());

    this.filters = {
      raw: true,
      synthesized: true,
      bridge: true,
    };

    this.boundHandleResize = this.handleResize.bind(this);

    this.renderer.domElement.addEventListener("pointermove", (event) =>
      this.handlePointerMove(event)
    );
    this.renderer.domElement.addEventListener("click", () => this.handlePointerClick());
    this.renderer.domElement.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        e.stopPropagation();
      },
      { passive: false }
    );

    window.addEventListener("resize", this.boundHandleResize);

    if (typeof ResizeObserver !== "undefined") {
      this.resizeObserver = new ResizeObserver(() => this.handleResize());
      this.resizeObserver.observe(container);
    }

    this.animate();
  }

  getNodeColor(nodeType) {
    return NODE_COLORS[nodeType] ?? NODE_COLORS.raw;
  }

  getNodeRadius(node) {
    const retrievals = Number(node.times_retrieved ?? 0);
    return 2.6 + Math.log2(retrievals + 1) * 1.05;
  }

  getEdgeColor(type) {
    return EDGE_COLORS[type] ?? EDGE_COLORS.related;
  }

  createNodeMesh(node) {
    const radius = this.getNodeRadius(node);
    const geometry = new THREE.SphereGeometry(radius, 18, 18);
    const nodeColor = this.getNodeColor(node.node_type);

    const material = new THREE.MeshStandardMaterial({
      color: nodeColor,
      roughness: 0.22,
      metalness: 0.14,
      emissive: new THREE.Color(nodeColor).multiplyScalar(0.25),
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.userData = {
      id: node.id,
      baseRadius: radius,
      pulseUntil: 0,
      pulseColor: new THREE.Color(0xd64545),
      baseEmissive: new THREE.Color(nodeColor).multiplyScalar(0.25),
      spawnedAt: performance.now(),
    };
    mesh.scale.setScalar(0.01);
    return mesh;
  }

  addOrUpdateNode(node, restartSimulation = true) {
    if (!node?.id) return;

    const existing = this.forceNodesById.get(node.id);

    if (!existing) {
      const seededPosition = () => (Math.random() - 0.5) * 10;

      this.forceNodesById.set(node.id, {
        id: node.id,
        x: seededPosition(),
        y: seededPosition(),
        z: seededPosition(),
        data: node,
      });

      const mesh = this.createNodeMesh(node);
      this.nodeMeshes.set(node.id, mesh);
      this.nodeGroup.add(mesh);
    } else {
      existing.data = node;
      const mesh = this.nodeMeshes.get(node.id);
      if (mesh) {
        const newRadius = this.getNodeRadius(node);
        mesh.userData.baseRadius = newRadius;
        mesh.geometry.dispose();
        mesh.geometry = new THREE.SphereGeometry(newRadius, 18, 18);
        mesh.material.color.setHex(this.getNodeColor(node.node_type));
        mesh.userData.baseEmissive = new THREE.Color(
          this.getNodeColor(node.node_type)
        ).multiplyScalar(0.25);
      }
    }

    if (restartSimulation) this.rebuildSimulation();
  }

  edgeKey(edge) {
    return `${edge.source}|${edge.target}|${edge.type ?? "related"}`;
  }

  addOrUpdateEdge(edge, restartSimulation = true) {
    if (!edge?.source || !edge?.target) return;

    const key = this.edgeKey(edge);
    if (this.edgeObjects.has(key)) return;

    const geometry = new THREE.BufferGeometry();
    geometry.setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);

    const material = new THREE.LineBasicMaterial({
      color: this.getEdgeColor(edge.type),
      transparent: true,
      opacity: 0.7,
    });

    const line = new THREE.Line(geometry, material);
    line.userData = {
      source: edge.source,
      target: edge.target,
      type: edge.type ?? "related",
      filterVisible: true,
    };

    this.edgeObjects.set(key, line);
    this.edgeGroup.add(line);

    if (this.edgeParticles.length < this.maxParticleCount) {
      const particle = new THREE.Mesh(
        new THREE.SphereGeometry(0.32, 8, 8),
        new THREE.MeshBasicMaterial({ color: this.getEdgeColor(edge.type) })
      );
      particle.userData = {
        edgeKey: key,
        progress: Math.random(),
        speed: 0.002 + Math.random() * 0.0025,
      };
      this.edgeParticles.push(particle);
      this.particleGroup.add(particle);
    }

    if (restartSimulation) this.rebuildSimulation();
  }

  disposeInstancedNodes() {
    if (!this.instancedNodes) return;
    this.scene.remove(this.instancedNodes);
    this.instancedNodes.geometry.dispose();
    this.instancedNodes.material.dispose();
    this.instancedNodes = null;
    this.instancedNodeIds = [];
  }

  rebuildInstancedNodes() {
    this.disposeInstancedNodes();

    const entries = Array.from(this.forceNodesById.entries());
    if (!entries.length) return;

    const geometry = new THREE.SphereGeometry(1, 10, 10);
    const material = new THREE.MeshStandardMaterial({
      roughness: 0.28,
      metalness: 0.06,
    });

    this.instancedNodes = new THREE.InstancedMesh(geometry, material, entries.length);
    this.instancedNodes.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    this.instancedNodeIds = entries.map(([id]) => id);

    this.scene.add(this.instancedNodes);
    this.updateInstancedNodeMatrices();
  }

  refreshNodeRenderingMode() {
    const shouldUseInstancing = this.forceNodesById.size >= this.instanceNodeThreshold;
    if (shouldUseInstancing === this.useInstancedNodes) {
      if (shouldUseInstancing) this.updateInstancedNodeMatrices();
      return;
    }

    this.useInstancedNodes = shouldUseInstancing;

    if (this.useInstancedNodes) {
      this.nodeMeshes.forEach((mesh) => (mesh.visible = false));
      this.rebuildInstancedNodes();
    } else {
      this.disposeInstancedNodes();
      this.nodeMeshes.forEach((mesh, nodeId) => {
        const data = this.forceNodesById.get(nodeId)?.data;
        mesh.visible = Boolean(this.filters[data?.node_type || "raw"]);
      });
    }
  }

  updateInstancedNodeMatrices() {
    if (!this.useInstancedNodes || !this.instancedNodes) return;

    for (let i = 0; i < this.instancedNodeIds.length; i += 1) {
      const nodeId = this.instancedNodeIds[i];
      const forceNode = this.forceNodesById.get(nodeId);
      if (!forceNode) continue;

      const nodeData = forceNode.data;
      const isVisible = Boolean(this.filters[nodeData.node_type || "raw"]);
      const radius = isVisible ? this.getNodeRadius(nodeData) : 0.0001;

      this.instanceDummy.position.set(forceNode.x || 0, forceNode.y || 0, forceNode.z || 0);
      this.instanceDummy.scale.setScalar(radius);
      this.instanceDummy.updateMatrix();
      this.instancedNodes.setMatrixAt(i, this.instanceDummy.matrix);
    }

    this.instancedNodes.instanceMatrix.needsUpdate = true;
  }

  clearGraph() {
    this.disposeInstancedNodes();
    this.useInstancedNodes = false;

    this.nodeMeshes.forEach((mesh) => {
      mesh.geometry.dispose();
      mesh.material.dispose();
      this.nodeGroup.remove(mesh);
    });

    this.edgeObjects.forEach((line) => {
      line.geometry.dispose();
      line.material.dispose();
      this.edgeGroup.remove(line);
    });

    this.edgeParticles.forEach((particle) => {
      particle.geometry.dispose();
      particle.material.dispose();
      this.particleGroup.remove(particle);
    });

    this.nodeMeshes.clear();
    this.edgeObjects.clear();
    this.forceNodesById.clear();
    this.forceLinks = [];
    this.edgeParticles = [];

    this.rebuildSimulation();
  }

  setGraph(nodes, edges) {
    this.clearGraph();

    nodes.forEach((node) => this.addOrUpdateNode(node, false));
    edges.forEach((edge) => this.addOrUpdateEdge(edge, false));

    this.rebuildSimulation();
    this.refreshNodeRenderingMode();
  }

  rebuildSimulation() {
    const nodes = Array.from(this.forceNodesById.values());

    this.forceLinks = [];
    this.edgeObjects.forEach((edgeObj) => {
      this.forceLinks.push({
        source: edgeObj.userData.source,
        target: edgeObj.userData.target,
      });
    });

    this.simulation.nodes(nodes);
    this.linkForce.links(this.forceLinks);
    this.simulation.alpha(0.9).alphaTarget(0).restart();
    this.refreshNodeRenderingMode();
  }

  syncPositions() {
    this.forceNodesById.forEach((forceNode, id) => {
      const mesh = this.nodeMeshes.get(id);
      if (!mesh) return;
      mesh.position.set(forceNode.x || 0, forceNode.y || 0, forceNode.z || 0);
    });

    this.edgeObjects.forEach((line) => {
      const source = this.forceNodesById.get(line.userData.source);
      const target = this.forceNodesById.get(line.userData.target);
      if (!source || !target) return;

      const points = [
        new THREE.Vector3(source.x || 0, source.y || 0, source.z || 0),
        new THREE.Vector3(target.x || 0, target.y || 0, target.z || 0),
      ];

      line.geometry.setFromPoints(points);
    });

    if (this.useInstancedNodes) {
      this.updateInstancedNodeMatrices();
    }

    this.updateParticles();
  }

  updateParticles() {
    for (const particle of this.edgeParticles) {
      const edge = this.edgeObjects.get(particle.userData.edgeKey);
      if (!edge || !edge.visible) {
        particle.visible = false;
        continue;
      }

      const source = this.forceNodesById.get(edge.userData.source);
      const target = this.forceNodesById.get(edge.userData.target);
      if (!source || !target) continue;

      particle.visible = true;
      particle.userData.progress += particle.userData.speed;
      if (particle.userData.progress > 1) particle.userData.progress = 0;

      const t = particle.userData.progress;
      particle.position.set(
        (source.x || 0) + ((target.x || 0) - (source.x || 0)) * t,
        (source.y || 0) + ((target.y || 0) - (source.y || 0)) * t,
        (source.z || 0) + ((target.z || 0) - (source.z || 0)) * t
      );
    }
  }

  handlePointerMove(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

    this.raycaster.setFromCamera(this.pointer, this.camera);
    let nextNodeId = null;

    if (this.useInstancedNodes && this.instancedNodes) {
      const hits = this.raycaster.intersectObject(this.instancedNodes);
      if (hits.length) {
        const instanceId = hits[0].instanceId;
        if (typeof instanceId === "number") {
          nextNodeId = this.instancedNodeIds[instanceId] || null;
        }
      }
    }

    if (!nextNodeId) {
      const hits = this.raycaster.intersectObjects(Array.from(this.nodeMeshes.values()));
      if (hits.length) {
        const mesh = hits[0].object;
        if (mesh.visible && mesh.material.opacity > 0.15) {
          nextNodeId = mesh.userData.id;
        }
      }
    }

    if (!nextNodeId) {
      if (this.hoveredNodeId && this.onNodeHover) {
        this.onNodeHover(null, event.clientX, event.clientY);
      }
      this.hoveredNodeId = null;
      this.renderer.domElement.style.cursor = "default";
      return;
    }

    if (this.hoveredNodeId !== nextNodeId) {
      this.hoveredNodeId = nextNodeId;
      const node = this.forceNodesById.get(nextNodeId)?.data ?? null;
      if (this.onNodeHover) {
        this.onNodeHover(node, event.clientX, event.clientY);
      }
    }

    this.renderer.domElement.style.cursor = "pointer";
  }

  handlePointerClick() {
    if (!this.hoveredNodeId) return;

    const selectedNode = this.forceNodesById.get(this.hoveredNodeId)?.data;
    if (!selectedNode) return;

    this.focusNode(this.hoveredNodeId);
    if (this.onNodeClick) this.onNodeClick(selectedNode);
  }

  setFilters(filters) {
    this.filters = { ...this.filters, ...filters };

    this.nodeMeshes.forEach((mesh, nodeId) => {
      const data = this.forceNodesById.get(nodeId)?.data;
      const nodeType = data?.node_type || "raw";
      const visibleByFilter = Boolean(this.filters[nodeType]);
      mesh.visible = this.useInstancedNodes ? false : visibleByFilter;
    });

    this.edgeObjects.forEach((line) => {
      const sourceType =
        this.forceNodesById.get(line.userData.source)?.data?.node_type || "raw";
      const targetType =
        this.forceNodesById.get(line.userData.target)?.data?.node_type || "raw";
      const visibleByFilter = Boolean(this.filters[sourceType] && this.filters[targetType]);
      line.userData.filterVisible = visibleByFilter;
      line.visible = visibleByFilter;
    });

    if (this.useInstancedNodes) this.updateInstancedNodeMatrices();
  }

  pulseNode(nodeId, colorHex = 0xd64545, durationMs = 2200) {
    const mesh = this.nodeMeshes.get(nodeId);
    if (!mesh) return;
    mesh.userData.pulseUntil = performance.now() + durationMs;
    mesh.userData.pulseColor = new THREE.Color(colorHex);
  }

  createArcEffect(sourceId, targetId, mode = "contradiction") {
    const sourceMesh = this.nodeMeshes.get(sourceId);
    const targetMesh = this.nodeMeshes.get(targetId);
    if (!sourceMesh || !targetMesh) return;

    const source = sourceMesh.position.clone();
    const target = targetMesh.position.clone();
    const middle = source.clone().add(target).multiplyScalar(0.5);
    middle.y += 7;

    const curve = new THREE.QuadraticBezierCurve3(source, middle, target);
    const geometry = new THREE.TubeGeometry(curve, 20, 0.16, 8, false);

    const material = new THREE.MeshBasicMaterial({
      color: mode === "resolution" ? 0x22a06b : 0xdc2626,
      transparent: true,
      opacity: 0.85,
    });

    const arcMesh = new THREE.Mesh(geometry, material);
    this.effectGroup.add(arcMesh);

    this.effectAnimations.push({
      mesh: arcMesh,
      mode,
      start: performance.now(),
      duration: 1800,
    });
  }

  highlightContradiction(nodeA, nodeB) {
    this.pulseNode(nodeA, 0xd64545, 2400);
    this.pulseNode(nodeB, 0xd64545, 2400);
    this.createArcEffect(nodeA, nodeB, "contradiction");
  }

  animateResolution(nodeA, nodeB) {
    this.pulseNode(nodeA, 0x22a06b, 2200);
    this.pulseNode(nodeB, 0x22a06b, 2200);
    this.createArcEffect(nodeA, nodeB, "resolution");
  }

  focusNode(nodeId) {
    const mesh = this.nodeMeshes.get(nodeId);
    if (!mesh) return;

    const focusTarget = mesh.position.clone();
    const offset = new THREE.Vector3(14, 10, 16);

    this.cameraTween = {
      fromPosition: this.camera.position.clone(),
      toPosition: focusTarget.clone().add(offset),
      fromTarget: this.controls.target.clone(),
      toTarget: focusTarget,
      start: performance.now(),
      duration: 700,
    };
  }

  searchAndFocus(term) {
    const query = term.trim().toLowerCase();
    if (!query) return null;

    for (const [nodeId, forceNode] of this.forceNodesById.entries()) {
      const concept = (forceNode.data.concept || "").toLowerCase();
      if (concept.includes(query)) {
        this.focusNode(nodeId);
        return forceNode.data;
      }
    }

    return null;
  }

  setQueryHighlight(nodeIds) {
    const sourceSet = new Set(nodeIds);

    this.nodeMeshes.forEach((mesh, nodeId) => {
      if (sourceSet.has(nodeId)) {
        mesh.userData.pulseUntil = performance.now() + 9000;
        mesh.userData.pulseColor = new THREE.Color(0xfbbf24);
        mesh.material.transparent = false;
        mesh.material.opacity = 1;
      } else {
        mesh.material.transparent = true;
        mesh.material.opacity = 0.12;
      }
    });

    this.edgeObjects.forEach((line) => {
      const src = line.userData.source;
      const tgt = line.userData.target;
      line.material.opacity = sourceSet.has(src) && sourceSet.has(tgt) ? 1 : 0.08;
    });
  }

  clearQueryHighlight() {
    this.nodeMeshes.forEach((mesh) => {
      mesh.material.transparent = false;
      mesh.material.opacity = 1;
    });

    this.edgeObjects.forEach((line) => {
      line.material.opacity = 0.7;
    });
  }

  fitToNodes(nodeIds) {
    const positions = nodeIds
      .map((id) => this.forceNodesById.get(id))
      .filter(Boolean)
      .map((n) => new THREE.Vector3(n.x || 0, n.y || 0, n.z || 0));

    if (!positions.length) {
      this.fitToAllNodes();
      return;
    }

    const centroid = new THREE.Vector3();
    positions.forEach((p) => centroid.add(p));
    centroid.divideScalar(positions.length);

    let maxDist = 0;
    positions.forEach((p) => {
      maxDist = Math.max(maxDist, centroid.distanceTo(p));
    });

    const camDist = Math.max(45, maxDist * 2 + 25);
    const dirNorm = new THREE.Vector3(0.25, 0.5, 0.82).normalize();

    this.cameraTween = {
      fromPosition: this.camera.position.clone(),
      toPosition: centroid.clone().add(dirNorm.multiplyScalar(camDist)),
      fromTarget: this.controls.target.clone(),
      toTarget: centroid.clone(),
      start: performance.now(),
      duration: 900,
    };
  }

  setPhysicsPaused(paused) {
    if (paused) {
      this.simulation.stop();
    } else {
      this.simulation.alpha(0.35).alphaTarget(0).restart();
    }
  }

  zoom(factor) {
    this.camera.position.multiplyScalar(factor);
  }

  resetView() {
    this.fitToAllNodes();
  }

  fitToAllNodes() {
    const positions = [];
    this.forceNodesById.forEach((forceNode) => {
      positions.push(
        new THREE.Vector3(forceNode.x || 0, forceNode.y || 0, forceNode.z || 0)
      );
    });

    if (!positions.length) {
      this.cameraTween = {
        fromPosition: this.camera.position.clone(),
        toPosition: new THREE.Vector3(0, 70, 160),
        fromTarget: this.controls.target.clone(),
        toTarget: new THREE.Vector3(0, 0, 0),
        start: performance.now(),
        duration: 900,
      };
      return;
    }

    const centroid = new THREE.Vector3();
    positions.forEach((p) => centroid.add(p));
    centroid.divideScalar(positions.length);

    let maxDist = 0;
    positions.forEach((p) => {
      maxDist = Math.max(maxDist, centroid.distanceTo(p));
    });

    const camDist = Math.max(70, maxDist * 1.8 + 30);
    const dirNorm = new THREE.Vector3(0.2, 0.55, 0.81).normalize();

    this.cameraTween = {
      fromPosition: this.camera.position.clone(),
      toPosition: centroid.clone().add(dirNorm.multiplyScalar(camDist)),
      fromTarget: this.controls.target.clone(),
      toTarget: centroid.clone(),
      start: performance.now(),
      duration: 1000,
    };
  }

  updateNodeEffects(now) {
    this.nodeMeshes.forEach((mesh) => {
      const spawnedAt = mesh.userData.spawnedAt;
      if (spawnedAt) {
        const elapsed = now - spawnedAt;
        const progress = Math.min(1, elapsed / 500);
        const eased = 1 - Math.pow(1 - progress, 3);
        mesh.scale.setScalar(Math.max(0.01, eased));
        if (progress >= 1) mesh.userData.spawnedAt = 0;
      }

      if (mesh.userData.pulseUntil > now) {
        const pulse = (Math.sin(now * 0.016) + 1) / 2;
        mesh.material.emissive
          .copy(mesh.userData.pulseColor)
          .multiplyScalar(0.42 * pulse);
      } else {
        mesh.material.emissive.copy(
          mesh.userData.baseEmissive || new THREE.Color(0x000000)
        );
      }
    });

    this.effectAnimations = this.effectAnimations.filter((animation) => {
      const elapsed = now - animation.start;
      const progress = Math.min(1, elapsed / animation.duration);

      animation.mesh.material.opacity = 0.85 * (1 - progress);
      animation.mesh.scale.setScalar(1 + progress * 0.15);

      if (progress >= 1) {
        animation.mesh.geometry.dispose();
        animation.mesh.material.dispose();
        this.effectGroup.remove(animation.mesh);
        return false;
      }
      return true;
    });

    if (this.cameraTween) {
      const elapsed = now - this.cameraTween.start;
      const progress = Math.min(1, elapsed / this.cameraTween.duration);
      const eased = 1 - Math.pow(1 - progress, 3);

      this.camera.position.lerpVectors(
        this.cameraTween.fromPosition,
        this.cameraTween.toPosition,
        eased
      );

      this.controls.target.lerpVectors(
        this.cameraTween.fromTarget,
        this.cameraTween.toTarget,
        eased
      );

      if (progress >= 1) this.cameraTween = null;
    }
  }

  handleResize() {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);

    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  animate() {
    const step = () => {
      const now = performance.now();
      this.updateNodeEffects(now);
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
}

const graph = new KnowledgeGraph3D(dom.graphContainer, {
  onNodeClick: (node) => {
    appState.selectedNodeId = node.id;
    showInspector(node);
  },
  onNodeHover: (node, x, y) => {
    if (!node) {
      hideTooltip();
      return;
    }
    showTooltip(node, x, y);
  },
});

const sessionSocket = new SessionSocket({
  onEvent: (event) => handleSocketEvent(event),
  onStatus: (status) => updateConnectionStatus(status),
});

function ensureSession() {
  const sessionId =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `session-${Date.now()}-${Math.floor(Math.random() * 100000)}`;

  appState.activeSessionId = sessionId;
  sessionSocket.shouldReconnect = true;
  sessionSocket.connect(sessionId);
  return sessionId;
}

function updateConnectionStatus(status) {
  if (status === "online") {
    dom.connectionStatus.textContent = "Connected";
    dom.connectionStatus.classList.remove("offline");
    dom.connectionStatus.classList.add("online");
  } else if (status === "connecting") {
    dom.connectionStatus.textContent = "Connecting...";
    dom.connectionStatus.classList.remove("online");
    dom.connectionStatus.classList.add("offline");
  } else {
    dom.connectionStatus.textContent = "Disconnected";
    dom.connectionStatus.classList.remove("online");
    dom.connectionStatus.classList.add("offline");
  }
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }

  return response.json();
}

function debounce(callback, waitMs) {
  let timeoutId = null;
  return (...args) => {
    if (timeoutId) clearTimeout(timeoutId);
    timeoutId = setTimeout(() => callback(...args), waitMs);
  };
}

const reloadGraphDebounced = debounce(async () => {
  await loadGraphData();
  await refreshStats();
}, 450);

function showTooltip(node, x, y) {
  dom.graphTooltip.classList.remove("hidden");
  dom.graphTooltip.innerHTML = `
    <strong>${escapeHtml(node.concept)}</strong><br>
    <small>${escapeHtml(node.node_type || "raw")}</small>
  `;

  const rect = dom.graphContainer.getBoundingClientRect();
  const relX = x - rect.left + 14;
  const relY = y - rect.top + 14;
  const clampedX = Math.min(relX, rect.width - 220);
  const clampedY = Math.min(relY, rect.height - 70);

  dom.graphTooltip.style.left = `${clampedX}px`;
  dom.graphTooltip.style.top = `${clampedY}px`;
}

function hideTooltip() {
  dom.graphTooltip.classList.add("hidden");
}

function addEventLog(eventType, message, timestamp = new Date()) {
  const item = document.createElement("article");
  item.className = `log-item ${eventType}`;

  const time = document.createElement("time");
  time.textContent = timestamp.toLocaleTimeString();

  const body = document.createElement("span");
  body.innerHTML = `<strong>${escapeHtml(eventType)}</strong> ${escapeHtml(message)}`;

  item.append(time, body);
  dom.eventLog.appendChild(item);
  dom.eventLog.scrollTop = dom.eventLog.scrollHeight;

  while (dom.eventLog.children.length > 150) {
    dom.eventLog.removeChild(dom.eventLog.firstChild);
  }
}

function renderAnswer(answer, sources = []) {
  dom.answerBox.innerHTML = renderAnswerTemplate(answer, sources);
}

function showUiError(error) {
  const mapped = mapErrorToUserMessage(error);
  addEventLog("error", `${mapped.title}: ${mapped.detail}`);
  alert(`${mapped.title}\n${mapped.detail}\n\nSuggestion: ${mapped.suggestion}`);
}

function openDrawer(tab = "inspector") {
  dom.sideDrawer.classList.add("open");
  dom.drawerBackdrop.classList.remove("hidden");
  switchDrawerTab(tab);
}

function closeDrawer() {
  dom.sideDrawer.classList.remove("open");
  dom.drawerBackdrop.classList.add("hidden");
}

function switchDrawerTab(tab) {
  const isInspector = tab === "inspector";
  dom.drawerTabInspector.classList.toggle("active", isInspector);
  dom.drawerTabEvents.classList.toggle("active", !isInspector);
  dom.drawerInspectorSection.classList.toggle("hidden", !isInspector);
  dom.drawerEventsSection.classList.toggle("hidden", isInspector);
}

function showInspector(node) {
  if (!node) {
    dom.inspectorBody.classList.add("empty");
    dom.inspectorBody.innerHTML = "<p>Select a node to inspect details.</p>";
    closeDrawer();
    return;
  }

  openDrawer("inspector");

  const connections = (node.connected_to || []).map((targetId, index) => {
    const relation = node.relationship_types?.[index] || "related";
    return `<li>${escapeHtml(targetId)} <em>(${escapeHtml(relation)})</em></li>`;
  });

  dom.inspectorBody.classList.remove("empty");
  dom.inspectorBody.innerHTML = `
    <div class="inspector-field"><strong>ID</strong><span>${escapeHtml(node.id)}</span></div>
    <div class="inspector-field"><strong>Concept</strong><span>${escapeHtml(node.concept || "-")}</span></div>
    <div class="inspector-field"><strong>Summary</strong><span>${escapeHtml(node.summary || "-")}</span></div>
    <div class="inspector-field"><strong>Type / Confidence</strong><span>${escapeHtml(node.node_type || "raw")} / ${Number(node.confidence ?? 1).toFixed(2)}</span></div>
    <div class="inspector-field"><strong>Source</strong><span>${escapeHtml(node.source || "-")}</span></div>
    <div class="inspector-field"><strong>Times Retrieved</strong><span>${Number(node.times_retrieved ?? 0)}</span></div>
    <div class="inspector-field">
      <strong>Connections</strong>
      ${connections.length ? `<ul class="connections-list">${connections.join("")}</ul>` : "<span>None</span>"}
    </div>
  `;
}

async function loadGraphData() {
  const payload = await apiRequest("/graph/nodes");
  const nodes = payload.nodes || [];
  const edges = payload.edges || [];

  appState.nodesById = new Map(nodes.map((node) => [node.id, node]));
  appState.edges = edges;

  graph.setGraph(nodes, edges);
  graph.setFilters(appState.filters);

  addEventLog("graph", `Graph loaded (${nodes.length} nodes, ${edges.length} edges)`);

  if (nodes.length > 0) {
    setTimeout(() => graph.fitToAllNodes(), 1200);
  }
}

async function refreshStats() {
  const stats = await apiRequest("/graph/stats");
  dom.nodeCount.textContent = stats.node_count;
  dom.edgeCount.textContent = stats.edge_count;
  dom.contradictionCount.textContent = stats.contradiction_count;
  dom.synthesizedCount.textContent = stats.synthesized_count;
}

async function checkHealth() {
  const health = await apiRequest("/health");
  if (!health.demo_mode) {
    dom.demoBanner.classList.add("hidden");
    return;
  }

  dom.demoBanner.classList.remove("hidden");
  dom.ingestDocumentBtn.disabled = true;
  dom.ingestUrlBtn.disabled = true;
  dom.ingestState.textContent = "Demo Mode";
  addEventLog(
    "system",
    "Demo mode detected: ingestion disabled until OPENAI_API_KEY is configured."
  );
}

function setIngestLoading(active, label = "Processing") {
  const demoLocked = !dom.demoBanner.classList.contains("hidden");
  appState.activeIngestion = active;
  dom.ingestDocumentBtn.disabled = active || demoLocked;
  dom.ingestUrlBtn.disabled = active || demoLocked;
  dom.queryBtn.disabled = active;

  dom.ingestState.textContent = active ? label : demoLocked ? "Demo Mode" : "Idle";
}

function setQueryLoading(active) {
  dom.querySpinner.classList.toggle("hidden", !active);
  dom.queryBtn.disabled = active;
}

function updateUploadProgress(progressPercent, hintText) {
  const progress = Math.max(0, Math.min(100, progressPercent));
  dom.uploadProgress.style.width = `${progress}%`;
  if (hintText) dom.uploadHint.textContent = hintText;
}

function readFileAsArrayBuffer(file, onProgress) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress((event.loaded / event.total) * 100);
      }
    };

    reader.onerror = () => reject(new Error("Failed to read file"));
    reader.onload = () => resolve(reader.result);
    reader.readAsArrayBuffer(file);
  });
}

async function extractPdfText(arrayBuffer) {
  const loadingTask = pdfjsLib.getDocument({ data: arrayBuffer });
  const pdf = await loadingTask.promise;
  const pages = [];

  for (let index = 1; index <= pdf.numPages; index += 1) {
    const page = await pdf.getPage(index);
    const content = await page.getTextContent();
    const text = content.items.map((item) => item.str).join(" ");
    pages.push(text);
  }

  return pages.join("\n");
}

async function buildDocumentPayload() {
  const sourceLabel = dom.sourceLabel.value.trim();
  const textareaContent = dom.documentContent.value.trim();
  const file = dom.fileInput.files?.[0];

  if (!file && !textareaContent) {
    throw new Error("Provide either a file or text content before ingestion.");
  }

  if (!file) {
    return {
      content: textareaContent,
      source_label: sourceLabel || "direct-input.txt",
    };
  }

  const maxFileSizeBytes = 10 * 1024 * 1024;
  if (file.size > maxFileSizeBytes) {
    throw new Error("File is too large. Maximum supported size is 10MB.");
  }

  updateUploadProgress(5, `Reading ${file.name}...`);
  const arrayBuffer = await readFileAsArrayBuffer(file, (progress) => {
    updateUploadProgress(progress * 0.8, `Reading ${file.name}...`);
  });

  let content = "";
  const isPdf =
    file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");

  if (isPdf) {
    updateUploadProgress(86, "Extracting PDF text...");
    content = await extractPdfText(arrayBuffer);
  } else {
    const decoder = new TextDecoder("utf-8");
    content = decoder.decode(arrayBuffer);
  }

  updateUploadProgress(100, `${file.name} ready`);

  return {
    content: content.trim(),
    source_label: sourceLabel || file.name || "uploaded-document",
  };
}

async function ingestDocument() {
  try {
    setIngestLoading(true, "Ingesting");
    const sessionId = ensureSession();
    const payload = await buildDocumentPayload();
    payload.events_session = sessionId;

    addEventLog("ingest", `Submitting ${payload.source_label}`);
    await apiRequest("/ingest/document", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    dom.documentContent.value = "";
    dom.sourceLabel.value = "";
    dom.fileInput.value = "";
    updateUploadProgress(0, "Select a file or paste text below.");

    addEventLog("ingest", "Document ingestion submitted successfully");
  } catch (error) {
    showUiError(error);
  } finally {
    setIngestLoading(false);
  }
}

async function ingestUrl() {
  const url = dom.urlInput.value.trim();
  if (!url) {
    alert("Enter a URL to ingest.");
    return;
  }

  try {
    setIngestLoading(true, "Fetching URL");
    const sessionId = ensureSession();

    await apiRequest("/ingest/url", {
      method: "POST",
      body: JSON.stringify({
        url,
        events_session: sessionId,
      }),
    });

    dom.urlInput.value = "";
    addEventLog("ingest", `URL ingestion submitted: ${url}`);
  } catch (error) {
    showUiError(error);
  } finally {
    setIngestLoading(false);
  }
}

async function queryKnowledge() {
  const query = dom.queryInput.value.trim();
  if (!query) {
    alert("Enter a question before querying.");
    return;
  }

  try {
    setQueryLoading(true);
    graph.clearQueryHighlight();
    const sessionId = ensureSession();

    const result = await apiRequest("/query", {
      method: "POST",
      body: JSON.stringify({
        query,
        events_session: sessionId,
      }),
    });

    renderAnswer(result.answer, result.sources);
    addEventLog("query", `Scholar used ${result.sources.length} sources`);

    for (const event of result.agent_events || []) {
      handleSocketEvent(event);
    }

    await refreshStats();
    await loadGraphData();

    if (result.sources.length > 0) {
      graph.setQueryHighlight(result.sources);
      setTimeout(() => graph.fitToNodes(result.sources), 300);
    }
  } catch (error) {
    showUiError(error);
  } finally {
    setQueryLoading(false);
  }
}

async function resetGraph() {
  const confirmed = window.confirm(
    "Reset the entire knowledge base and clear all graph data?"
  );
  if (!confirmed) return;

  try {
    await apiRequest("/graph/reset", { method: "DELETE" });
    appState.nodesById.clear();
    appState.edges = [];
    graph.clearGraph();
    showInspector(null);
    renderAnswer("Knowledge graph has been reset.", []);
    addEventLog("reset", "Knowledge graph reset");
    await refreshStats();
  } catch (error) {
    showUiError(error);
  }
}

function expandCompactBatch(compactEvent) {
  const schema = compactEvent?.data?.schema || [];
  const rows = compactEvent?.data?.rows || [];
  if (!schema.length || !rows.length) return [];

  return rows.map((row) => {
    const expanded = {};
    for (let index = 0; index < schema.length; index += 1) {
      expanded[schema[index]] = row[index];
    }
    return expanded;
  });
}

function handleSocketEvent(event) {
  if (!event?.event) return;

  if (event.event === "event_batch") {
    const events = event?.data?.events || [];
    for (const childEvent of events) handleSocketEvent(childEvent);
    return;
  }

  if (event.event === "event_batch_compact") {
    const expandedEvents = expandCompactBatch(event);
    for (const childEvent of expandedEvents) handleSocketEvent(childEvent);
    return;
  }

  const eventType = event.event;
  const data = event.data || {};
  const timestamp = parseEventTimestamp(event);

  switch (eventType) {
    case "agent_start":
      dom.ingestState.textContent = data.label || data.agent || "Running";
      addEventLog(eventType, `${data.label || "Agent started"}`, timestamp);
      break;

    case "concept_extracted": {
      const node = {
        id: data.node_id,
        concept: data.concept,
        summary: "Live extracted concept",
        source: "Ingestion stream",
        node_type: data.is_new ? "raw" : "bridge",
        confidence: 1,
        contradiction_resolved: false,
        connected_to: [],
        relationship_types: [],
        times_retrieved: 0,
      };
      appState.nodesById.set(node.id, node);
      graph.addOrUpdateNode(node);
      addEventLog(eventType, `Extracted concept: ${data.concept}`, timestamp);
      break;
    }

    case "connection_found":
      graph.addOrUpdateEdge({
        source: data.from,
        target: data.to,
        type: data.type || "related",
      });
      addEventLog(
        eventType,
        `Connection ${data.from?.slice(0, 8)} -> ${data.to?.slice(0, 8)} (${data.type})`,
        timestamp
      );
      break;

    case "contradiction_found":
      appState.lastContradiction = {
        nodeA: data.node_a,
        nodeB: data.node_b,
      };
      graph.highlightContradiction(data.node_a, data.node_b);
      addEventLog(eventType, data.reason || "Contradiction detected", timestamp);
      break;

    case "resolution_start": {
      const loopValue = Number(data.loop || 0);
      dom.loopBadge.textContent = `Loop: ${loopValue}`;
      addEventLog(eventType, `Resolution loop ${loopValue}`, timestamp);
      break;
    }

    case "resolution_done":
      if (appState.lastContradiction) {
        graph.animateResolution(
          appState.lastContradiction.nodeA,
          appState.lastContradiction.nodeB
        );
      }
      addEventLog(
        eventType,
        `Resolution confidence ${(Number(data.confidence || 0) * 100).toFixed(1)}%`,
        timestamp
      );
      break;

    case "loop_back":
      dom.loopBadge.textContent = `Loop: ${data.loop_count || 0}`;
      addEventLog(eventType, data.reason || "Looping for re-evaluation", timestamp);
      break;

    case "node_stored":
      addEventLog(
        eventType,
        `Stored ${data.type} node ${data.node_id?.slice(0, 8)}`,
        timestamp
      );
      reloadGraphDebounced();
      break;

    case "ingestion_complete":
      dom.ingestState.textContent = "Complete";
      addEventLog(
        eventType,
        `Ingestion complete (${data.new_nodes || 0} nodes, ${data.edges || 0} edges)`,
        timestamp
      );
      reloadGraphDebounced();
      break;

    case "scholar_answer":
      renderAnswer(data.answer || "", data.sources || []);
      addEventLog(eventType, "Scholar produced an answer", timestamp);
      break;

    case "error":
      addEventLog(eventType, data.message || "Agent error", timestamp);
      break;

    case "pong":
      break;

    default:
      addEventLog(eventType, JSON.stringify(data), timestamp);
  }
}

function applyFilters() {
  appState.filters = {
    raw: dom.filterRaw.checked,
    synthesized: dom.filterSynthesized.checked,
    bridge: dom.filterBridge.checked,
  };
  graph.setFilters(appState.filters);
}

function togglePhysics() {
  appState.physicsPaused = !appState.physicsPaused;
  graph.setPhysicsPaused(appState.physicsPaused);
  dom.physicsToggleBtn.textContent = appState.physicsPaused
    ? "Resume Physics"
    : "Pause Physics";
}

function handleCitationClick(event) {
  const button = event.target.closest(".citation-link");
  if (!button) return;

  const nodeId = button.getAttribute("data-node-id");
  if (!nodeId) return;

  const node = appState.nodesById.get(nodeId);
  if (!node) {
    addEventLog("graph", `Node ${nodeId.slice(0, 8)} not loaded yet`);
    return;
  }

  graph.focusNode(nodeId);
  showInspector(node);
}

function bindEvents() {
  dom.navToggle.addEventListener("click", () => {
    const isOpen = dom.navLinks.classList.toggle("open");
    dom.navToggle.setAttribute("aria-expanded", String(isOpen));
  });

  dom.ingestDocumentBtn.addEventListener("click", ingestDocument);
  dom.ingestUrlBtn.addEventListener("click", ingestUrl);
  dom.queryBtn.addEventListener("click", queryKnowledge);
  dom.refreshStatsBtn.addEventListener("click", refreshStats);
  dom.resetGraphBtn.addEventListener("click", resetGraph);

  dom.resetViewBtn.addEventListener("click", () => graph.resetView());
  dom.physicsToggleBtn.addEventListener("click", togglePhysics);
  dom.zoomInBtn.addEventListener("click", () => graph.zoom(0.9));
  dom.zoomOutBtn.addEventListener("click", () => graph.zoom(1.1));

  dom.nodeSearchBtn.addEventListener("click", () => {
    const found = graph.searchAndFocus(dom.nodeSearchInput.value || "");
    if (found) {
      showInspector(found);
      addEventLog("graph", `Focused node: ${found.concept}`);
    } else {
      addEventLog("graph", "No node matched the search term");
    }
  });

  dom.filterRaw.addEventListener("change", applyFilters);
  dom.filterSynthesized.addEventListener("change", applyFilters);
  dom.filterBridge.addEventListener("change", applyFilters);

  dom.closeDrawerBtn.addEventListener("click", () => {
    appState.selectedNodeId = null;
    closeDrawer();
  });

  dom.drawerBackdrop.addEventListener("click", () => {
    appState.selectedNodeId = null;
    closeDrawer();
  });

  dom.drawerTabInspector.addEventListener("click", () => switchDrawerTab("inspector"));
  dom.drawerTabEvents.addEventListener("click", () => switchDrawerTab("events"));

  dom.openEventsBtn.addEventListener("click", () => openDrawer("events"));
  dom.clearHighlightBtn.addEventListener("click", () => {
    graph.clearQueryHighlight();
    graph.fitToAllNodes();
  });

  dom.answerBox.addEventListener("click", handleCitationClick);

  dom.queryInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      queryKnowledge();
    }
  });

  document.addEventListener("keydown", (event) => {
    const tag = event.target?.tagName?.toLowerCase();
    const isInput = tag === "input" || tag === "textarea";
    if (isInput) return;

    if (event.code === "KeyR") {
      event.preventDefault();
      graph.resetView();
    }

    if (event.code === "Space") {
      event.preventDefault();
      togglePhysics();
    }
  });
}

async function initialize() {
  bindEvents();
  updateUploadProgress(0, "Select a file or paste text below.");
  showInspector(null);

  await Promise.all([
    loadGraphData(),
    refreshStats(),
    checkHealth().catch((error) => {
      addEventLog("error", `Health check failed: ${error.message}`);
    }),
  ]);

  ensureSession();
  addEventLog("system", "Application initialized");

  window.setInterval(() => {
    refreshStats().catch((error) => {
      addEventLog("error", `Stats refresh failed: ${error.message}`);
    });
  }, 25000);
}

initialize().catch((error) => {
  addEventLog("error", `Initialization failed: ${error.message}`);
  console.error(error);
});