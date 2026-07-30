import { InputHTMLAttributes, useState } from "react";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  /** Display live guidance when a user is choosing or resetting a password. */
  showStrength?: boolean;
};

const passwordRequirements = [
  { label: "15+ characters (required)", test: (value: string) => value.length >= 15 },
  { label: "20+ characters (recommended)", test: (value: string) => value.length >= 20 },
  { label: "30+ characters (strong)", test: (value: string) => value.length >= 30 },
] as const;

function EyeIcon({ hidden }: { hidden: boolean }) {
  return hidden ? (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m3 3 18 18M10.6 10.7a2 2 0 0 0 2.7 2.7M9.9 4.2A10.7 10.7 0 0 1 12 4c5 0 8.3 5.1 9 7.5a11.8 11.8 0 0 1-2.1 3.7M6.1 6.1C4.2 7.5 3.3 9.7 3 11.5 3.7 13.9 7 19 12 19c1.2 0 2.3-.3 3.3-.8" /></svg>
  ) : (
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12s3.2-7 9-7 9 7 9 7-3.2 7-9 7-9-7-9-7Z" /><circle cx="12" cy="12" r="3" /></svg>
  );
}

/** Password visibility and passphrase-length guidance without changing server policy. */
export function PasswordInput({ showStrength = false, ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  const value = typeof props.value === "string" ? props.value : "";
  const strength = value ? Math.min(100, Math.round((value.length / 30) * 100)) : 0;
  const strengthLabel = value.length < 15 ? "Too short" : value.length < 20 ? "Good" : value.length < 30 ? "Strong" : "Excellent";
  const label = visible ? "Hide password" : "Show password";

  return <div className="password-input">
    <div className="password-control">
      <input {...props} type={visible ? "text" : "password"} />
      <button
        className="password-toggle"
        type="button"
        aria-label={label}
        aria-pressed={visible}
        onClick={() => setVisible((current) => !current)}
      >
        <EyeIcon hidden={visible} />
        <span>{visible ? "Hide" : "Show"}</span>
      </button>
    </div>
    {showStrength && value && <div className="password-guidance" aria-live="polite">
      <div className="password-meter" aria-label={`Password strength: ${strengthLabel}`}><span style={{ width: `${strength}%` }} /></div>
      <p>Strength: <b>{strengthLabel}</b></p>
      <div className="password-requirements">
        {passwordRequirements.map(({ label: requirement, test }) => <div key={requirement} className={test(value) ? "met" : ""}>
          <span aria-hidden="true">{test(value) ? "✓" : "○"}</span>{requirement}
        </div>)}
      </div>
    </div>}
  </div>;
}
