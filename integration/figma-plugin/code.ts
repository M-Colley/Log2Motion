// Initialize and display the plugin's HTML interface with these dimensions.
figma.showUI(__html__, {
  width: 450,
  height: 430,
});

// ----------------------
// --- Export pipline ---
// ----------------------

// Node data shape sent to the UI.
interface UINodeData {
  id: string;
  name: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  parentId?: string;
  children: string[];
  normalizedCenter: [number, number];
  thumbnail?: string;
}

// Screen coordinates require the outermost frame and the node's complete
// transform. Local x/y alone are insufficient for nested groups or frames.
function normalizedCenter(node: SceneNode): [number, number] {
  let screen: FrameNode | null = node.type === "FRAME" ? node : null;
  let ancestor = node.parent;
  while (ancestor && ancestor.type !== "PAGE" && ancestor.type !== "DOCUMENT") {
    if (ancestor.type === "FRAME") screen = ancestor;
    ancestor = ancestor.parent;
  }
  if (!screen || screen.width <= 0 || screen.height <= 0) {
    throw new Error(`Place ${node.name} inside a screen frame before simulating.`);
  }
  const transform = node.absoluteTransform;
  const pageX = transform[0][0] * node.width / 2 + transform[0][1] * node.height / 2 + transform[0][2];
  const pageY = transform[1][0] * node.width / 2 + transform[1][1] * node.height / 2 + transform[1][2];
  const [[a, c, tx], [b, d, ty]] = screen.absoluteTransform;
  const determinant = a * d - b * c;
  if (Math.abs(determinant) < 1e-12) throw new Error("The screen frame has a singular transform.");
  const x = (d * (pageX - tx) - c * (pageY - ty)) / determinant / screen.width;
  const y = (-b * (pageX - tx) + a * (pageY - ty)) / determinant / screen.height;
  if (![x, y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)) {
    throw new Error(`${node.name} is outside its screen frame.`);
  }
  return [y, x];
}

// Render a small PNG preview of a node for display in the UI. Returns undefined if the node can't be exported.
async function exportThumbnail(node: SceneNode): Promise<string | undefined> {
  try {
    const bytes = await node.exportAsync({
      format: "PNG",
      constraint: { type: "SCALE", value: 4 },
    });
    return `data:image/png;base64,${figma.base64Encode(bytes)}`;
  } catch (error) {
    logToUI("Thumbnail export failed for", node.name, error);
    return undefined;
  }
}

//Extract and normalize essential layout and prototype data from a raw Figma SceneNode.
//Handle conditional layout properties safely across different node types.
async function extractNode(
  node: SceneNode,
): Promise<UINodeData> {
  // Identify the parent node ID if it exists and is valid.
  const parent =
    node.parent && "id" in node.parent ? node.parent.id : undefined;

  // Extract the children IDs if the current node supports nesting.
  const children =
    "children" in node ? node.children.map((child) => child.id) : [];

  // Copy the interaction properties if available on the node.
  const thumbnail = await exportThumbnail(node);

  return {
    id: node.id,
    name: node.name,
    type: node.type,
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    parentId: parent,
    children,
    normalizedCenter: normalizedCenter(node),
    thumbnail,
  };
}

//Compile a structured list containing only the nodes currently present
//in the selection path, mapped to their custom sequence index.
async function collectSelectedNodes(): Promise<UINodeData[]> {
  const result: UINodeData[] = [];

  // Iterate strictly in the order of the selection list.
  for (let i = 0; i < clickPath.length; i++) {
    const element = clickPath[i];

    // Fetch the live node object asynchronously from the Figma engine.
    const node = await figma.getNodeByIdAsync(element.id);

    // Process the node only if it still exists and is a valid SceneNode.
    if (node && node.type !== "PAGE" && node.type !== "DOCUMENT") {
      const sceneNode = node as SceneNode;

      result.push(await extractNode(sceneNode));
    }
  }

  return result;
}

// Run the full layout extraction pipeline, bundle the result with basic
// document metadata, and send it to the UI as the final JSON payload.
async function exportDesign() {
  const nodes = await collectSelectedNodes();
  const exportData = {
    fileName: figma.root.name,
    pageName: figma.currentPage.name,
    timestamp: new Date().toISOString(),
    nodes,
  };

  figma.ui.postMessage({
    type: "SEND_JSON",
    payload: exportData,
  });
}

// --------------------------
// --- Selection tracking ---
// --------------------------

