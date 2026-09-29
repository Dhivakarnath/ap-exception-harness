import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "@/App.tsx";
import { applyStoredTheme } from "@/lib/theme";
import "./index.css";

// Apply the persisted theme (defaults to dark) before first paint so there's no
// light-to-dark flash on load.
applyStoredTheme();

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
