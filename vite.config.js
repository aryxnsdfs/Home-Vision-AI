import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three", "@react-three/fiber", "@react-three/drei", "@react-spring/three"],
          pdf: ["@react-pdf/renderer"],
          motion: ["framer-motion"]
        }
      }
    }
  },
  server: {
    host: "0.0.0.0",          // listen on all interfaces (remote access)
    port: 5173,
    allowedHosts: true,        // accept any Host header (e.g. sanky.space)
  },
  preview: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: true,
  },
});
