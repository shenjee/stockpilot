import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { app, BrowserWindow, ipcMain } from "electron";


const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// 真实快照 fixture：驱动选股 → get_live_snapshot → 图表投影 → TradeDrawer 挂载。
const workbenchFixture = JSON.parse(
  await readFile(
    resolve(appRoot, "contracts", "fixtures", "workbench-flow-v2.json"),
    "utf8",
  ),
);
const fixtureSecurity = Object.freeze({
  symbol: "sh.600000",
  code: "600000",
  market: "sh",
  name: "浦发银行",
  instrument_type: "stock",
});
const targetViewports = Object.freeze([
  { name: "13-inch-default", width: 1440, height: 900 },
  { name: "14-inch-default", width: 1512, height: 982 },
]);
const defaultPreferences = Object.freeze({
  last_symbol: null,
  layout: {
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
    schema_version: "t0_app_v2",
    request_id: request?.request_id ?? "target-viewport",
    accepted: true,
    operation_id: null,
    data,
    error: null,
  };
}

function registerFakeBackend() {
  const unexpectedCommands = [];
  const handler = (_event, command, request) => {
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
    if (command === "search_securities") {
      return appResponse(request, { securities: [fixtureSecurity] });
    }
    if (command === "select_security") {
      return appResponse(request, {
        session_id:
          workbenchFixture.initial_snapshot_event.payload.session.session_id,
        security: fixtureSecurity,
      });
    }
    if (command === "get_live_snapshot") {
      return appResponse(
        request,
        workbenchFixture.initial_snapshot_event.payload,
      );
    }
    if (command === "list_trades") {
      return appResponse(request, null);
    }
    if (command === "list_fee_plans") {
      return appResponse(request, { fee_plans: [] });
    }
    unexpectedCommands.push(command);
    throw new Error(`Unexpected target-viewport backend command: ${command}`);
  };
  ipcMain.handle("bridge:invoke", handler);
  return {
    assertNoUnexpectedCommands() {
      assert.deepEqual(
        unexpectedCommands,
        [],
        "Renderer invoked commands without schema-valid fake responses",
      );
    },
    unregister() {
      ipcMain.removeHandler("bridge:invoke");
    },
  };
}

async function waitForWorkbench(window) {
  await window.webContents.executeJavaScript(`
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 5000;
      const poll = () => {
        const workspace = document.querySelector('[data-testid="workbench"]');
        const layout = document.querySelector('[data-testid="layout-switcher"]');
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

async function clickTestId(window, testId) {
  const selector = `[data-testid="${testId}"]`;
  const clicked = await window.webContents.executeJavaScript(`
    (() => {
      const button = document.querySelector(${JSON.stringify(selector)});
      if (!button) return false;
      button.click();
      return true;
    })()
  `);
  assert.equal(clicked, true, `missing data-testid=${testId}`);
  await settle(window);
}

// 通过搜索框选择证券：输入 → 防抖 → search_securities → 点击建议 → select_security
// → get_live_snapshot → 投影就绪 → isTradableSecurity → TradeDrawer 挂载。
async function selectSecurityViaSearch(window) {
  await window.webContents.executeJavaScript(`
    (() => {
      const input = document.querySelector("#security-search");
      if (!input) throw new Error("missing #security-search");
      input.focus();
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value",
      ).set;
      setter.call(input, "600000");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    })()
  `);
  const clickedOption = await window.webContents.executeJavaScript(`
    new Promise((resolve, reject) => {
      const deadline = Date.now() + 5000;
      const poll = () => {
        const option = document.querySelector('#security-results [role="option"]');
        if (option) {
          option.click();
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          resolve(false);
          return;
        }
        setTimeout(poll, 25);
      };
      poll();
    })
  `);
  assert.equal(
    clickedOption,
    true,
    "security suggestion did not appear after search",
  );
  const drawerMounted = await window.webContents.executeJavaScript(`
    new Promise((resolve) => {
      const deadline = Date.now() + 5000;
      const poll = () => {
        if (document.querySelector('[data-testid="trade-drawer-toggle"]')) {
          resolve(true);
          return;
        }
        if (Date.now() >= deadline) {
          resolve(false);
          return;
        }
        setTimeout(poll, 25);
      };
      poll();
    })
  `);
  assert.equal(drawerMounted, true, "trade drawer did not mount after selection");
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
        Array.from(
          document.querySelectorAll(
            '[data-testid="' + group + '"] [data-testid="chart-panel"]'
          )
        ).map((element) => {
          const value = element.getBoundingClientRect();
          return { top: value.top, height: value.height };
        });
      const workspace = document.querySelector('[data-testid="workbench"]');
      const intraday = document.querySelector('[data-testid="intraday-group"]');
      const ma5 = document.querySelector('[data-testid="layer-ma5"]');
      return {
        viewport: {
          width: document.documentElement.clientWidth,
          height: document.documentElement.clientHeight,
        },
        documentScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        shellScrollWidth:
          document.querySelector('[data-testid="shell"]')?.scrollWidth ?? null,
        shellClientWidth:
          document.querySelector('[data-testid="shell"]')?.clientWidth ?? null,
        workspaceScrollWidth: workspace?.scrollWidth ?? null,
        workspaceClientWidth: workspace?.clientWidth ?? null,
        workspace: rect('[data-testid="workbench"]'),
        toolbar: rect('[data-testid="toolbar"]'),
        fiveMinute: rect('[data-testid="five-minute-group"]'),
        intraday: rect('[data-testid="intraday-group"]'),
        sidebar: rect('[data-testid="market-sidebar"]'),
        replayControls: rect('[data-testid="replay-controls"]'),
        tradeDrawer: rect('[data-testid="trade-drawer"]'),
        tradePanel: rect('[data-testid="trade-drawer-panel"]'),
        fiveMinuteRows: rows("five-minute-group"),
        intradayRows: rows("intraday-group"),
        intradayHidden:
          intraday?.hasAttribute("hidden") ||
          (intraday ? getComputedStyle(intraday).display === "none" : null),
        intradayDisplay: intraday ? getComputedStyle(intraday).display : null,
        showIntraday: workspace?.getAttribute("data-show-intraday") ?? null,
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
  assert.equal(metrics.showIntraday, "true");
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
    Math.abs(metrics.fiveMinute.width / chartsWidth - 0.5) <= 0.01,
    `${target.name}: visible secondary pane must use a 50/50 split`,
  );
  assertChartRowsAligned(metrics, target.name);
}

async function verifyTargetViewport(window, target) {
  window.setContentSize(target.width, target.height);
  await settle(window);

  let metrics = await inspectWorkbench(window);
  assertBaseLayout(metrics, target);

  await clickTestId(window, "layer-ma5");
  await clickTestId(window, "layout-show-intraday");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.showIntraday, "true");
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} 50/50`);
  assert.ok(
    Math.abs(metrics.fiveMinute.width - metrics.intraday.width) <= 1,
    `${target.name}: equal layout must allocate equal chart widths`,
  );
  assertChartRowsAligned(metrics, `${target.name} 50/50`);

  await clickTestId(window, "layout-hide-intraday");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.showIntraday, "false");
  assert.equal(metrics.intradayHidden, true);
  assert.equal(
    metrics.intradayDisplay,
    "none",
    `${target.name}: hidden secondary pane must not occupy grid space`,
  );
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} hidden intraday`);
  assertWithinViewport(metrics.sidebar, metrics.viewport, "market sidebar");
  assert.ok(
    metrics.fiveMinute.right < metrics.sidebar.left,
    `${target.name}: hidden layout must keep the sidebar separate`,
  );
  assert.ok(
    metrics.sidebar.top >= metrics.workspace.top - 0.5 &&
      metrics.sidebar.bottom <= metrics.workspace.bottom + 0.5,
    `${target.name}: sidebar must stay on the first workspace row`,
  );

  await clickTestId(window, "layout-show-intraday");
  await clickTestId(window, "mode-replay");
  metrics = await inspectWorkbench(window);
  assert.equal(metrics.ma5Pressed, "true");
  assertNoHorizontalOverflow(metrics, `${target.name} replay`);
  assertWithinViewport(
    metrics.replayControls,
    metrics.viewport,
    "replay controls",
  );

  await clickTestId(window, "mode-live");
  await selectSecurityViaSearch(window);
  const beforeDrawer = await inspectWorkbench(window);
  await clickTestId(window, "trade-drawer-toggle");
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
    layouts: ["show_intraday", "hide_intraday"],
    replay_controls_visible: true,
    trade_drawer_overlays_charts: true,
  };
}

async function main() {
  const fakeBackend = registerFakeBackend();
  let window;
  try {
    await app.whenReady();
    window = new BrowserWindow({
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
    fakeBackend.assertNoUnexpectedCommands();
    process.stdout.write(
      `${JSON.stringify({ target_viewports: results }, null, 2)}\n`,
    );
  } finally {
    if (window && !window.isDestroyed()) window.destroy();
    fakeBackend.unregister();
  }
}

main()
  .then(() => app.quit())
  .catch((error) => {
    process.stderr.write(`${error?.stack ?? error}\n`);
    app.exit(1);
  });
