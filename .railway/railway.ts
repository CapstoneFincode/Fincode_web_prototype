// Reference only: the live environment was provisioned through Railway MCP.
// Pull the live config and review a plan before using IaC; this is not a full
// snapshot of resource limits, volumes, networking, or the pinned source commit.
// Evaluated by Railway's IaC CLI, not by the Sites/frontend build.
import { defineRailway, github, postgres, project, service } from "railway/iac";

export default defineRailway((ctx) => {
  const db = postgres("Postgres");
  const api = service("fincode-api", {
    source: github("CapstoneFincode/Fincode_web_prototype", { branch: "database" }),
    start: "python -m backend.start",
    healthcheck: "/api/ready",
    healthcheckTimeout: 300,
    replicas: 1,
    env: {
      FINCODE_ENV: "production",
      PORT: "8000",
      DATABASE_URL: db.env.DATABASE_URL,
      BACKEND_SHARED_TOKEN: ctx.shared.BACKEND_SHARED_TOKEN,
      BAZE_API_KEY: ctx.shared.BAZE_API_KEY,
    },
  });
  return project("fincode-team", { resources: [db, api] });
});
