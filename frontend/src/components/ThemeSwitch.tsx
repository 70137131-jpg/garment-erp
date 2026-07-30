import { useRef } from "react";
import { ThemeChoice, useTheme } from "../theme/ThemeProvider";

const OPTIONS: { value: ThemeChoice; label: string; glyph: string }[] = [
  { value: "light", label: "Light", glyph: "○" },
  { value: "dark", label: "Dark", glyph: "●" },
  { value: "system", label: "System", glyph: "◐" },
];

/**
 * Segmented light / dark / system control.
 *
 * Rendered as a radiogroup rather than three buttons: these are three states of
 * one setting, so a single tab stop with arrow-key navigation is the behaviour
 * a keyboard or screen-reader user expects.
 */
export function ThemeSwitch({ id, describedBy }: { id?: string; describedBy?: string }) {
  const { choice, setChoice } = useTheme();
  const groupRef = useRef<HTMLDivElement>(null);

  function move(step: number) {
    const index = OPTIONS.findIndex((entry) => entry.value === choice);
    const next = OPTIONS[(index + step + OPTIONS.length) % OPTIONS.length];
    setChoice(next.value);
    // Roving tabindex: focus has to follow the selection, or it lands on a
    // button that just became tabIndex={-1}.
    groupRef.current?.querySelectorAll<HTMLButtonElement>("button")[
      OPTIONS.indexOf(next)
    ]?.focus();
  }

  return (
    <div
      className="theme-switch"
      role="radiogroup"
      aria-label="Appearance"
      id={id}
      aria-describedby={describedBy}
      ref={groupRef}
    >
      {OPTIONS.map((option) => {
        const active = choice === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            className={`theme-option ${active ? "active" : ""}`}
            onClick={() => setChoice(option.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowRight" || event.key === "ArrowDown") {
                event.preventDefault();
                move(1);
              } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
                event.preventDefault();
                move(-1);
              }
            }}
          >
            <span className="theme-glyph" aria-hidden="true">{option.glyph}</span>
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
