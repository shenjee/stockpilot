import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, ipcMain } from "electron";


const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const targetViewports = Object.freeze([
  { name: "13-inch-default", width: 1440, height: 900 },
  { name: "14-inch-default", width: 1512, height: 982 },
]);
const defaultPreferences = Object.freeze({
  last_symbol: null,
  layout: {
    chart_split: "64_36",
    show_intraday: true,
  },
  layers: {
    ma5: false,
    ma10: false,
    ma20: false,
    ma30: false,
    ma60: false,
    strokes: true,
    pivot_zones: true,
  },
});

function appResponse(request, data) {
  return {
    schema_version: "t0_app_v1",
    request_id: request?.request_id ?? "target-viewport",
    accepted: true,
    operation_id: null,
    data,
    error: null,
  };
}

function registerFakeBackend() {
  ipcMain.handle("bridge:invoke", (_event, command, request) => {
    if (command === "get_service_status") {
      return {
        state: "ready",
        service_generation: 1,
        message: "目标视口验收服务已就绪",
      };
    }
    if (command === "get_preferences") {
      return appResponse(request, {
        snapshot: {
          preference_revision: 0,
          preferences: defaultPreferences,
        },
      });
    }
    if (command === "save_preferences") {
      return appResponse(request, {
        snapshot: {
          preference_revision: 1,
          preferences:
            request?.payload?.preferences ?? defaultPreferences,
        },
      });
    }
    return appResponse(request, null);
  });
}

async function waitForWorkbench(window) {
  await window.webContents.executeJavaScript(`
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 5000;
      const poll = () => {
        const workspace = document.querySelector(".workspace");
        const layout = document.querySelector(".layout-switcher");
        const bridgeReady =
          typeof window.stockpilot?.getServiceStatus === "function";
        if (workspace && layout && bridgeReady) {
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          reject(new Error("Workbench did not render before timeout"));
          return;
        }
        setTimeout(poll, 25);
      };
      poll();
    })
  `);
}

async function settle(window) {
  await window.webContents.executeJavaScript(
    "new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
  );
}

