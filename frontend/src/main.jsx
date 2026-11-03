import React, { useState, createContext, useCallback, useContext } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ThemeProvider, ToasterProvider, Toaster } from "@gravity-ui/uikit";
import "@gravity-ui/uikit/styles/fonts.css";
import "@gravity-ui/uikit/styles/styles.css";

import { App } from "./App.jsx";
import { AuthProvider } from "./AuthContext.jsx";
import { getInitialTheme, saveTheme } from "./theme.js";
import "./styles.css";

const toaster = new Toaster();

export const ThemeCtx = createContext(null);
export function useAppTheme() { return useContext(ThemeCtx); }

function Root() {
  const [theme, setTheme] = useState(getInitialTheme);
  const toggle = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "dark" ? "light" : "dark";
      saveTheme(next);
      return next;
    });
  }, []);

  return (
    <ThemeProvider theme={theme} rootClassName="dm-root">
      <ThemeCtx.Provider value={{ theme, toggle }}>
        <ToasterProvider toaster={toaster}>
          <BrowserRouter>
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </ToasterProvider>
      </ThemeCtx.Provider>
    </ThemeProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
