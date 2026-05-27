import type { ReactNode } from "react";

type Tone = "ok" | "warn" | "bad" | "idle";

interface Props {
  tone?: Tone;
  children: ReactNode;
  title?: string;
}

export default function Pill({ tone = "idle", children, title }: Props) {
  return (
    <span className={`pill pill-${tone}`} title={title}>
      {children}
    </span>
  );
}
