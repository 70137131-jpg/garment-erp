import { ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/** Shared with the pre-paint bootstrap in index.html — keep both in step. */
export const THEME_STORAGE_KEY = "erp:theme";

const LIGHT_QUERY = "(prefers-color-scheme: light)";

function prefersLight(): boolean {
  return typeof window !== "undefined" && window.matchMedia(LIGHT_QUERY).matches;
}

export function resolveTheme(choice: ThemeChoice): ResolvedTheme {
  if (choice !== "system") return choice;
  return prefersLight() ? "light" : "dark";
}

function readStoredChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Storage can throw in private browsing or when cookies are blocked.
    // Falling back to "system" is the right default either way.
  }
  return "system";
}

function applyTheme(resolved: ResolvedTheme) {
  document.documentElement.dataset.theme = resolved;
  // Keeps the mobile browser chrome matching the canvas.
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", resolved === "light" ? "#ffffff" : "#000000");
}

interface ThemeState {
  /** What the user picked, including the "follow my OS" option. */
  choice: ThemeChoice;
  /** What that resolves to right now — always a concrete theme. */
  resolved: ResolvedTheme;
  setChoice: (choice: ThemeChoice) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(readStoredChoice);
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme(readStoredChoice()));

  // Apply on mount and whenever the choice changes. When following the system,
  // also track live changes — the OS can flip at sunset while the tab is open.
  useEffect(() => {
    const sync = () => {
      const next = resolveTheme(choice);
      setResolved(next);
      applyTheme(next);
    };
    sync();
    if (choice !== "system") return;
    const query = window.matchMedia(LIGHT_QUERY);
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [choice]);

  const setChoice = useCallback((next: ThemeChoice) => {
    setChoiceState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Preference is still applied for this session; it just will not persist.
    }
  }, []);

  const value = useMemo(() => ({ choice, resolved, setChoice }), [choice, resolved, setChoice]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside a ThemeProvider");
  return context;
}
