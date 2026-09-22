"use client";

import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
import styles from "./page.module.css";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; status: string; db: string }
  | { kind: "error" };

export default function Home() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    getHealth()
      .then((res) => {
        if (!cancelled) {
          setHealth({ kind: "ok", status: res.status, db: res.db });
        }
      })
      .catch(() => {
        if (!cancelled) {
          setHealth({ kind: "error" });
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className={styles.main}>
      <h1>morphloop</h1>
      <p>
        API health:{" "}
        {health.kind === "loading" && "checking…"}
        {health.kind === "ok" && `${health.status} (db: ${health.db})`}
        {health.kind === "error" && "unreachable"}
      </p>
    </main>
  );
}
