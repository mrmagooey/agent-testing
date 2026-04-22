import '@testing-library/jest-dom'

// cmdk uses ResizeObserver and scrollIntoView internally; polyfill for jsdom test environment
if (typeof ResizeObserver === 'undefined') {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

if (typeof Element.prototype.scrollIntoView === 'undefined') {
  Element.prototype.scrollIntoView = function () {}
}
