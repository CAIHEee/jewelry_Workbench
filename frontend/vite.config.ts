import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const backendTarget = env.VITE_BACKEND_TARGET || "http://127.0.0.1:8000";
  const agentTarget = env.VITE_AGENT_TARGET || "http://127.0.0.1:8010";

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      allowedHosts: [
        "pettiest-commutatively-conception.ngrok-free.dev",
        "revocable-modular-alienate.ngrok-free.dev",
      ],
      proxy: {
        "/api": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/health": {
          target: backendTarget,
          changeOrigin: true,
        },
        "/agent-api": {
          target: agentTarget,
          changeOrigin: true,
        },
        "/agent-health": {
          target: agentTarget,
          changeOrigin: true,
        },
      },
    },
  };
});
