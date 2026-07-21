import { vi } from 'vitest';

// jsdom 没有实现 matchMedia，Lightweight Charts 的 fancy-canvas 依赖它
if (!window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

// jsdom 没有 ResizeObserver
if (!window.ResizeObserver) {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  Object.defineProperty(window, 'ResizeObserver', {
    writable: true,
    value: ResizeObserverMock,
  });
}

// jsdom 实现了 HTMLCanvasElement.prototype.getContext 但会抛 "Not implemented" 错误
// Lightweight Charts 在 destroy 时会调用，返回一个空 stub 让清理流程通过
HTMLCanvasElement.prototype.getContext = function () {
  return {
    canvas: null,
    clearRect: () => {},
    drawImage: () => {},
    getImageData: () => ({ data: new Uint8ClampedArray(0) }),
    putImageData: () => {},
    createImageData: () => ({ data: new Uint8ClampedArray(0) }),
    setTransform: () => {},
    resetTransform: () => {},
    save: () => {},
    restore: () => {},
    translate: () => {},
    scale: () => {},
    rotate: () => {},
    beginPath: () => {},
    closePath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    bezierCurveTo: () => {},
    quadraticCurveTo: () => {},
    arc: () => {},
    arcTo: () => {},
    rect: () => {},
    fill: () => {},
    stroke: () => {},
    clip: () => {},
    fillRect: () => {},
    strokeRect: () => {},
    fillText: () => {},
    strokeText: () => {},
    measureText: () => ({ width: 0 }),
    setLineDash: () => {},
    getLineDash: () => [],
    createLinearGradient: () => ({ addColorStop: () => {} }),
    createRadialGradient: () => ({ addColorStop: () => {} }),
    createPattern: () => ({}),
    drawFocusIfNeeded: () => {},
    isPointInPath: () => false,
    isPointInStroke: () => false,
    getContextAttributes: () => ({}),
  };
} as any;