// Track the state for the element selection, highlights, and vector arrows.
let selectionMode = false;
let isPluginSelection = false;
let currentHighlight: RectangleNode | null = null;
let highlightedNodeId: string | null = null;
let currentHighlightNodeId: string | null = null;

const clickPath: {
  id: string;
  name: string;
}[] = [];

// Track recently deleted elements to handle undo/redo states.
const recentlyDeletedIds = new Set<string>();
const deletedElementHistory = new Map<string, number[]>();

// Add the newly selected element to the click path, unless its a
// plugin-internal furniture (the highlight box, a connector arrow) or a
// node that was just deleted (Figma re-fires selectionchange during undo).
figma.on("selectionchange", () => {
  if (!selectionMode || isPluginSelection) return;

  const selection = figma.currentPage.selection;

  if (selection.length === 0) return;

  const node = selection[0];

  // Ignore highlighting and vectors.
  if (node.getPluginData("log2motion-owned")) return;

  // Check if the node was recently deleted and prevent adding it to the selected elements list.
  if (recentlyDeletedIds.has(node.id)) {
    recentlyDeletedIds.delete(node.id);
    return;
  }

  const entry = {
    id: node.id,
    name: node.name,
  };

  clickPath.push(entry);

  void cleanupClickPath().then(drawVectors);

  figma.ui.postMessage({
    type: "SELECTION_UPDATED",
    node: entry,
  });
});

// Initialize the element selection with the plugin by preloading document pages and setting up document change listeners.
async function initializePlugin() {
  await figma.loadAllPagesAsync();

  // Pages have been loaded immediately above; the rule is an unconditional reminder.
  // eslint-disable-next-line @figma/figma-plugins/dynamic-page-documentchange-event-advice
  figma.on("documentchange", async (event) => {
    if (!selectionMode) return;

    let needsRedraw = false;

    for (const change of event.documentChanges) {
      // Listen for node created/restored events and handle them accordingly.
      if (change.type === "CREATE") {
        isPluginSelection = true;

        setTimeout(async () => {
          try {
            const node = await figma.getNodeByIdAsync(change.id);
            if (!node) {
              isPluginSelection = false;
              return;
            }

            try {
              // Access can throw for a node removed since the async lookup.
              if (node.removed) return;
            } catch (e) {
              isPluginSelection = false;
              return;
            }

            // Ignore and clean up leftover plugin highlights.
            if (node.getPluginData("log2motion-owned") === "highlight") {
              if (node.id !== currentHighlightNodeId) {
                try {
                  node.remove();
                } catch (e) {}
              }
              isPluginSelection = false;
              return;
            }

            // Immediately remove restored connector arrows recreated by undo.
            if (node.getPluginData("log2motion-owned") === "connector") {
              if (!activeConnectors.some((c) => c.id === node.id)) {
                try {
                  node.remove();
                } catch (e) {}
              }
              isPluginSelection = false;
              return;
            }

            // Restore the nodes position in the selection path if it was previously deleted.
            if (deletedElementHistory.has(change.id)) {
              const savedIndices = deletedElementHistory.get(change.id) || [];
              deletedElementHistory.delete(change.id);
              recentlyDeletedIds.delete(change.id);

              if (
                savedIndices.length > 0 &&
                node.type !== "PAGE" &&
                node.type !== "DOCUMENT"
              ) {
                const entry = {
                  id: node.id,
                  name: node.name,
                };

                // Sort saved indices and insert the node back into its original sequence positions.
                const sortedIndices = [...savedIndices].sort((a, b) => a - b);

                for (const index of sortedIndices) {
                  const targetIndex = Math.min(index, clickPath.length);
                  clickPath.splice(targetIndex, 0, entry);
                }

                // Notify UI about the restored sequence list.
                figma.ui.postMessage({
                  type: "RESTORED_NODES",
                  newPath: clickPath,
                });
              }

              // Redraw vectors based on the restored clickpath.
              setTimeout(async () => {
                try {
                  await cleanupClickPath();
                  await drawVectors();
                } catch (e) {
                  console.warn("Error", e);
                }
              }, 60);
            }
          } catch (err) {
            console.warn("Error for CREATE:", err);
          } finally {
            setTimeout(() => {
              isPluginSelection = false;
            }, 150);
          }
        }, 50);
      }

      // Listen for node delet event and handle it.
      if (change.type === "DELETE") {
        const deletedId = change.id;

        // Clear the active highlight if the highlighted node was deleted.
        if (deletedId === highlightedNodeId) {
          if (currentHighlight) {
            try {
              currentHighlight.remove();
            } catch (e) {}
            currentHighlight = null;
            highlightedNodeId = null;
            currentHighlightNodeId = null;
          }
        }

        recentlyDeletedIds.add(deletedId);

        // Save the position indices of the deleted node for potential undo operations.
        const indicesToSave: number[] = [];
        for (let i = 0; i < clickPath.length; i++) {
          if (clickPath[i].id === deletedId) {
            indicesToSave.push(i);
          }
        }

        if (indicesToSave.length > 0) {
          deletedElementHistory.set(deletedId, indicesToSave);

          // Remove the deleted node from the clickpath in reverse order to preserve index alignment.
          for (let i = clickPath.length - 1; i >= 0; i--) {
            if (clickPath[i].id === deletedId) {
              clickPath.splice(i, 1);
            }
          }

          setTimeout(async () => {
            await cleanupClickPath();
            await drawVectors();
          }, 0);

          figma.ui.postMessage({
            type: "ELEMENT_REMOVED",
            id: deletedId,
          });
        }
      }

      // Redraw vectors if the element has been changed (position/size).
      if (change.type === "PROPERTY_CHANGE") {
        // Check if changed element is part of the clickpath.
        const isElementInPath = clickPath.some((item) => item.id === change.id);

        if (isElementInPath) {
          needsRedraw = true;
        }
      }

      if (needsRedraw) {
        try {
          await cleanupClickPath();
          await drawVectors();
        } catch (e) {
          console.warn("Error updating vector arrows after transform:", e);
        }
      }
    }
  });
}

