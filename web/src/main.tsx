import React from "react";
import ReactDOM from "react-dom/client";
import ThemeWrapper from "./ThemeWrapper";
import "./styles/global.css";
import "./styles/motion.css";
import "./styles/visual-finish.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeWrapper />
  </React.StrictMode>,
);
