import "@testing-library/jest-dom/vitest";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal("ResizeObserver", ResizeObserverStub);
vi.stubGlobal("requestAnimationFrame", vi.fn(() => 1));
vi.stubGlobal("cancelAnimationFrame", vi.fn());
