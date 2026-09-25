import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "morphloop",
  description: "morphloop adaptive interactive learning OS",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
