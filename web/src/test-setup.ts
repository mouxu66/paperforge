import "@testing-library/jest-dom/vitest";

// Mock window.getComputedStyle for Ant Design / JSDOM compatibility
const mockGetComputedStyle = vi.fn().mockImplementation(() => {
  const style: Record<string, string> = {};
  return {
    getPropertyValue: vi.fn((prop: string) => {
      if (prop === "scrollbar-color") return "auto";
      return style[prop] ?? "";
    }),
    setProperty: vi.fn((prop: string, value: string) => {
      style[prop] = value;
    }),
    removeProperty: vi.fn((prop: string) => {
      delete style[prop];
    }),
    getPropertyPriority: vi.fn(() => ""),
    item: vi.fn((index: number) => Object.keys(style)[index] ?? ""),
    length: 0,
    parentRule: null,
    cssText: "",
    scrollbarColor: "auto",
    scrollbarWidth: "auto",
    visibility: "visible",
  };
});
try {
  (window as any).getComputedStyle = mockGetComputedStyle;
} catch {
  // If direct assignment fails, fall back to Object.defineProperty
  Object.defineProperty(window, "getComputedStyle", {
    writable: true,
    value: mockGetComputedStyle,
  });
}
(globalThis as any).getComputedStyle = mockGetComputedStyle;

// Mock ResizeObserver (Ant Design Table / responsive components need it)
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, "ResizeObserver", {
  writable: true,
  value: ResizeObserverMock,
});

// Mock react-i18next for all tests
const mockT = (key: string) => key;
const mockI18n = { language: "zh" };
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: mockT,
    i18n: mockI18n,
  }),
  initReactI18next: {
    type: "3rdParty",
    init: () => {},
  },
}));

// Mock scrollIntoView (JSDOM does not implement it; many components call it)
Object.defineProperty(window.Element.prototype, "scrollIntoView", {
  writable: true,
  value: vi.fn(),
});

// Mock HTMLCanvasElement.getContext (pdfjs-dist / charts need a context stub)
Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
  writable: true,
  value: vi.fn(() => ({})),
});

// Mock matchMedia (Ant Design / CSS modules require it)
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});
