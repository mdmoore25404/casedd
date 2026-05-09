import js from "@eslint/js";
import security from "eslint-plugin-security";

export default [
  js.configs.recommended,
  security.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    rules: {
      // Allow intentionally-unused catch variables prefixed with _
      "no-unused-vars": ["error", { caughtErrorsIgnorePattern: "^_" }],
      // detect-object-injection is very noisy in React rendering code where
      // dynamic key access on local controlled objects is routine.
      // Keep as warn so real injection sinks in new code still surface.
      "security/detect-object-injection": "warn",
    },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        console: "readonly",
        fetch: "readonly",
        WebSocket: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        setInterval: "readonly",
        clearInterval: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        AbortController: "readonly",
        Event: "readonly",
        EventSource: "readonly",
        localStorage: "readonly",
        sessionStorage: "readonly",
        navigator: "readonly",
        location: "readonly",
        history: "readonly",
        performance: "readonly",
        requestAnimationFrame: "readonly",
        cancelAnimationFrame: "readonly",
        CustomEvent: "readonly",
        HTMLElement: "readonly",
        Node: "readonly",
        MutationObserver: "readonly",
        ResizeObserver: "readonly",
        IntersectionObserver: "readonly",
        FormData: "readonly",
        Blob: "readonly",
        FileReader: "readonly",
        atob: "readonly",
        btoa: "readonly",
        crypto: "readonly",
        Promise: "readonly",
        Symbol: "readonly",
        Map: "readonly",
        Set: "readonly",
        WeakMap: "readonly",
        WeakSet: "readonly",
        Proxy: "readonly",
        Reflect: "readonly",
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
  },
  {
    // Ignore built output and dependencies
    ignores: ["dist/**", "node_modules/**"],
  },
];
