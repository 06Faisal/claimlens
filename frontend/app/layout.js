import "./globals.css";

export const metadata = { title: "ClaimLens", description: "Grounded health insurance claim checker" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