initializePlugin();

// -------------------------
// --- Connector vectors ---
// -------------------------

// Currently drawn connector arrows, and whether they should be shown at all.
let activeConnectors: SceneNode[] = [];
let showArrowsGlobally = true;
let vectorRenderTask: Promise<void> = Promise.resolve();

// Validates and purges non-existent nodes from the current click path.
async function cleanupClickPath() {
  const missing = new Set<string>();

  for (const entry of [...clickPath]) {
    const node = await figma.getNodeByIdAsync(entry.id);

    if (!node) {
      missing.add(entry.id);
      figma.ui.postMessage({
        type: "ELEMENT_REMOVED",
        id: entry.id,
      });
    }
  }

  // New clicks may arrive while asynchronous node lookups are in flight.
  for (let index = clickPath.length - 1; index >= 0; index--) {
    if (missing.has(clickPath[index].id)) clickPath.splice(index, 1);
  }
}

// Removes all active connector vectors from the current page.
// Draw a native figma vector arrow between each consecutive pair of
// elements in the click path, positioned and sized from their actual
// canvas coordinates.
function drawVectors(): Promise<void> {
  vectorRenderTask = vectorRenderTask.catch((error) => {
    console.warn("Unable to draw click-path arrows:", error);
  }).then(renderVectors);
  return vectorRenderTask;
}

async function renderVectors() {
  // Clear existing vectors from canvas.
  for (const connector of activeConnectors) {
    try {
      connector.remove();
    } catch (e) {}
  }
  activeConnectors = [];

  if (!showArrowsGlobally || !selectionMode) return;

  const path = [...clickPath];
  if (path.length < 2) return;

  // Vector styling.
  const strokeColorRgb: RGB = { r: 1.0, g: 0.678, b: 0.251 };
  const strokeWidth = 3;

  for (let i = 0; i < path.length - 1; i++) {
    const startNode = await figma.getNodeByIdAsync(path[i].id);
    const endNode = await figma.getNodeByIdAsync(path[i + 1].id);
    if (!selectionMode) return;

    // Skip invalid or page-level nodes.
    if (
      !startNode ||
      !endNode ||
      startNode.type === "PAGE" ||
      startNode.type === "DOCUMENT" ||
      endNode.type === "PAGE" ||
      endNode.type === "DOCUMENT"
    ) {
      continue;
    }

    const sNode = startNode as SceneNode;
    const eNode = endNode as SceneNode;

    if (!sNode.absoluteBoundingBox || !eNode.absoluteBoundingBox) continue;

    // Calculate center points for both source and target elements.
    const startX =
      sNode.absoluteBoundingBox.x + sNode.absoluteBoundingBox.width / 2;
    const startY =
      sNode.absoluteBoundingBox.y + sNode.absoluteBoundingBox.height / 2;
    const endX =
      eNode.absoluteBoundingBox.x + eNode.absoluteBoundingBox.width / 2;
    const endY =
      eNode.absoluteBoundingBox.y + eNode.absoluteBoundingBox.height / 2;

    const dx = endX - startX;
    const dy = endY - startY;
    const length = Math.sqrt(dx * dx + dy * dy);

    // Skip rendering for overlapping or extremely close elements.
    if (length < 2) continue;

    // Create a native figma vector node.
    const vector = figma.createVector();
    vector.setPluginData("log2motion-owned", "connector");
    vector.name = `V${i + 1}: ${path[i].name} → ${path[i + 1].name}`;

    // Define path coordinates relative to (0,0) with an arrow cap at the end.
    await vector.setVectorNetworkAsync({
      vertices: [
        { x: 0, y: 0, strokeCap: "NONE" },
        { x: dx, y: dy, strokeCap: "ARROW_EQUILATERAL" },
      ],
      segments: [
        {
          start: 0,
          end: 1,
        },
      ],
    });
    if (!selectionMode) {
      vector.remove();
      return;
    }

    // Position vector accurately on the canvas.
    vector.x = startX;
    vector.y = startY;

    // Apply visual styling.
    vector.strokes = [
      {
        type: "SOLID",
        color: strokeColorRgb,
      },
    ];
    vector.strokeWeight = strokeWidth;

    // Append vector node to the current page view.
    figma.currentPage.appendChild(vector);
    activeConnectors.push(vector);
  }
}

