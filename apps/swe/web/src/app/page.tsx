import { redirect } from "next/navigation";

// The v0.1 UI is gone (#69); the app lives at /v2.
export default function Home() {
  redirect("/v2");
}
