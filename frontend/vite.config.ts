import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  // Amplify UI and the Amplify auth library make one large bundle; acceptable for an internal dashboard.
  build: { chunkSizeWarningLimit: 1000 },
});