async function clickButton(window, scope, label) {
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const root = document.querySelector(${JSON.stringify(scope)});
      const button = Array.from(root?.querySelectorAll("button") ?? [])
        .find((candidate) => candidate.textContent?.trim() === ${JSON.stringify(label)});
      if (!button) return false;
      button.click();
      return true;
    })()
  `);
  assert.equal(clicked, true, `missing ${label} button in ${scope}`);
  await settle(window);
}

async function inspectWorkbench(window) {
  return window.webContents.executeJavaScript(`
    (() => {
      const rect = (selector) => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const value = element.getBoundingClientRect();
        return {
          left: value.left,
          right: value.right,
          top: value.top,
          bottom: value.bottom,
          width: value.width,
          height: value.height,
        };
      };
      const rows = (group) =>
        Array.from(document.querySelectorAll(group + " .chart-panel")).map((element) => {
          const value = element.getBoundingClientRect();
          return { top: value.top, height: value.height };
        });
      const workspace = document.querySelector(".workspace");
      const intraday = document.querySelector(".intraday-group");
      const selectedLayout = Array.from(
        document.querySelectorAll(".layout-switcher button"),
      ).find((button) => button.getAttribute("aria-pressed") === "true");
      const ma5 = document.querySelector('.layer-switcher button[data-layer="ma5"]');
      return {
        viewport: {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
        documentScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        shellScrollWidth: document.querySelector(".shell")?.scrollWidth ?? null,
        shellClientWidth: document.querySelector(".shell")?.clientWidth ?? null,
        workspaceScrollWidth: workspace?.scrollWidth ?? null,
        workspaceClientWidth: workspace?.clientWidth ?? null,
        workspace: rect(".workspace"),
        toolbar: rect(".toolbar"),
        fiveMinute: rect(".five-minute-group"),
        intraday: rect(".intraday-group"),
        sidebar: rect(".market-sidebar"),
        replayControls: rect(".replay-controls"),
        tradeDrawer: rect(".trade-drawer"),
        tradePanel: rect(".trade-drawer-panel"),
        fiveMinuteRows: rows(".five-minute-group"),
        intradayRows: rows(".intraday-group"),
        intradayHidden:
          intraday?.hasAttribute("hidden") ||
          (intraday ? getComputedStyle(intraday).display === "none" : null),
        layout: selectedLayout?.textContent?.trim() ?? null,
        ma5Pressed: ma5?.getAttribute("aria-pressed") ?? null,
      };
    })()
  `);
}

function assertWithinViewport(rect, viewport, label) {
  assert.ok(rect, `${label} must be rendered`);
  assert.ok(rect.left >= -0.5, `${label} crosses the left viewport edge`);
  assert.ok(
    rect.right <= viewport.width + 0.5,
    `${label} crosses the right viewport edge`,
  );
  assert.ok(rect.top >= -0.5, `${label} crosses the top viewport edge`);
  assert.ok(
    rect.bottom <= viewport.height + 0.5,
    `${label} crosses the bottom viewport edge`,
  );
}

function assertNoHorizontalOverflow(metrics, label) {
  assert.ok(
    metrics.documentScrollWidth <= metrics.viewport.width + 1,
    `${label}: document requires horizontal scrolling`,
  );
  assert.ok(
    metrics.bodyScrollWidth <= metrics.viewport.width + 1,
    `${label}: body requires horizontal scrolling`,
  );
  assert.ok(
    metrics.shellScrollWidth <= metrics.shellClientWidth + 1,
    `${label}: shell requires horizontal scrolling`,
  );
  assert.ok(
    metrics.workspaceScrollWidth <= metrics.workspaceClientWidth + 1,
    `${label}: workspace requires horizontal scrolling`,
  );
}

function assertChartRowsAligned(metrics, label) {
  assert.equal(metrics.fiveMinuteRows.length, 3, `${label}: 5m rows`);
  assert.equal(metrics.intradayRows.length, 3, `${label}: intraday rows`);
  for (let index = 0; index < 3; index += 1) {
    const fiveMinute = metrics.fiveMinuteRows[index];
    const intraday = metrics.intradayRows[index];
    assert.ok(
      Math.abs(fiveMinute.top - intraday.top) <= 1,
      `${label}: row ${index + 1} starts are not aligned`,
    );
    assert.ok(
      Math.abs(fiveMinute.height - intraday.height) <= 1,
      `${label}: row ${index + 1} heights are not aligned`,
    );
  }
}

function assertBaseLayout(metrics, target) {
  assert.equal(metrics.viewport.width, target.width);
  assert.equal(metrics.viewport.height, target.height);
  assert.equal(metrics.layout, "64 / 36");
  assert.equal(metrics.intradayHidden, false);
  assertNoHorizontalOverflow(metrics, target.name);
  assertWithinViewport(metrics.toolbar, metrics.viewport, "toolbar");
  assertWithinViewport(metrics.workspace, metrics.viewport, "workspace");
  assertWithinViewport(metrics.sidebar, metrics.viewport, "market sidebar");
  assert.ok(
    Math.abs(metrics.sidebar.width - 280) <= 1,
    `${target.name}: sidebar width must remain 280px`,
  );
  const chartsWidth = metrics.fiveMinute.width + metrics.intraday.width;
  assert.ok(
    Math.abs(metrics.fiveMinute.width / chartsWidth - 0.64) <= 0.01,
    `${target.name}: main-priority split is not 64/36`,
  );
  assertChartRowsAligned(metrics, target.name);
}

async function verifyTargetViewport(window, target) {
  window.setContentSize(target.width, target.height);
  await settle(window);

  let metrics = await inspectWorkbench(window);
  assertBaseLayout(metrics, target);

  await clickButton(window, ".layer-switcher", "MA5");
  await clickButton(window, ".layout-switcher", "50 / 50");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.layout, "50 / 50");
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} 50/50`);
  assert.ok(
    Math.abs(metrics.fiveMinute.width - metrics.intraday.width) <= 1,
    `${target.name}: equal layout must allocate equal chart widths`,
  );
  assertChartRowsAligned(metrics, `${target.name} 50/50`);

  await clickButton(window, ".layout-switcher", "隐藏分时");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.layout, "隐藏分时");
  assert.equal(metrics.intradayHidden, true);
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} hidden intraday`);
  assertWithinViewport(metrics.sidebar, metrics.viewport, "market sidebar");
  assert.ok(
    metrics.fiveMinute.right < metrics.sidebar.left,
    `${target.name}: hidden layout must keep the sidebar separate`,
  );

  await clickButton(window, ".layout-switcher", "64 / 36");
  await clickButton(window, ".mode-switcher", "回放");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.layout, "64 / 36");
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} replay`);
  assertWithinViewport(
    metrics.replayControls,
    metrics.viewport,
    "replay controls",
  );

  await clickButton(window, ".mode-switcher", "实盘");
  const beforeDrawer = await inspectWorkbench(window);
  const expanded = await window.webContents.executeJavaScript(`
    (() => {
      const button = document.querySelector('button[aria-label="展开成交栏"]');
      if (!button) return false;
      button.click();
      return true;
    })()
  `);
  assert.equal(expanded, true, "missing trade drawer toggle");
  await settle(window);
  metrics = await inspectWorkbench(window);
  assertNoHorizontalOverflow(metrics, `${target.name} trade drawer`);
  assertWithinViewport(metrics.tradeDrawer, metrics.viewport, "trade drawer");
  assertWithinViewport(metrics.tradePanel, metrics.viewport, "trade drawer panel");
  assert.ok(
    Math.abs(metrics.workspace.height - beforeDrawer.workspace.height) <= 1,
    `${target.name}: expanded trade drawer must not resize charts`,
  );

  return {
    viewport: target,
    sidebar_width: metrics.sidebar.width,
    workspace_height: metrics.workspace.height,
    horizontal_overflow: false,
    layouts: ["64 / 36", "50 / 50", "隐藏分时"],
    replay_controls_visible: true,
    trade_drawer_overlays_charts: true,
  };
}

async function main() {
  registerFakeBackend();
  await app.whenReady();
  const window = new BrowserWindow({
    show: false,
    useContentSize: true,
    width: targetViewports[0].width,
    height: targetViewports[0].height,
    webPreferences: {
      preload: resolve(appRoot, "electron", "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  window.webContents.on("console-message", (details) => {
    if (details.level === "warning" || details.level === "error") {
      process.stderr.write(`[renderer] ${details.message}\n`);
    }
  });
  await window.loadFile(resolve(appRoot, "dist", "index.html"));
  await waitForWorkbench(window);

  const results = [];
  for (const target of targetViewports) {
    results.push(await verifyTargetViewport(window, target));
    // Restore a clean state before resizing to the next target.
    await window.reload();
    await waitForWorkbench(window);
  }
  process.stdout.write(`${JSON.stringify({ target_viewports: results }, null, 2)}\n`);
  window.destroy();
}

main()
  .then(() => app.quit())
  .catch((error) => {
    process.stderr.write(`${error?.stack ?? error}\n`);
    app.exit(1);
  });