// ---------------------------
// --- UI message handling ---
// ---------------------------

// Forward a debug log to the UI panel console.
function logToUI(...args: unknown[]) {
  figma.ui.postMessage({
    type: "DEBUG_LOG",
    message: args,
  });
}

// Handle every message sent from the UI.
figma.ui.onmessage = async (msg) => {
  logToUI("got a message");
  logToUI(msg.type);

  // Start element selection mode.
  if (msg.type === "START_SELECTION") {
    selectionMode = true;
    if (!msg.isEdit) {
      clickPath.length = 0;
    }
    if (msg.visible !== undefined) {
      showArrowsGlobally = msg.visible;
    }
    await cleanupClickPath();
    await drawVectors();
    return;
  }

  // End element selection mode.
  if (msg.type === "FINISH_SELECTION") {
    selectionMode = false;

    // Remove highlight and vectors when exiting edit mode.
    if (currentHighlight) {
      try {
        currentHighlight.remove();
      } catch (e) {}
      currentHighlight = null;
      highlightedNodeId = null;
      currentHighlightNodeId = null;
    }

    for (const connector of activeConnectors) {
      try {
        connector.remove();
      } catch (e) {}
    }
    activeConnectors = [];

    // Clean up remaining orphan vectors after undo.
    const remainingVectors = figma.currentPage.findAll(
      (node) => node.getPluginData("log2motion-owned") === "connector",
    );
    for (const vec of remainingVectors) {
      try {
        vec.remove();
      } catch (e) {}
    }

    figma.ui.postMessage({
      type: "CLICK_PATH_FINISHED",
      path: clickPath,
    });

    return;
  }

  // Update clickpath when nodes have been reordered via UI.
  if (msg.type === "REORDERED_NODES") {
    clickPath.length = 0;
    clickPath.push(...msg.newPath);
    await cleanupClickPath();
    await drawVectors();
    return;
  }

  // Remove a node from the selection path when deleted from the UI list.
  if (msg.type === "UI_REMOVED_NODE") {
    logToUI("Gelöschter UI-Index:", msg.index, "ID:", msg.id);
    logToUI(
      "Aktueller clickPath:",
      clickPath.map((n) => n.id),
    );

    const targetIndex = msg.index;

    if (
      typeof targetIndex === "number" &&
      targetIndex >= 0 && targetIndex < clickPath.length &&
      clickPath[targetIndex].id === msg.id
    ) {
      clickPath.splice(targetIndex, 1);
    } else {
      const foundIndex = clickPath.findIndex(
        (entry, idx) => entry.id === msg.id && idx === targetIndex,
      );

      if (foundIndex !== -1) {
        clickPath.splice(foundIndex, 1);
      } else {
        const fallbackIndex = clickPath.findIndex(
          (entry) => entry.id === msg.id,
        );
        if (fallbackIndex !== -1) clickPath.splice(fallbackIndex, 1);
      }
    }

    await cleanupClickPath();
    await drawVectors();
    return;
  }

  // Show or hide vectors on the canva.
  if (msg.type === "ARROWS_VISIBILITY") {
    showArrowsGlobally = msg.visible;
    await cleanupClickPath();
    await drawVectors();
    return;
  }

  // Highlight a specific selected element on the canvas.
  if (msg.type === "SELECT_NODE_CARD") {
    // Fetch the target node
    const node = await figma.getNodeByIdAsync(msg.id);

    if (!node) return;

    // Remove any pre-existing visual highlight box before creating a new one.
    if (currentHighlight) {
      currentHighlight.remove();
      currentHighlight = null;
      currentHighlightNodeId = null;
    }

    // Ensure the target node possesses layout and position properties required for highlighting.
    if (
      !("x" in node) ||
      !("y" in node) ||
      !("width" in node) ||
      !("height" in node) ||
      !("absoluteTransform" in node)
    ) {
      return;
    }

    // Lock selection change while positioning highlight.
    isPluginSelection = true;

    // Create a new rectangle node to serve as the visual highlight overlay.
    const highlight = figma.createRectangle();
    highlight.setPluginData("log2motion-owned", "highlight");

    highlight.name = "__PLUGIN_HIGHLIGHT__";
    currentHighlightNodeId = highlight.id;

    const absoluteX = node.absoluteTransform[0][2];
    const absoluteY = node.absoluteTransform[1][2];

    // Set initial absolute position with a 5px padding margin around the target element.
    highlight.x = absoluteX - 5;
    highlight.y = absoluteY - 5;

    // Expand dimensions by 10px total (5px per side) to encapsulate the target element.
    highlight.resize(node.width + 10, node.height + 10);

    // Styling the highlight rectangle
    highlight.fills = []; //fill background transperent
    highlight.strokes = [
      // Apply the stroke color.
      {
        type: "SOLID",
        color: {
          r: 1.0,
          g: 0.678,
          b: 0.251,
        },
      },
    ];
    highlight.strokeWeight = 5;
    highlight.locked = true;

    // Handle nesting: adjust hierarchy and local coordinates if target resides inside a frame/group.
    if (node.parent && node.parent.type !== "PAGE") {
      node.parent.appendChild(highlight);

      highlight.x = node.x - 5;
      highlight.y = node.y - 5;
    } else {
      figma.currentPage.appendChild(highlight);
    }

    // Store references to the currently active highlight rectangle.
    currentHighlight = highlight;
    highlightedNodeId = node.id;

    figma.currentPage.selection = [node as SceneNode];

    // Center and adjust the viewport zoom to focus on the selected node.
    figma.viewport.scrollAndZoomIntoView([node as SceneNode]);

    // Release the selection listener lock after a brief timeout to restore normal tracking.
    setTimeout(() => {
      isPluginSelection = false;
    }, 50);

    return;
  }

  // Trigger the design structure export process.
  if (msg.type === "START") {
    try {
      await exportDesign();
    } catch (error) {
      figma.ui.postMessage({ type: "EXPORT_ERROR", error: String(error) });
    }
    return;
  }

  // Resize the plugin window to the given dimensions.
  if (msg.type === "RESIZE_WINDOW") {
    figma.ui.resize(msg.width, msg.height);
    return;
  }

  // When the client gives up reconnecting to the server - close the plugin.
  if (msg.type === "CLOSE_PLUGIN") {
    figma.closePlugin(msg.message || "Simulation_Log2Motion was closed.");
    return;
  }
};

// ----------------
// --- Shutdown ---
// ----------------

// Clean up all plugin highlight frames and vector connectors on plugin shutdown.
function cleanupConnectorsAndHighlights() {
  selectionMode = false;
  // Remove highlight.
  if (currentHighlight && !currentHighlight.removed) {
    try {
      currentHighlight.remove();
    } catch (e) {
      console.warn("Fehler beim Entfernen von currentHighlight:", e);
    }
  }
  currentHighlight = null;

  // Remove vectors.
  if (Array.isArray(activeConnectors)) {
    for (const connector of activeConnectors) {
      try {
        if (connector && !connector.removed) {
          connector.remove();
        }
      } catch (e) {
        console.warn("Fehler beim Entfernen eines Connectors:", e);
      }
    }
    activeConnectors = [];

    // Clean up remaining orphan vectors after undo.
    const remainingVectors = figma.currentPage.findAll(
      (node) => node.getPluginData("log2motion-owned") === "connector",
    );
    for (const vec of remainingVectors) {
      try {
        vec.remove();
      } catch (e) {}
    }
  }
}

// Register cleanup listener on plugin close.
figma.on("close", () => {
  cleanupConnectorsAndHighlights();
});
