import '@testing-library/jest-dom/vitest'

// jsdom does not implement scrollIntoView; the Factory Floor chat uses it
// to follow new messages.
if (!HTMLElement.prototype.scrollIntoView) {
  HTMLElement.prototype.scrollIntoView = function scrollIntoView() {}
}
