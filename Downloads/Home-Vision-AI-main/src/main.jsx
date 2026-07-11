import { Buffer } from "buffer";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

if (typeof window !== "undefined" && !window.Buffer) {
  window.Buffer = Buffer;
}

console.log("App loaded - PDF report updated");

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
